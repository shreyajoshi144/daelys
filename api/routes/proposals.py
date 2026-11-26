"""Proposals: generate (agent), list, approve / edit-then-approve, reject, undo; plus the audit log.
Approval only queues execution; the executor (workers/execute.py) is the only thing that acts."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agents.proposer import run_proposer
from api.deps import AnyMember, LeadOrOwner, LeadPrincipal
from api.schemas import ApproveIn, GenerateOut, RejectIn
from core import queue
from core.actions import InvalidAction, preview
from core.config import get_settings
from core.db import tenant_tx
from core.llm import LLMUnavailable, ProviderError, get_llm
from core.proposals import Conflict, NotAllowed, ProposalNotFound, approve, can_decide, reject, store
from core.ratelimit import allow
from workers.execute import UndoRefused, undo

router = APIRouter(tags=["proposals"])

_NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
_LIST_SQL = text("""
    SELECT p.id, p.work_item_id, p.action_type, p.args, p.original_args, p.evidence_event_ids,
        p.expected_effect,
    p.reversible, p.tier, p.status, p.approval_source, p.approved_by, p.decided_at, p.rejection_reason,
    p.expires_at, p.created_at, p.executed_at, p.execution_result, p.undone_at, p.signal_rules,
    coalesce(w.title, p.args->>'title') AS item_title, p.meeting_id, w.key AS item_key,
    w.risk_level AS item_risk_level
    FROM proposals p LEFT JOIN work_items w ON w.id = p.work_item_id
    WHERE (CAST(:status AS text) IS NULL OR p.status = :status)
      AND (CAST(:item AS uuid) IS NULL OR p.work_item_id = :item)
    ORDER BY p.created_at DESC LIMIT :limit
""")
_GET_SQL = text("""
    SELECT p.id, p.work_item_id, p.action_type, p.args, p.original_args, p.evidence_event_ids,
        p.expected_effect,
    p.reversible, p.tier, p.status, p.approval_source, p.approved_by, p.decided_at, p.rejection_reason,
    p.expires_at, p.created_at, p.executed_at, p.execution_result, p.undone_at, p.signal_rules,
    coalesce(w.title, p.args->>'title') AS item_title, p.meeting_id, w.key AS item_key,
    w.risk_level AS item_risk_level
    FROM proposals p LEFT JOIN work_items w ON w.id = p.work_item_id WHERE p.id = :id
