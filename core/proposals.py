"""Proposal lifecycle: store validated drafts, decide (approve / edit-then-approve / reject), audit every
step. Execution lives in workers/execute.py, the only writer of the work graph and external systems."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agents.proposer import ProposerResult
from core.actions import InvalidAction, check_against_state, parse_args
from core.security import Principal, Role
from rules.engine import tenant_now


class ProposalNotFound(Exception):
    pass


class NotAllowed(Exception):
    pass


class Conflict(Exception):
    pass


@dataclass(frozen=True)
class Created:
    ids: list[UUID]
    auto_approved: list[UUID]


async def agent_user_id(session: AsyncSession) -> UUID | None:
    value = await session.scalar(text("SELECT user_id FROM memberships WHERE role = 'agent' LIMIT 1"))
    return UUID(str(value)) if value else None


async def audit(
    session: AsyncSession,
    *,
    actor_type: str,
    actor_id: UUID | None,
    action: str,
    target_type: str,
    target_id: str,
    on_behalf_of: UUID | None = None,
    before: Any = None,
    after: Any = None,
    proposal_id: UUID | None = None,
    model: str | None = None,
    prompt_version: str | None = None,
    tier: int | None = None,
) -> None:
    await session.execute(
        text("""
            INSERT INTO audit_log (tenant_id, actor_type, actor_id, on_behalf_of, action, target_type,
                target_id,
                before, after, proposal_id, model, prompt_version, tier)
            VALUES (NULLIF(current_setting('app.tenant_id', true), '')::uuid, :at, :aid, :obo, :action,
                :tt, :tid,
                CAST(:before AS jsonb), CAST(:after AS jsonb), :pid, :model, :pv, :tier)
        """),
        {
            "at": actor_type,
            "aid": actor_id,
            "obo": on_behalf_of,
            "action": action,
            "tt": target_type,
            "tid": target_id,
            "before": json.dumps(before, default=str) if before is not None else None,
            "after": json.dumps(after, default=str) if after is not None else None,
            "pid": proposal_id,
            "model": model,
            "pv": prompt_version,
            "tier": tier,
        },
    )


async def can_decide_proposal(session: AsyncSession, principal: Principal, row: Any) -> bool:
    """Item proposals: by the item's project. `create_item` proposals: by the project named in the args."""
    if row.work_item_id is not None:
        return await can_decide(session, principal, row.work_item_id)
    if principal.role == Role.owner:
        return True
    project = row.args.get("project_id")
    if principal.role != Role.lead or not project:
        return False
    return bool(
        await session.scalar(
            text("SELECT 1 FROM project_members WHERE project_id = :p AND user_id = :u AND role = 'lead'"),
            {"p": project, "u": principal.user_id},
        )
    )


async def can_decide(session: AsyncSession, principal: Principal, work_item_id: UUID) -> bool:
    """Owners decide anything; a lead decides only for projects they lead."""
    if principal.role == Role.owner:
        return True
    if principal.role != Role.lead:
        return False
    return bool(
        await session.scalar(
            text("""
                SELECT 1 FROM work_items w JOIN project_members pm ON pm.project_id = w.project_id
                WHERE w.id = :item AND pm.user_id = :u AND pm.role = 'lead'
            """),
            {"item": work_item_id, "u": principal.user_id},
        )
    )


async def store(session: AsyncSession, tenant_id: UUID, result: ProposerResult) -> Created:
    policy = (
        await session.execute(text("SELECT auto_approve_max_tier, proposal_ttl_hours FROM policies"))
    ).first()
    auto_max, ttl = (policy.auto_approve_max_tier, policy.proposal_ttl_hours) if policy else (0, 48)
    agent = await agent_user_id(session)
    rules = list(
        await session.scalars(
            text("SELECT rule FROM signals WHERE work_item_id = :i AND resolved_at IS NULL"),
            {"i": result.item_id},
        )
    )
    ids, auto = [], []
    for draft in result.drafts:
        proposal_id = uuid4()
        # Tier 3 is never auto-approved, whatever the policy says (the column can't hold 3 either).
        auto_ok = draft.tier != 3 and draft.tier <= auto_max
        await session.execute(
            text("""
                INSERT INTO proposals (id, tenant_id, work_item_id, agent_run_id, action_type, args,
                    evidence_event_ids, expected_effect, reversible, tier, status, idempotency_key,
                        expires_at,
                    signal_rules, approval_source, decided_at)
                VALUES (:id, :t, :item, :run, :action, CAST(:args AS jsonb), :ev, :effect, :rev, :tier,
                    :status,
                    :key, :expires, :rules, :source, :decided)
            """),
            {
                "id": proposal_id,
                "t": tenant_id,
                "item": result.item_id,
                "run": result.agent_run_id,
                "action": draft.action_type,
                "args": json.dumps(draft.args),
                "ev": draft.evidence_event_ids,
                "effect": draft.expected_effect,
                "rev": draft.reversible,
                "tier": draft.tier,
                "status": "approved" if auto_ok else "pending",
                "key": f"proposal:{proposal_id}",
                "expires": datetime.now(UTC) + timedelta(hours=ttl),
                "rules": rules,
                "source": "policy" if auto_ok else None,
                "decided": datetime.now(UTC) if auto_ok else None,
            },
        )
        await audit(
            session,
            actor_type="agent",
            actor_id=agent,
            action="proposal.created",
            target_type="proposal",
            target_id=str(proposal_id),
            after={"action_type": draft.action_type, "args": draft.args},
            proposal_id=proposal_id,
            model=result.model,
            prompt_version=result.prompt_version,
            tier=draft.tier,
        )
        if auto_ok:
            await audit(
                session,
                actor_type="system",
                actor_id=None,
                action="proposal.auto_approved",
                target_type="proposal",
                target_id=str(proposal_id),
                proposal_id=proposal_id,
                tier=draft.tier,
                after={"policy_max_tier": auto_max},
            )
            auto.append(proposal_id)
        ids.append(proposal_id)
    for r in result.rejected:
        await audit(
            session,
            actor_type="agent",
            actor_id=agent,
            action="proposal.rejected_by_validator",
            target_type="work_item",
            target_id=str(result.item_id),
            after=r.model_dump(),
            model=result.model,
            prompt_version=result.prompt_version,
        )
    return Created(ids, auto)


