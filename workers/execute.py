"""The executor: the only code that changes the work graph or external systems on the agent's behalf (rule 4).

Guarantees:
- runs only `approved`, unexpired proposals, re-checking permission and current state at execution time
- effective permission = min(agent rank, approver rank) must meet the action's requirement
- exactly once: a row lock + status transition claims the proposal; event idempotency keys make tier-2 retries
  harmless; tier-3 posts carry a marker that is looked for before posting, and an interrupted tier-3 run is
  never retried automatically
- every outcome writes an audit_log row; tier 2 stores the inverse so a human can undo it"""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from connectors.github import GitHubConnector, parse_item_key
from core.actions import (
    COMMENT_MARKER,
    REQUIRED_RANK,
    ROLE_RANK,
    CommentArgs,
    CreateItemArgs,
    MoveDueArgs,
    NotifyArgs,
    ReassignArgs,
    SplitArgs,
    check_against_state,
    comment_text,
    human_members,
    linked_pr,
    new_due_at,
    parse_args,
)
from core.config import get_settings
from core.db import tenant_tx
from core.errors import NotConfigured
from core.events import NewEvent, append_events
from core.mailer import send_email
from core.proposals import ProposalNotFound, agent_user_id, audit, can_decide_proposal
from core.security import Principal, Role
from rules.engine import evaluate_tenant, tenant_now
from workers.projector import project_tenant

log = logging.getLogger(__name__)

STUCK_AFTER_MINUTES = 5


@dataclass(frozen=True)
class Outcome:
    proposal_id: UUID
    status: str
    detail: str = ""


class UndoRefused(Exception):
    pass


def _event(
    proposal_id: UUID,
    n: int,
    kind: str,
    subject: str,
    payload: dict[str, Any],
    actor: str,
    prefix: str = "exec",
    *,
    at: datetime,
) -> NewEvent:
    return NewEvent(
        source="daelys",
        type=kind,
        idempotency_key=f"{prefix}:{proposal_id}:{n}",
        occurred_at=at,
        subject_key=subject,
        actor=actor,
        payload=payload,
    )


async def _run_meta(session: AsyncSession, run_id: UUID | None) -> tuple[str | None, str | None]:
    if run_id is None:
        return None, None
    row = (
        await session.execute(
            text("SELECT model, prompt_version FROM agent_runs WHERE id = :id"), {"id": run_id}
        )
    ).first()
    return (row.model, row.prompt_version) if row else (None, None)


async def _approver_rank(session: AsyncSession, proposal: Any) -> int:
    if proposal.approval_source == "policy":
        return ROLE_RANK[Role.agent]  # auto-approval grants nothing beyond the agent's own rank
    role = await session.scalar(
        text("SELECT role FROM memberships WHERE user_id = :u"), {"u": proposal.approved_by}
    )
    return ROLE_RANK[Role(role)] if role and role != "agent" else -1


async def _finish(
    session: AsyncSession,
    proposal: Any,
    status: str,
    detail: str,
    *,
    actor: UUID | None = None,
    action: str = "execution.refused",
) -> Outcome:
    await session.execute(
        text("""
            UPDATE proposals SET status = :s, execution_result = CAST(:r AS jsonb),
                updated_at = now() WHERE id = :id
        """),
        {"s": status, "r": _json({"detail": detail}), "id": proposal.id},
    )
    await audit(
        session,
        actor_type="system",
        actor_id=actor,
        action=action,
        target_type="proposal",
        target_id=str(proposal.id),
        after={"status": status, "detail": detail},
        proposal_id=proposal.id,
        tier=proposal.tier,
    )
    return Outcome(proposal.id, status, detail)


def _external_ready(action_type: str) -> None:
    settings = get_settings()
    if action_type == "comment_on_pr" and not (
        settings.github_app_id and settings.github_app_private_key_path
    ):
        raise NotConfigured("GitHub App credentials are not set (GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY_PATH)")
    if action_type == "notify" and not settings.smtp_host:
        raise NotConfigured("SMTP_HOST is not set")


