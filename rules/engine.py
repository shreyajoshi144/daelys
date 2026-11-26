"""Risk evaluation: rules + propagation -> signals, risk_score, risk_level.

Deterministic and idempotent: evaluating twice at the same `as_of` leaves the same rows. Writes only derived
columns (never projected fields), so it never competes with the event log (design rules 1 and 2)."""

import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_sessionmaker, set_tenant, tenant_tx
from rules.checks import RULES, Policy, SignalDraft
from rules.scoring import SEVERITY_POINTS, combine, health, level, slip_points

log = logging.getLogger(__name__)

NOTIFY_CHANNEL = "daelys_updates"


@cache
def propagation_sql() -> str:
    return (Path(__file__).with_name("propagation.sql")).read_text()


@dataclass(frozen=True)
class Slip:
    days: float
    roots: list[UUID]
    downstream_impact: int


@dataclass(frozen=True)
class Evaluation:
    as_of: datetime
    signals: list[SignalDraft]
    scores: dict[UUID, int]
    slips: dict[UUID, Slip]


async def load_policy(session: AsyncSession) -> Policy:
    row = (
        await session.execute(
            text(
                "SELECT stale_pr_days, due_window_days, overload_limit, blocker_window_days, clock "
                "FROM policies"
            )
        )
    ).first()
    return Policy(*row) if row else Policy()


async def resolve_as_of(session: AsyncSession, policy: Policy) -> datetime:
    """'wall' = now; 'event' = time of the latest observed-work event (replayed scenarios).

    Meetings carry a date people type in, so they never move the clock: a meeting dated "today" in a
    replayed March scenario would otherwise age every task by months. Plan and executor events are
    stamped at this clock."""
    if policy.clock == "event":
        latest = await session.scalar(text("SELECT max(occurred_at) FROM events WHERE source <> 'meeting'"))
        if latest is not None:
            return latest
    return datetime.now(UTC)


async def tenant_now(session: AsyncSession) -> datetime:
    """'Now' on this tenant's clock: wall time, or the latest event time for replayed scenarios. Anything that
    acts or validates dates (executor, proposer) must use this, or a scenario's clock would jump."""
    return await resolve_as_of(session, await load_policy(session))


async def propagate(session: AsyncSession, as_of: datetime) -> dict[UUID, Slip]:
    rows = await session.execute(text(propagation_sql()), {"as_of": as_of})
    return {
        r.item_id: Slip(float(r.projected_slip_days), list(r.root_ids), int(r.downstream_impact or 0))
        for r in rows
    }


def score(signals: list[SignalDraft], slips: dict[UUID, Slip]) -> dict[UUID, int]:
    points: dict[UUID, list[int]] = defaultdict(list)
    for s in signals:
        points[s.work_item_id].append(SEVERITY_POINTS[s.severity])
    for item_id, slip in slips.items():
        points[item_id].append(slip_points(slip.days))
    return {item_id: combine(p) for item_id, p in points.items()}


async def evaluate(session: AsyncSession, as_of: datetime | None = None) -> Evaluation:
    """Run every rule and the propagation CTE, then persist signals and derived risk in the caller's tx."""
    policy = await load_policy(session)
    when = as_of or await resolve_as_of(session, policy)
    signals: list[SignalDraft] = []
    for rule in RULES:
        signals.extend(await rule(session, policy, when))
    slips = await propagate(session, when)
    scores = score(signals, slips)
    await _persist_signals(session, signals)
    await _persist_scores(session, scores, slips, when)
    return Evaluation(when, signals, scores, slips)


async def _persist_signals(session: AsyncSession, signals: list[SignalDraft]) -> None:
    current = {(s.work_item_id, s.rule): s for s in signals}
    existing = await session.execute(
        text("SELECT id, work_item_id, rule, evidence_event_ids FROM signals WHERE resolved_at IS NULL")
    )
    seen: set[tuple[UUID, str]] = set()
    for row in existing:
        key = (row.work_item_id, row.rule)
        draft = current.get(key)
        if draft is None:
            await session.execute(
                text("UPDATE signals SET resolved_at = now() WHERE id = :id"), {"id": row.id}
            )
            continue
        seen.add(key)
        changed = list(row.evidence_event_ids) != draft.evidence
        await session.execute(
            text("""
                UPDATE signals SET severity = :sev, evidence_event_ids = :ev, details = CAST(:details AS
                    jsonb),
                    computed_at = now(),
                    explanation = CASE WHEN :changed THEN NULL ELSE explanation END,
                    explanation_evidence = CASE WHEN :changed THEN NULL ELSE explanation_evidence END
                WHERE id = :id
            """),
            {
                "sev": draft.severity,
                "ev": draft.evidence,
                "details": json.dumps(draft.details, default=str),
                "changed": changed,
                "id": row.id,
            },
        )
    for key, draft in current.items():
        if key in seen:
            continue
        await session.execute(
            text("""
                INSERT INTO signals (tenant_id, work_item_id, rule, severity, evidence_event_ids, details)
                SELECT tenant_id, id, :rule, :sev, :ev, CAST(:details AS jsonb) FROM work_items WHERE id =
                    :item
            """),
            {
                "rule": draft.rule,
                "sev": draft.severity,
                "ev": draft.evidence,
                "details": json.dumps(draft.details, default=str),
                "item": draft.work_item_id,
            },
        )