async def _locked(session: AsyncSession, proposal_id: UUID) -> Any:
    row = (
        await session.execute(text("SELECT * FROM proposals WHERE id = :id FOR UPDATE"), {"id": proposal_id})
    ).first()
    if row is None:
        raise ProposalNotFound
    return row


async def approve(
    session: AsyncSession, principal: Principal, proposal_id: UUID, edited_args: dict[str, Any] | None = None
) -> None:
    """pending -> approved, exactly once (row lock + status check), optionally with edited arguments."""
    row = await _locked(session, proposal_id)
    if not await can_decide_proposal(session, principal, row):
        raise NotAllowed("only an owner or a lead of this item's project can decide")
    if row.status != "pending":
        raise Conflict(f"proposal is {row.status}")
    if row.expires_at <= datetime.now(UTC):
        await session.execute(
            text("UPDATE proposals SET status = 'expired', updated_at = now() WHERE id = :id"),
            {"id": proposal_id},
        )
        await audit(
            session,
            actor_type="system",
            actor_id=None,
            action="proposal.expired",
            target_type="proposal",
            target_id=str(proposal_id),
            proposal_id=proposal_id,
            tier=row.tier,
        )
        raise Conflict("proposal has expired")
    args = row.args
    if edited_args is not None:
        parsed = parse_args(row.action_type, edited_args)
        item = None
        if row.work_item_id is not None:
            item = (
                await session.execute(
                    text("SELECT * FROM work_items WHERE id = :id"), {"id": row.work_item_id}
                )
            ).one()
        if problems := await check_against_state(
            session, row.action_type, parsed, item, await tenant_now(session)
        ):
            raise InvalidAction("; ".join(problems))
        args = parsed.model_dump(mode="json")
    await session.execute(
        text("""
            UPDATE proposals SET status = 'approved', approved_by = :u, decided_at = now(),
                approval_source = 'human',
                original_args = CASE WHEN :edited THEN args ELSE NULL END, args = CAST(:args AS jsonb),
                updated_at = now()
            WHERE id = :id
        """),
        {
            "u": principal.user_id,
            "edited": edited_args is not None,
            "args": json.dumps(args),
            "id": proposal_id,
        },
    )
    await audit(
        session,
        actor_type="human",
        actor_id=principal.user_id,
        action="proposal.edited_and_approved" if edited_args is not None else "proposal.approved",
        target_type="proposal",
        target_id=str(proposal_id),
        before={"args": row.args},
        after={"args": args},
        proposal_id=proposal_id,
        tier=row.tier,
    )


async def reject(session: AsyncSession, principal: Principal, proposal_id: UUID, reason: str) -> None:
    row = await _locked(session, proposal_id)
    if not await can_decide_proposal(session, principal, row):
        raise NotAllowed("only an owner or a lead of this item's project can decide")
    if row.status != "pending":
        raise Conflict(f"proposal is {row.status}")
    await session.execute(
        text("""
            UPDATE proposals SET status = 'rejected', rejection_reason = :r, approved_by = :u,
                decided_at = now(),
                updated_at = now() WHERE id = :id
        """),
        {"r": reason, "u": principal.user_id, "id": proposal_id},
    )
    await audit(
        session,
        actor_type="human",
        actor_id=principal.user_id,
        action="proposal.rejected",
        target_type="proposal",
        target_id=str(proposal_id),
        after={"reason": reason},
        proposal_id=proposal_id,
        tier=row.tier,
    )


async def store_create_item(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    meeting_id: UUID,
    args: dict[str, Any],
    evidence_event_ids: list[int],
    expected_effect: str,
    agent_run_id: UUID | None,
    model: str | None,
    prompt_version: str,
) -> UUID:
    """An unmatched meeting action item becomes a pending `create_item` proposal, never a silent item."""
    ttl = await session.scalar(text("SELECT proposal_ttl_hours FROM policies")) or 48
    proposal_id = uuid4()
    await session.execute(
        text("""
            INSERT INTO proposals (id, tenant_id, work_item_id, meeting_id, agent_run_id, action_type, args,
                evidence_event_ids, expected_effect, reversible, tier, status, idempotency_key, expires_at)
            VALUES (:id, :t, NULL, :m, :run, 'create_item', CAST(:args AS jsonb), :ev, :effect, true, 2,
                'pending', :key, :expires)
        """),
        {
            "id": proposal_id,
            "t": tenant_id,
            "m": meeting_id,
            "run": agent_run_id,
            "args": json.dumps(args),
            "ev": evidence_event_ids,
            "effect": expected_effect,
            "key": f"proposal:{proposal_id}",
            "expires": datetime.now(UTC) + timedelta(hours=ttl),
        },
    )
    await audit(
        session,
        actor_type="agent",
        actor_id=await agent_user_id(session),
        action="proposal.created",
        target_type="proposal",
        target_id=str(proposal_id),
        after={"action_type": "create_item", "args": args},
        proposal_id=proposal_id,
        model=model,
        prompt_version=prompt_version,
        tier=2,
    )
    return proposal_id