async def execute(tenant_id: UUID, proposal_id: UUID) -> Outcome:
    """Run one approved proposal. Safe to call repeatedly and concurrently."""
    async with tenant_tx(tenant_id) as s:
        p = (
            await s.execute(text("SELECT * FROM proposals WHERE id = :id FOR UPDATE"), {"id": proposal_id})
        ).first()
        if p is None:
            raise ProposalNotFound  # includes other tenants' ids (RLS)
        if p.status in ("executed", "executing", "undone"):
            return Outcome(p.id, p.status, "already handled")
        if p.status != "approved":
            # Refused without touching the proposal; the attempt itself is audited.
            await audit(
                s,
                actor_type="system",
                actor_id=None,
                action="execution.refused",
                target_type="proposal",
                target_id=str(p.id),
                after={"reason": f"status is {p.status}, not approved"},
                proposal_id=p.id,
                tier=p.tier,
            )
            return Outcome(p.id, p.status, f"refused: proposal is {p.status}, not approved")
        if p.expires_at <= datetime.now(UTC):
            return await _finish(s, p, "expired", "proposal expired before execution")
        agent = await agent_user_id(s)
        effective = min(ROLE_RANK[Role.agent], await _approver_rank(s, p))
        if effective < REQUIRED_RANK[p.action_type]:
            return await _finish(s, p, "failed", "effective permission min(agent, approver) is too low")
        creates = p.action_type == "create_item"
        item = None
        if not creates:
            item = (
                await s.execute(
                    text("SELECT * FROM work_items WHERE id = :id FOR UPDATE"), {"id": p.work_item_id}
                )
            ).first()
            if item is None:
                return await _finish(s, p, "failed", "item no longer exists")
        args = parse_args(p.action_type, p.args)
        now = await tenant_now(s)
        if problems := await check_against_state(s, p.action_type, args, item, now):
            return await _finish(s, p, "failed", "; ".join(problems))
        model, prompt_version = await _run_meta(s, p.agent_run_id)
        actor = f"agent:{agent}" + (f" for {p.approved_by}" if p.approved_by else "")
        if p.tier == 2:
            before, after, inverse, events = await _internal_effect(p, item, args, actor, now)
            await append_events(s, tenant_id, events)
            await s.execute(
                text("""
                    UPDATE proposals SET status = 'executed', executed_at = now(),
                        inverse = CAST(:inv AS jsonb),
                        execution_result = CAST(:res AS jsonb), updated_at = now() WHERE id = :id
                """),
                {"inv": _json(inverse), "res": _json({"events": len(events)}), "id": p.id},
            )
            await audit(
                s,
                actor_type="agent",
                actor_id=agent,
                on_behalf_of=p.approved_by,
                action=f"execute.{p.action_type}",
                target_type="work_item",
                target_id=str(item.id) if item else after["key"],
                before=before,
                after=after,
                proposal_id=p.id,
                model=model,
                prompt_version=prompt_version,
                tier=p.tier,
            )
        else:
            _external_ready(p.action_type)  # NotConfigured leaves it approved, ready once configured
            await s.execute(
                text("UPDATE proposals SET status = 'executing', updated_at = now() WHERE id = :id"),
                {"id": p.id},
            )
    if p.tier == 2:
        await _refresh(tenant_id)
        return Outcome(p.id, "executed")
    return await _external(tenant_id, p, item, args, actor, agent, model, prompt_version, now)


def _json(value: Any) -> str:
    return json.dumps(value, default=str)


async def _internal_effect(
    p: Any, item: Any, args: Any, actor: str, now: datetime
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[NewEvent]]:
    before: dict[str, Any]
    after: dict[str, Any]
    if isinstance(args, CreateItemArgs):
        key = f"meeting-item:{p.id}"
        fields: dict[str, Any] = {"kind": "task", "title": args.title, "status": "open"}
        if args.lane:
            fields["lane"] = args.lane
        if args.owner_user_id:
            fields["owner_id"] = str(args.owner_user_id)
        if args.project_id:
            fields["project_id"] = str(args.project_id)
        if args.due_date:
            fields["due_at"] = new_due_at(None, args.due_date).isoformat()
        event = _event(
            p.id,
            0,
            "action.item_created",
            key,
            {"fields": fields, "proposal_id": str(p.id), "meeting_id": str(p.meeting_id)},
            actor,
            at=now,
        )
        return {}, {"key": key, **fields}, {"cancel": [key]}, [event]
    if isinstance(args, ReassignArgs):
        before = {"owner_id": item.owner_id, "owner_login": item.owner_login}
        after = {"owner_id": args.to_user_id}
        fields = {"owner_id": str(args.to_user_id)}
        inverse = {
            "fields": {
                "owner_id": str(item.owner_id) if item.owner_id else None,
                "owner_login": item.owner_login,
            }
        }
        return (
            before,
            after,
            inverse,
            [
                _event(
                    p.id,
                    0,
                    "action.reassigned",
                    item.key,
                    {"fields": fields, "proposal_id": str(p.id)},
                    actor,
                    at=now,
                )
            ],
        )
    if isinstance(args, MoveDueArgs):
        due = new_due_at(item.due_at, args.new_due_date)
        before, after = {"due_at": item.due_at}, {"due_at": due}
        inverse = {"fields": {"due_at": item.due_at.isoformat() if item.due_at else None}}
        return (
            before,
            after,
            inverse,
            [
                _event(
                    p.id,
                    0,
                    "action.due_moved",
                    item.key,
                    {"fields": {"due_at": due.isoformat()}, "proposal_id": str(p.id)},
                    actor,
                    at=now,
                )
            ],
        )
    assert isinstance(args, SplitArgs)
    children = [f"split:{p.id}:{n}" for n in range(len(args.parts))]
    events = []
    for n, (key, part) in enumerate(zip(children, args.parts, strict=True)):
        child: dict[str, Any] = {
            "kind": "task",
            "title": part.title,
            "status": "open",
            "estimate_days": part.estimate_days,
        }
        for name in ("lane", "owner_login"):
            if getattr(item, name):
                child[name] = getattr(item, name)
        if item.project_id:
            child["project_id"] = str(item.project_id)
        if item.due_at:
            child["due_at"] = item.due_at.isoformat()
        events.append(
            _event(p.id, n, "action.split_created", key, {"fields": child, "parent": item.key}, actor, at=now)
        )
    # The original becomes an umbrella that finishes when its parts do.
    events.append(
        _event(
            p.id,
            len(children),
            "action.split",
            item.key,
            {"depends_on": children, "proposal_id": str(p.id)},
            actor,
            at=now,
        )
    )
    return ({"title": item.title}, {"parts": [c for c in children]}, {"cancel": children}, events)


