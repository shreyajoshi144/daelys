"""Read-only tools for the proposer. Every function runs inside `readonly_tenant_tx`, so even a bug here
cannot write (Postgres rejects writes in a READ ONLY transaction) and RLS keeps it inside the tenant."""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from core.db import fetch_all, fetch_one

TEXT_LIMIT = 500
EVENT_LIMIT = 25


def _excerpt(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {k: payload[k] for k in ("fields", "text", "conclusion") if k in payload}
    if payload.get("body"):
        out["body"] = str(payload["body"])[:TEXT_LIMIT]
    return out


async def get_item(session: AsyncSession, item_id: UUID) -> dict[str, Any] | None:
    row = await fetch_one(
        session,
        """
                SELECT w.id, w.key, w.title, w.kind, w.lane, w.status, w.due_at, w.estimate_days, w.owner_id,
                       coalesce(u.display_name, w.owner_login) AS owner, w.risk_score, w.risk_level,
                       w.projected_slip_days, w.project_id
                FROM work_items w LEFT JOIN users u ON u.id = w.owner_id WHERE w.id = :id
        """,
        {"id": item_id},
    )
    if row is None:
        return None
    signals = await fetch_all(
        session,
        """
        SELECT rule, severity, details, evidence_event_ids FROM signals
        WHERE work_item_id = :id AND resolved_at IS NULL ORDER BY computed_at
        """,
        {"id": item_id},
    )
    return {**row, "signals": signals}


async def get_dependents(session: AsyncSession, item_id: UUID) -> list[dict[str, Any]]:
    rows = await fetch_all(
        session,
        """
            SELECT w.key, w.title, w.status, w.due_at, w.risk_level FROM edges e
            JOIN work_items w ON w.id = e.src_id
            WHERE e.dst_id = :id AND e.kind = 'depends_on' ORDER BY w.due_at NULLS LAST LIMIT 20
        """,
        {"id": item_id},
    )
    return rows


async def get_owner_load(session: AsyncSession) -> list[dict[str, Any]]:
    """Team members (never the agent) with their active and due-this-week workload."""
    rows = await fetch_all(
        session,
        """
            SELECT u.id, u.display_name, u.department, m.role,
                   count(w.id) FILTER (WHERE w.status NOT IN ('done', 'cancelled')) AS active_items,
                   count(w.id) FILTER (WHERE w.status NOT IN ('done', 'cancelled')
                                        AND w.due_at < now() + interval '7 days') AS due_this_week
            FROM users u JOIN memberships m ON m.user_id = u.id
            LEFT JOIN work_items w ON w.owner_id = u.id
            WHERE m.role IN ('owner', 'lead', 'member')
            GROUP BY u.id, u.display_name, u.department, m.role ORDER BY u.display_name
        """,
    )
    return rows


async def search_events(session: AsyncSession, item_key: str) -> list[dict[str, Any]]:
    """Recent events on the item and on PRs linked to it."""
    rows = await fetch_all(
        session,
        """
            WITH subjects AS (
                SELECT :key AS key
                UNION
                SELECT p.key FROM work_items i JOIN edges e ON e.dst_id = i.id AND e.kind = 'closes'
                JOIN work_items p ON p.id = e.src_id WHERE i.key = :key
            )
            SELECT e.id, e.type, e.occurred_at, e.actor, e.subject_key, e.payload FROM events e
            WHERE e.subject_key IN (SELECT key FROM subjects)
            ORDER BY e.occurred_at DESC, e.id DESC LIMIT :n
        """,
        {"key": item_key, "n": EVENT_LIMIT},
    )
    return [{**r, "payload": _excerpt(r["payload"])} for r in rows]


async def get_rejections(session: AsyncSession, item_id: UUID) -> list[dict[str, Any]]:
    rows = await fetch_all(
        session,
        """
            SELECT action_type, args, rejection_reason, decided_at FROM proposals
            WHERE work_item_id = :id AND status = 'rejected' ORDER BY decided_at DESC LIMIT 5
        """,
        {"id": item_id},
    )
    return rows
