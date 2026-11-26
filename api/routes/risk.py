"""Risk read APIs, the 'why' panel, explanations, the time machine and the live update stream."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agents.explainer import EvidenceEvent, describe_signal, explain
from api.deps import HUMAN_ROLES, AnyMember, require_principal
from connectors.replay import ReplayConnector
from core import queue
from core.config import get_settings
from core.db import tenant_tx
from core.llm import LLMUnavailable, ProviderError, get_llm
from core.ratelimit import allow
from core.realtime import CLOSED, broadcaster
from core.sandbox import copy_tenant
from core.security import Principal, Role
from rules.engine import evaluate_as_of, health_summary

router = APIRouter(tags=["risk"])
log = logging.getLogger(__name__)

_NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
KEEPALIVE_SECONDS = 15
Member = Annotated[Principal, Depends(require_principal(*HUMAN_ROLES))]
Owner = Annotated[Principal, Depends(require_principal(Role.owner))]

_EVIDENCE_SQL = text("""
    SELECT e.id, e.type, e.occurred_at, e.actor, e.subject_key, e.payload, w.id AS item_id, w.url AS item_url,
           w.title AS item_title
    FROM events e LEFT JOIN work_items w ON w.key = e.subject_key
    WHERE e.id = ANY(CAST(:ids AS bigint[])) ORDER BY e.occurred_at, e.id