async def _external(
    tenant_id: UUID,
    p: Any,
    item: Any,
    args: Any,
    actor: str,
    agent: UUID | None,
    model: str | None,
    prompt_version: str | None,
    now: datetime,
) -> Outcome:
    try:
        if isinstance(args, CommentArgs):
            async with tenant_tx(tenant_id) as s:
                pr = await linked_pr(s, item)
            repo, number = parse_item_key(pr.key)
            body = comment_text(p.id, args.body)
            comment_id = await GitHubConnector().post_comment(
                tenant_id, repo, number, body, COMMENT_MARKER.format(id=p.id)
            )
            subject, kind = pr.key, "action.pr_commented"
            payload: dict[str, Any] = {"comment_id": comment_id, "body": body, "url": pr.url}
            after: dict[str, Any] = {"pull_request": pr.key, "comment_id": comment_id, "text": body}
        else:
            assert isinstance(args, NotifyArgs)
            async with tenant_tx(tenant_id) as s:
                users = await human_members(s, args.to_user_ids)
            await send_email(
                [u.email for u in users.values()], args.subject, args.body, f"<proposal-{p.id}@daelys.local>"
            )
            subject, kind = item.key, "action.notified"
            payload = {"to": [str(u) for u in users], "subject": args.subject}
            after = {"to": [u.email for u in users.values()], "subject": args.subject, "body": args.body}
    except Exception as exc:  # any failure of the external call: recorded as failed, never retried blindly
        log.exception("execute.external_failed", extra={"proposal": str(p.id)})
        async with tenant_tx(tenant_id) as s:
            locked = (
                await s.execute(text("SELECT * FROM proposals WHERE id = :id FOR UPDATE"), {"id": p.id})
            ).one()
            return await _finish(
                s, locked, "failed", f"external call failed: {type(exc).__name__}", action="execution.failed"
            )
    async with tenant_tx(tenant_id) as s:
        payload["proposal_id"] = str(p.id)
        await append_events(s, tenant_id, [_event(p.id, 0, kind, subject, payload, actor, at=now)])
        await s.execute(
            text("""
                UPDATE proposals SET status = 'executed', executed_at = now(),
                    execution_result = CAST(:r AS jsonb),
                    updated_at = now() WHERE id = :id
            """),
            {"r": _json(after), "id": p.id},
        )
        await audit(
            s,
            actor_type="agent",
            actor_id=agent,
            on_behalf_of=p.approved_by,
            action=f"execute.{p.action_type}",
            target_type="work_item",
            target_id=str(item.id),
            after=after,
            proposal_id=p.id,
            model=model,
            prompt_version=prompt_version,
            tier=p.tier,
        )
    await _refresh(tenant_id)
    return Outcome(p.id, "executed")