""")


async def _enqueue(tenant_id: UUID, proposal_id: UUID) -> None:
    pool = await queue.get_pool()
    # Fixed job id: a second approve (or retry) cannot queue a second execution.
    await pool.enqueue_job(
        "execute_proposal", str(tenant_id), str(proposal_id), _job_id=f"execute:{proposal_id}"
    )


async def _with_preview(session: AsyncSession, row: Any) -> dict[str, Any]:
    item = None
    if row.work_item_id is not None:
        item = (
            await session.execute(text("SELECT * FROM work_items WHERE id = :id"), {"id": row.work_item_id})
        ).one()
    evidence = await session.execute(
        text(
            "SELECT id, type, occurred_at, subject_key FROM events WHERE id = ANY(:ids) ORDER BY occurred_at"
        ),
        {"ids": row.evidence_event_ids},
    )
    data = dict(row._mapping)
    data["preview"] = await preview(session, row, item)
    data["evidence"] = [dict(e) for e in evidence.mappings()]
    return data


@router.post("/work-items/{item_id}/proposals/generate", response_model=GenerateOut)
async def generate(item_id: UUID, principal: LeadPrincipal) -> GenerateOut:
    async with tenant_tx(principal.tenant_id) as s:
        if await s.scalar(text("SELECT 1 FROM work_items WHERE id = :id"), {"id": item_id}) is None:
            raise _NOT_FOUND
        if not await can_decide(s, principal, item_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="not a lead of this item's project"
            )
    if not await allow(f"llm:propose:{principal.tenant_id}", get_settings().llm_rate_limit_per_minute):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="too many proposal requests"
        )
    llm = get_llm()  # NotConfigured -> 503
    try:
        result = await run_proposer(llm, principal.tenant_id, item_id)
    except (LLMUnavailable, ProviderError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="language model unavailable"
        ) from exc
    async with tenant_tx(principal.tenant_id) as s:
        created = await store(s, principal.tenant_id, result)
    for proposal_id in created.auto_approved:
        await _enqueue(principal.tenant_id, proposal_id)
    return GenerateOut(
        created=created.ids,
        auto_approved=created.auto_approved,
        rejected=[r.model_dump() for r in result.rejected],
    )


@router.get("/proposals")
async def list_proposals(
    ctx: AnyMember,
    status_filter: Annotated[str | None, Query(alias="status", max_length=20)] = None,
    work_item_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[dict[str, Any]]:
    rows = await ctx.session.execute(
        _LIST_SQL, {"status": status_filter, "item": work_item_id, "limit": limit}
    )
    return [await _with_preview(ctx.session, r) for r in rows.all()]


@router.get("/proposals/{proposal_id}")
async def get_proposal(proposal_id: UUID, ctx: AnyMember) -> dict[str, Any]:
    row = (await ctx.session.execute(_GET_SQL, {"id": proposal_id})).first()
    if row is None:
        raise _NOT_FOUND
    return await _with_preview(ctx.session, row)


@router.post("/proposals/{proposal_id}/approve", status_code=status.HTTP_202_ACCEPTED)
async def approve_proposal(
    proposal_id: UUID, principal: LeadPrincipal, body: ApproveIn | None = None
) -> dict[str, str]:
    try:
        async with tenant_tx(principal.tenant_id) as s:
            await approve(s, principal, proposal_id, body.args if body else None)
    except ProposalNotFound:
        raise _NOT_FOUND from None
    except NotAllowed as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from None
    except Conflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except InvalidAction as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    await _enqueue(principal.tenant_id, proposal_id)
    return {"status": "approved", "detail": "queued for execution"}


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(proposal_id: UUID, body: RejectIn, principal: LeadPrincipal) -> dict[str, str]:
    try:
        async with tenant_tx(principal.tenant_id) as s:
            await reject(s, principal, proposal_id, body.reason.strip())
    except ProposalNotFound:
        raise _NOT_FOUND from None
    except NotAllowed as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from None
    except Conflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    return {"status": "rejected"}


@router.post("/proposals/{proposal_id}/undo")
async def undo_proposal(proposal_id: UUID, principal: LeadPrincipal) -> dict[str, str]:
    try:
        outcome = await undo(principal.tenant_id, proposal_id, principal)
    except ProposalNotFound:
        raise _NOT_FOUND from None
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from None
    except UndoRefused as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    return {"status": outcome.status}


@router.get("/audit")
async def audit_log(
    ctx: LeadOrOwner,
    actor_type: Annotated[str | None, Query(pattern="^(human|agent|system)$")] = None,
    tier: Annotated[int | None, Query(ge=0, le=3)] = None,
    before_id: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict[str, Any]:
    rows = await ctx.session.execute(
        text("""
            SELECT a.id, a.created_at, a.actor_type, a.action, a.target_type, a.target_id, a.before, a.after,
                   a.proposal_id, a.model, a.prompt_version, a.tier,
                   actor.display_name AS actor_name, obo.display_name AS on_behalf_of_name
            FROM audit_log a
            LEFT JOIN users actor ON actor.id = a.actor_id
            LEFT JOIN users obo ON obo.id = a.on_behalf_of
            WHERE (CAST(:actor AS text) IS NULL OR a.actor_type = :actor)
              AND (CAST(:tier AS smallint) IS NULL OR a.tier = :tier)
              AND (CAST(:before AS bigint) IS NULL OR a.id < :before)
            ORDER BY a.id DESC LIMIT :limit
        """),
        {"actor": actor_type, "tier": tier, "before": before_id, "limit": limit},
    )
    items = [dict(r) for r in rows.mappings()]
    return {"items": items, "next_before_id": items[-1]["id"] if len(items) == limit else None}