""")


@router.get("/risk/health")
async def launch_health(ctx: AnyMember) -> dict[str, Any]:
    return await health_summary(ctx.session)


async def _evidence(session: AsyncSession, ids: list[int]) -> list[dict[str, Any]]:
    rows = await session.execute(_EVIDENCE_SQL, {"ids": ids})
    return [dict(r) for r in rows.mappings()]


@router.get("/work-items/{item_id}/signals")
async def item_signals(item_id: UUID, ctx: AnyMember) -> dict[str, Any]:
    item = (
        (
            await ctx.session.execute(
                text("""
                SELECT id, key, title, status, due_at, risk_score, risk_level, projected_slip_days,
                       risk_evaluated_at FROM work_items WHERE id = :id
            """),
                {"id": item_id},
            )
        )
        .mappings()
        .first()
    )
    if item is None:
        raise _NOT_FOUND
    rows = await ctx.session.execute(
        text("""
            SELECT id, rule, severity, evidence_event_ids, details, computed_at, explanation,
                explanation_evidence
            FROM signals WHERE work_item_id = :id AND resolved_at IS NULL ORDER BY computed_at
        """),
        {"id": item_id},
    )
    signals = []
    for s in rows.mappings():
        fresh = s["explanation"] is not None and s["explanation_evidence"] == s["evidence_event_ids"]
        signals.append(
            {
                "id": s["id"],
                "rule": s["rule"],
                "severity": s["severity"],
                "details": s["details"],
                "computed_at": s["computed_at"],
                "evidence": await _evidence(ctx.session, s["evidence_event_ids"]),
                "explanation": s["explanation"] if fresh else None,
            }
        )
    return {"item": dict(item), "signals": signals}


@router.post("/signals/{signal_id}/explain")
async def explain_signal(signal_id: UUID, principal: Member) -> dict[str, Any]:
    """LLM narrative for one signal; every sentence cites evidence events or is dropped. Cached per
    evidence set."""
    async with tenant_tx(principal.tenant_id) as s:
        row = (
            await s.execute(
                text("""
                    SELECT s.rule, s.severity, s.details, s.evidence_event_ids, s.explanation,
                           s.explanation_evidence, w.title
                    FROM signals s JOIN work_items w ON w.id = s.work_item_id
                    WHERE s.id = :id AND s.resolved_at IS NULL
                """),
                {"id": signal_id},
            )
        ).first()
        if row is None:
            raise _NOT_FOUND
        if row.explanation is not None and row.explanation_evidence == row.evidence_event_ids:
            return dict(row.explanation)
        events = await _evidence(s, row.evidence_event_ids)
    if not await allow(f"llm:explain:{principal.tenant_id}", get_settings().llm_rate_limit_per_minute):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="too many explanation requests"
        )
    llm = get_llm()  # NotConfigured -> 503
    try:
        checked = await explain(
            llm,
            principal.tenant_id,
            describe_signal(row.rule, row.severity, row.details, row.title),
            [
                EvidenceEvent(
                    e["id"],
                    e["type"],
                    e["occurred_at"].isoformat(),
                    e["actor"],
                    e["subject_key"],
                    e["payload"],
                )
                for e in events
            ],
        )
    except (LLMUnavailable, ProviderError) as exc:
        log.error("explain.llm_failed", extra={"signal": str(signal_id), "error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="language model unavailable"
        ) from exc
    result = {
        "sentences": [{"text": c.text, "event_ids": c.event_ids} for c in checked.sentences],
        "dropped": len(checked.dropped),
    }
    async with tenant_tx(principal.tenant_id) as s:
        await s.execute(
            text("""
                UPDATE signals SET explanation = CAST(:e AS jsonb), explanation_evidence = :ev
                WHERE id = :id AND evidence_event_ids = :ev
            """),
            {"e": json.dumps(result), "ev": row.evidence_event_ids, "id": signal_id},
        )
    return result


@router.get("/projects/{project_id}/graph")
async def project_graph(project_id: UUID, ctx: AnyMember) -> dict[str, Any]:
    if await ctx.session.scalar(text("SELECT 1 FROM projects WHERE id = :p"), {"p": project_id}) is None:
        raise _NOT_FOUND
    nodes = await ctx.session.execute(
        text("""
            SELECT id, key, title, kind, lane, status, risk_score, risk_level, projected_slip_days, due_at
            FROM work_items WHERE project_id = :p ORDER BY key
        """),
        {"p": project_id},
    )
    edges = await ctx.session.execute(
        text("""
            SELECT e.src_id, e.dst_id, e.kind FROM edges e
            JOIN work_items s ON s.id = e.src_id JOIN work_items d ON d.id = e.dst_id
            WHERE s.project_id = :p AND d.project_id = :p
        """),
        {"p": project_id},
    )
    return {"nodes": [dict(n) for n in nodes.mappings()], "edges": [dict(e) for e in edges.mappings()]}


@router.get("/time-machine/state")
async def state_as_of(as_of: Annotated[datetime, Query()], principal: Member) -> dict[str, Any]:
    """Health, items and signals exactly as they would have been at `as_of` (computed, never stored)."""
    if as_of.tzinfo is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="as_of needs a UTC offset"
        )
    return await evaluate_as_of(principal.tenant_id, as_of)


class ReplayRequest(BaseModel):
    scenario: str = Field(pattern=r"^[a-z0-9_]{1,64}$")
    timelines: list[str] = Field(min_length=1, max_length=8)
    speed: float = Field(default=0, ge=0, le=100_000)


@router.post("/time-machine/replays", status_code=status.HTTP_202_ACCEPTED)
async def replay_into_copy(body: ReplayRequest, principal: Owner) -> dict[str, Any]:
    """Copy this workspace (people, projects, briefs; no events) and replay a scenario into the copy."""
    if not await allow(f"replay:{principal.tenant_id}", 5, window_seconds=3600):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="too many replays this hour"
        )
    try:
        for timeline in body.timelines:
            ReplayConnector(body.scenario, timeline).load()
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    copy = await copy_tenant(principal.tenant_id)
    pool = await queue.get_pool()
    await pool.enqueue_job("replay_scenario", str(copy.tenant_id), body.scenario, body.timelines, body.speed)
    return {"tenant_slug": copy.slug, "detail": "Sign in to this workspace with the same email and password."}


@router.get("/stream")
async def stream(request: Request, principal: Member) -> StreamingResponse:
    """Server-sent events: `update` whenever this tenant's projection or risk changes. Clients refetch."""

    async def events() -> AsyncIterator[str]:
        async with broadcaster.subscribe(str(principal.tenant_id)) as updates:
            yield "retry: 5000\n\n"
            while not await request.is_disconnected():
                try:
                    message = await asyncio.wait_for(updates.get(), KEEPALIVE_SECONDS)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield "event: update\ndata: {}\n\n"  # also on CLOSED: refetch what may have been missed
                if message == CLOSED:
                    return

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