async def undo(tenant_id: UUID, proposal_id: UUID, principal: Principal) -> Outcome:
    """One-click undo of an executed tier-2 action, by a human who may decide on that item."""
    async with tenant_tx(tenant_id) as s:
        p = (
            await s.execute(text("SELECT * FROM proposals WHERE id = :id FOR UPDATE"), {"id": proposal_id})
        ).first()
        if p is None:
            raise ProposalNotFound
        if not await can_decide_proposal(s, principal, p):
            raise PermissionError("only an owner or a lead of this item's project can undo")
        if p.status != "executed" or p.tier != 2 or p.inverse is None:
            raise UndoRefused(
                f"only executed tier-2 actions can be undone (this one is {p.status}, tier {p.tier})"
            )
        target = str(p.work_item_id) if p.work_item_id else p.inverse["cancel"][0]
        item = None
        if p.work_item_id is not None:
            item = (
                await s.execute(text("SELECT * FROM work_items WHERE id = :id"), {"id": p.work_item_id})
            ).one()
        actor = f"human:{principal.user_id}"
        now = await tenant_now(s)
        if "cancel" in p.inverse:
            events = [
                _event(
                    p.id,
                    n,
                    "action.split_undone",
                    key,
                    {"fields": {"status": "cancelled"}},
                    actor,
                    "undo",
                    at=now,
                )
                for n, key in enumerate(p.inverse["cancel"])
            ]
        else:
            events = [
                _event(
                    p.id,
                    0,
                    f"action.{p.action_type}_undone",
                    item.key if item else target,
                    {"fields": p.inverse["fields"], "proposal_id": str(p.id)},
                    actor,
                    "undo",
                    at=now,
                )
            ]
        await append_events(s, tenant_id, events)
        await s.execute(
            text("""
                UPDATE proposals SET status = 'undone', undone_at = now(), undone_by = :u, updated_at = now()
                WHERE id = :id
            """),
            {"u": principal.user_id, "id": p.id},
        )
        await audit(
            s,
            actor_type="human",
            actor_id=principal.user_id,
            action=f"undo.{p.action_type}",
            target_type="work_item",
            target_id=target,
            before=p.execution_result,
            after=p.inverse,
            proposal_id=p.id,
            tier=p.tier,
        )
    await _refresh(tenant_id)
    return Outcome(p.id, "undone")


async def sweep(tenant_id: UUID) -> dict[str, int]:
    """Expire stale proposals; settle tier-3 runs interrupted mid-flight (never re-sent automatically)."""
    expired = recovered = failed = 0
    async with tenant_tx(tenant_id) as s:
        rows = (
            await s.execute(
                text("""
            SELECT * FROM proposals WHERE status IN ('pending', 'approved') AND expires_at <= now() FOR UPDATE
        """)
            )
        ).all()
        for p in rows:
            await _finish(s, p, "expired", "expired before execution", action="proposal.expired")
            expired += 1
        stuck = (
            await s.execute(
                text("""
            SELECT * FROM proposals WHERE status = 'executing'
              AND updated_at < now() - make_interval(mins => :m) FOR UPDATE
        """),
                {"m": STUCK_AFTER_MINUTES},
            )
        ).all()
    for p in stuck:
        posted = None
        if p.action_type == "comment_on_pr":
            async with tenant_tx(tenant_id) as s:
                item = (
                    await s.execute(text("SELECT * FROM work_items WHERE id = :id"), {"id": p.work_item_id})
                ).one()
                pr = await linked_pr(s, item)
            if pr is not None:
                repo, number = parse_item_key(pr.key)
                posted = await GitHubConnector().find_comment(
                    tenant_id, repo, number, COMMENT_MARKER.format(id=p.id)
                )
        async with tenant_tx(tenant_id) as s:
            locked = (
                await s.execute(text("SELECT * FROM proposals WHERE id = :id FOR UPDATE"), {"id": p.id})
            ).one()
            if locked.status != "executing":
                continue
            if posted is not None:
                await s.execute(
                    text("""
                    UPDATE proposals SET status = 'executed', executed_at = now(),
                        execution_result = CAST(:r AS jsonb), updated_at = now() WHERE id = :id
                """),
                    {"r": _json({"comment_id": posted, "recovered": True}), "id": p.id},
                )
                await audit(
                    s,
                    actor_type="system",
                    actor_id=None,
                    action="execution.recovered",
                    target_type="proposal",
                    target_id=str(p.id),
                    after={"comment_id": posted},
                    proposal_id=p.id,
                    tier=p.tier,
                )
                recovered += 1
            else:
                await _finish(
                    s,
                    locked,
                    "failed",
                    "interrupted mid-execution; not retried (check before re-proposing)",
                    action="execution.interrupted",
                )
                failed += 1
    return {"expired": expired, "recovered": recovered, "failed": failed}


async def _refresh(tenant_id: UUID) -> None:
    """Fold the new events into the projection and re-score risk (also notifies live clients)."""
    await project_tenant(tenant_id)
    await evaluate_tenant(tenant_id)
