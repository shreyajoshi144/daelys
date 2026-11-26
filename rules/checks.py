"""The five risk rules. Each is a pure function of the database state at `as_of` (inside the caller's
tenant-scoped transaction) and returns signal drafts with the event ids that justify them. No LLM here."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ACTIVE = "status NOT IN ('done', 'cancelled')"
# Event that last set a projected field: field_versions stores "<timestamp>|<idempotency_key>".
_SET_BY = (
    "(SELECT e.id FROM events e WHERE e.idempotency_key = "
    "substr({col}->>'{field}', strpos({col}->>'{field}', '|') + 1))"
)


def set_by(alias: str, field_name: str) -> str:
    return _SET_BY.format(col=f"{alias}.field_versions", field=field_name)


@dataclass(frozen=True)
class Policy:
    stale_pr_days: int = 3
    due_window_days: int = 5
    overload_limit: int = 3
    blocker_window_days: int = 7
    clock: str = "wall"


@dataclass
class SignalDraft:
    work_item_id: UUID
    rule: str
    severity: str
    evidence: list[int]
    details: dict[str, Any] = field(default_factory=dict)


# SQL is built once from the constants above (no runtime input is ever interpolated).
_FMT = {
    "active": ACTIVE,
    "i_due_at": set_by("i", "due_at"),
    "u_due_at": set_by("u", "due_at"),
    "u_status": set_by("u", "status"),
}
_STALE_PR_SQL = text(
    """
            SELECT i.id, i.due_at, p.key AS pr_key, p.title AS pr_title, p.last_activity_at,
                   {i_due_at} AS due_event,
                   (SELECT e.id FROM events e WHERE e.subject_key = p.key AND e.occurred_at <= :as_of
                    ORDER BY e.occurred_at DESC, e.id DESC LIMIT 1) AS last_event
            FROM work_items i
            JOIN edges l ON l.dst_id = i.id AND l.kind = 'closes'
            JOIN work_items p ON p.id = l.src_id AND p.kind = 'pr'
            WHERE i.{active} AND p.status IN ('open', 'in_progress', 'in_review')
              AND i.due_at IS NOT NULL AND i.due_at <= :as_of + make_interval(days => :window)
              AND p.last_activity_at <= :as_of - make_interval(days => :stale)
        """.format(**_FMT)
)
_CI_FAILING_SQL = text(
    """
            SELECT i.id, i.due_at, p.key AS pr_key, p.title AS pr_title, {i_due_at} AS due_event,
                   ARRAY(SELECT e.id FROM events e
                         WHERE e.subject_key = p.key AND e.type LIKE 'ci.%' AND e.occurred_at <= :as_of
                           AND e.payload @? '$.fields.* ? (@ == "failure")'
                         ORDER BY e.occurred_at DESC, e.id DESC LIMIT 3) AS failures
            FROM work_items i
            JOIN edges l ON l.dst_id = i.id AND l.kind = 'closes'
            JOIN work_items p ON p.id = l.src_id AND p.kind = 'pr'
            WHERE i.{active} AND p.{active} AND p.ci_status = 'failure'
              AND i.due_at IS NOT NULL AND i.due_at <= :as_of + make_interval(days => :window)
        """.format(**_FMT)
)
_OVERDUE_UPSTREAM_SQL = text(
    """
            SELECT d.id, u.key AS up_key, u.title AS up_title, u.status AS up_status, u.due_at AS up_due,
                   e.source_event_id, {u_due_at} AS due_event, {u_status} AS status_event
            FROM edges e
            JOIN work_items d ON d.id = e.src_id
            JOIN work_items u ON u.id = e.dst_id
            WHERE e.kind = 'depends_on' AND d.{active} AND u.{active}
              AND (u.status = 'blocked' OR u.due_at < :as_of)
        """.format(**_FMT)
)
_BLOCKER_SQL = text(
    """
            SELECT i.id, array_agg(e.id ORDER BY e.occurred_at DESC, e.id DESC) AS ids,
                   (array_agg(e.payload->>'text' ORDER BY e.occurred_at DESC, e.id DESC))[1] AS latest
            FROM work_items i
            JOIN events e ON e.subject_key = i.key AND e.type = 'blocker.mentioned'
            WHERE i.{active} AND e.occurred_at <= :as_of
              AND e.occurred_at > :as_of - make_interval(days => :window)
            GROUP BY i.id
        """.format(**_FMT)
)
_OVERLOAD_SQL = text(
    """
            WITH due_soon AS (
                SELECT i.id, coalesce(i.owner_id::text, i.owner_login) AS owner, {i_due_at} AS due_event
                FROM work_items i
                WHERE i.{active} AND coalesce(i.owner_id::text, i.owner_login) IS NOT NULL
                  AND i.due_at >= :as_of AND i.due_at < :as_of + interval '7 days'
            ),
            loaded AS (SELECT owner FROM due_soon GROUP BY owner HAVING count(*) > :limit)
            SELECT d.owner, array_agg(d.id) AS items, array_agg(d.due_event) AS due_events
            FROM due_soon d JOIN loaded USING (owner) GROUP BY d.owner
        """.format(**_FMT)
)


Rule = Callable[[AsyncSession, Policy, datetime], Awaitable[list[SignalDraft]]]


def _ids(*values: Any) -> list[int]:
    out: list[int] = []
    for value in values:
        for v in value if isinstance(value, list | tuple) else [value]:
            if v is not None and int(v) not in out:
                out.append(int(v))
    return out


def _days(delta: timedelta) -> float:
    return round(delta.total_seconds() / 86400, 1)


async def stale_pr(session: AsyncSession, policy: Policy, as_of: datetime) -> list[SignalDraft]:
    rows = await session.execute(
        _STALE_PR_SQL,
        {"as_of": as_of, "window": policy.due_window_days, "stale": policy.stale_pr_days},
    )
    return [
        SignalDraft(
            r.id,
            "stale_pr",
            "high" if r.due_at - as_of <= timedelta(days=2) else "medium",
            _ids(r.last_event, r.due_event),
            {
                "pr": r.pr_key,
                "pr_title": r.pr_title,
                "idle_days": _days(as_of - r.last_activity_at),
                "due_in_days": _days(r.due_at - as_of),
            },
        )
        for r in rows
    ]


async def ci_failing_near_due(session: AsyncSession, policy: Policy, as_of: datetime) -> list[SignalDraft]:
    rows = await session.execute(
        _CI_FAILING_SQL,
        {"as_of": as_of, "window": policy.due_window_days},
    )
    return [
        SignalDraft(
            r.id,
            "ci_failing_near_due",
            "critical" if r.due_at - as_of <= timedelta(days=1) else "high",
            _ids(list(r.failures), r.due_event),
            {"pr": r.pr_key, "pr_title": r.pr_title, "due_in_days": _days(r.due_at - as_of)},
        )
        for r in rows
        if r.failures
    ]


async def overdue_upstream(session: AsyncSession, policy: Policy, as_of: datetime) -> list[SignalDraft]:
    rows = await session.execute(
        _OVERDUE_UPSTREAM_SQL,
        {"as_of": as_of},
    )
    drafts = []
    for r in rows:
        overdue = r.up_due is not None and r.up_due < as_of
        drafts.append(
            SignalDraft(
                r.id,
                "overdue_upstream",
                "high" if overdue else "medium",
                _ids(r.due_event if overdue else r.status_event, r.source_event_id),
                {
                    "upstream": r.up_key,
                    "upstream_title": r.up_title,
                    "upstream_status": r.up_status,
                    "upstream_overdue_days": _days(as_of - r.up_due) if overdue else 0,
                },
            )
        )
    return drafts


async def explicit_blocker(session: AsyncSession, policy: Policy, as_of: datetime) -> list[SignalDraft]:
    rows = await session.execute(
        _BLOCKER_SQL,
        {"as_of": as_of, "window": policy.blocker_window_days},
    )
    return [
        SignalDraft(
            r.id, "explicit_blocker", "high", _ids(list(r.ids)[:5]), {"latest": (r.latest or "")[:300]}
        )
        for r in rows
    ]


async def owner_overloaded(session: AsyncSession, policy: Policy, as_of: datetime) -> list[SignalDraft]:
    rows = await session.execute(
        _OVERLOAD_SQL,
        {"as_of": as_of, "limit": policy.overload_limit},
    )
    drafts = []
    for r in rows:
        count = len(r.items)
        for item in r.items:
            drafts.append(
                SignalDraft(
                    item,
                    "owner_overloaded",
                    "medium" if count > policy.overload_limit + 2 else "low",
                    _ids(list(r.due_events)[:10]),
                    {"owner": r.owner, "items_due_this_week": count, "limit": policy.overload_limit},
                )
            )
    return drafts


RULES: list[Rule] = [stale_pr, ci_failing_near_due, overdue_upstream, explicit_blocker, owner_overloaded]