async def _persist_scores(
    session: AsyncSession, scores: dict[UUID, int], slips: dict[UUID, Slip], as_of: datetime
) -> None:
    ids = list(scores)
    await session.execute(
        text("""
            UPDATE work_items SET risk_score = 0, risk_level = 'ok', projected_slip_days = 0,
                risk_evaluated_at = :as_of
            WHERE NOT (id = ANY(CAST(:ids AS uuid[])))
        """),
        {"ids": ids, "as_of": as_of},
    )
    if ids:
        await session.execute(
            text("""
                UPDATE work_items w SET risk_score = v.score, risk_level = v.level, projected_slip_days =
                    v.slip,
                    risk_evaluated_at = :as_of
                FROM unnest(CAST(:ids AS uuid[]), CAST(:scores AS int[]), CAST(:levels AS text[]),
                            CAST(:slips AS real[])) AS v(id, score, level, slip)
                WHERE w.id = v.id
            """),
            {
                "ids": ids,
                "scores": [scores[i] for i in ids],
                "levels": [level(scores[i]) for i in ids],
                "slips": [slips[i].days if i in slips else 0.0 for i in ids],
                "as_of": as_of,
            },
        )


async def evaluate_tenant(tenant_id: UUID, as_of: datetime | None = None) -> Evaluation:
    """Committed evaluation; listeners are told on commit (Postgres NOTIFY is transactional)."""
    async with tenant_tx(tenant_id) as session:
        result = await evaluate(session, as_of)
        await session.execute(text("SELECT pg_notify(:ch, :t)"), {"ch": NOTIFY_CHANNEL, "t": str(tenant_id)})
    at_risk = sum(1 for s in result.scores.values() if level(s) == "at_risk")
    log.info(
        "risk.evaluated", extra={"tenant": str(tenant_id), "signals": len(result.signals), "at_risk": at_risk}
    )
    return result


async def health_summary(session: AsyncSession) -> dict[str, Any]:
    """Tenant-wide launch health plus per-project health, from the stored derived risk of active items."""
    rows = (
        await session.execute(
            text("""
                SELECT p.id AS project_id, p.name, w.risk_level
                FROM projects p LEFT JOIN work_items w
                  ON w.project_id = p.id AND w.status NOT IN ('done', 'cancelled')
                ORDER BY p.created_at
            """)
        )
    ).all()
    tenant_levels = list(
        await session.scalars(
            text("SELECT risk_level FROM work_items WHERE status NOT IN ('done', 'cancelled')")
        )
    )
    projects: dict[UUID, dict[str, Any]] = {}
    for r in rows:
        entry = projects.setdefault(r.project_id, {"project_id": r.project_id, "name": r.name, "levels": []})
        if r.risk_level:
            entry["levels"].append(r.risk_level)
    return {
        "health": health(tenant_levels),
        "counts": {lvl: tenant_levels.count(lvl) for lvl in ("at_risk", "watch", "ok")},
        "projects": [
            {
                "project_id": p["project_id"],
                "name": p["name"],
                "health": health(p["levels"]),
                "at_risk": p["levels"].count("at_risk"),
                "watch": p["levels"].count("watch"),
            }
            for p in projects.values()
        ],
    }


async def evaluate_as_of(tenant_id: UUID, as_of: datetime) -> dict[str, Any]:
    """Time machine: rebuild the projection from events up to `as_of`, evaluate, read, then ROLL BACK. Nothing
    persists and other sessions never see the intermediate state (MVCC). Cost is O(events) per call."""
    from workers.projector import fold_events  # projector imports nothing from rules; avoid a module cycle

    async with get_sessionmaker()() as session:
        await session.begin()
        try:
            await set_tenant(session, tenant_id)
            await session.execute(text("DELETE FROM work_items"))  # cascades to edges and signals
            await fold_events(session, tenant_id, until=as_of)
            await evaluate(session, as_of)
            items = (
                (
                    await session.execute(
                        text("""
                        SELECT w.key, w.title, w.kind, w.lane, w.status, w.due_at, w.risk_score, w.risk_level,
                               w.projected_slip_days, w.project_id,
                               coalesce(json_agg(json_build_object('rule', s.rule, 'severity', s.severity,
                                   'evidence_event_ids', s.evidence_event_ids, 'details', s.details))
                                   FILTER (WHERE s.id IS NOT NULL), '[]') AS signals
                        FROM work_items w LEFT JOIN signals s ON s.work_item_id = w.id AND s.resolved_at IS
                            NULL
                        GROUP BY w.id ORDER BY w.risk_score DESC, w.key
                    """)
                    )
                )
                .mappings()
                .all()
            )
            summary = await health_summary(session)
        finally:
            await session.rollback()
    return {"as_of": as_of, **summary, "items": [dict(i) for i in items]}
