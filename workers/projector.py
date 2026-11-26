"""Folds the event log into `work_items` and `edges`.

Order independence: every projected field is a last-writer-wins register versioned by
(occurred_at, idempotency_key). An event only overwrites a field whose stored version is older, so replaying
the same events in any order yields the same projection, and re-applying an event is a no-op.
"""

import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from connectors.base import get_cursor, set_cursor
from core.db import tenant_tx

log = logging.getLogger(__name__)

CURSOR_NAME = "projector"
KINDS = {"issue", "pr", "task"}
STATUSES = {"open", "in_progress", "in_review", "blocked", "done", "cancelled"}
LANES = {"Product", "Engineering", "Marketing"}
CI_STATES = {"pending", "success", "failure"}
SCALAR_FIELDS = {
    "kind",
    "title",
    "status",
    "lane",
    "owner_login",
    "due_at",
    "url",
    "last_activity_at",
    "project_id",
    "owner_role",
    "estimate_days",
    "source_span",
}
EDGE_PAYLOAD_KEYS = {"links": "closes", "depends_on": "depends_on"}
CI_PREFIX = "ci_checks."


def _version(occurred_at: datetime, idempotency_key: str) -> str:
    # Fixed-width UTC timestamp, so string comparison orders versions chronologically.
    return occurred_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ") + "|" + idempotency_key


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    ts = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if ts.tzinfo is None:
        raise ValueError(f"timestamp without timezone: {value!r}")
    return ts


def _valid(name: str, value: Any) -> bool:
    allowed = {"kind": KINDS, "status": STATUSES, "lane": LANES}.get(name)
    if allowed is not None:
        return value in allowed
    if name.startswith(CI_PREFIX):
        return value in CI_STATES
    if name == "project_id":
        return _is_uuid(value)
    if name == "owner_id":  # set by the executor (reassign / undo); None unassigns
        return value is None or _is_uuid(value)
    if name == "estimate_days":
        return isinstance(value, int | float) and not isinstance(value, bool) and 0 < value <= 365
    if name == "source_span":
        return (
            isinstance(value, dict)
            and set(value) == {"start", "end"}
            and all(isinstance(v, int) and not isinstance(v, bool) for v in value.values())
            and 0 <= value["start"] < value["end"]
        )
    if name == "owner_role":
        return isinstance(value, str) and len(value) <= 100
    return name in SCALAR_FIELDS


def _is_uuid(value: Any) -> bool:
    try:
        UUID(str(value))
    except ValueError:
        return False
    return True


def _default_kind(key: str) -> str:
    return "task" if key.startswith("plan:") else "issue"


def ci_rollup(checks: dict[str, str]) -> str | None:
    if not checks:
        return None
    values = set(checks.values())
    if "failure" in values:
        return "failure"
    return "pending" if "pending" in values else "success"


async def _load_or_create(session: AsyncSession, tenant_id: UUID, key: str, kind: str) -> RowMapping:
    await session.execute(
        text("""
            INSERT INTO work_items (tenant_id, key, kind) VALUES (:t, :k, :kind)
            ON CONFLICT (tenant_id, key) DO NOTHING
        """),
        {"t": tenant_id, "k": key, "kind": kind},
    )
    result = await session.execute(text("SELECT * FROM work_items WHERE key = :k FOR UPDATE"), {"k": key})
    return result.mappings().one()


async def _resolve_owner(session: AsyncSession, login: str | None) -> UUID | None:
    if not login:
        return None
    value = await session.scalar(text("SELECT id FROM users WHERE github_login = :l"), {"l": login})
    return UUID(str(value)) if value else None


async def _login_of(session: AsyncSession, user_id: UUID | None) -> str | None:
    if user_id is None:
        return None
    value = await session.scalar(text("SELECT github_login FROM users WHERE id = :u"), {"u": user_id})
    return str(value) if value else None


async def apply_event(session: AsyncSession, tenant_id: UUID, ev: RowMapping) -> None:
    payload: dict[str, Any] = ev["payload"]
    key: str | None = ev["subject_key"]
    if not key or not ({"fields", *EDGE_PAYLOAD_KEYS} & payload.keys()):
        return
    version = _version(ev["occurred_at"], ev["idempotency_key"])
    incoming: dict[str, Any] = dict(payload.get("fields", {}))
    incoming["last_activity_at"] = ev["occurred_at"].isoformat()

    kind = incoming.get("kind")
    item = await _load_or_create(session, tenant_id, key, kind if kind in KINDS else _default_kind(key))
    versions: dict[str, str] = dict(item["field_versions"])
    row: dict[str, Any] = {name: item[name] for name in SCALAR_FIELDS | {"owner_id"}}
    checks: dict[str, str] = dict(item["ci_checks"])

    for name, value in incoming.items():
        if not _valid(name, value):
            log.warning("projector.invalid_field", extra={"event_id": ev["id"], "field": name})
            continue
        if versions.get(name, "") >= version:
            continue
        versions[name] = version
        if name.startswith(CI_PREFIX):
            checks[name.removeprefix(CI_PREFIX)] = value
        elif name in ("due_at", "last_activity_at"):
            row[name] = _parse_ts(value)
        elif name == "owner_id":
            row["owner_id"] = UUID(str(value)) if value else None
            if "owner_login" not in incoming:
                row["owner_login"] = await _login_of(session, row["owner_id"])
        else:
            row[name] = value
        if name == "owner_login" and "owner_id" not in incoming:
            row["owner_id"] = await _resolve_owner(session, value)

    await session.execute(
        text("""
            UPDATE work_items SET kind = :kind, title = :title, status = :status, lane = :lane,
                owner_login = :owner_login, owner_id = :owner_id, due_at = :due_at, url = :url,
                project_id = :project_id, owner_role = :owner_role, estimate_days = :estimate_days,
                source_span = CAST(:source_span AS jsonb),
                ci_checks = CAST(:ci_checks AS jsonb), ci_status = :ci_status,
                last_activity_at = :last_activity_at, field_versions = CAST(:versions AS jsonb),
                updated_at = now()
            WHERE id = :id
        """),
        {
            **row,
            "source_span": json.dumps(row["source_span"]) if row["source_span"] is not None else None,
            "ci_checks": json.dumps(checks),
            "ci_status": ci_rollup(checks),
            "versions": json.dumps(versions),
            "id": item["id"],
        },
    )

    # Edge direction: src closes / depends on dst.
    for payload_key, edge_kind in EDGE_PAYLOAD_KEYS.items():
        for target in payload.get(payload_key, []):
            dst = await _load_or_create(session, tenant_id, target, _default_kind(target))
            if dst["id"] == item["id"]:
                continue
            await session.execute(
                text("""
                    INSERT INTO edges (tenant_id, src_id, dst_id, kind, source_event_id)
                    VALUES (:t, :src, :dst, :kind, :ev)
                    ON CONFLICT (tenant_id, src_id, dst_id, kind) DO NOTHING
                """),
                {"t": tenant_id, "src": item["id"], "dst": dst["id"], "kind": edge_kind, "ev": ev["id"]},
            )


async def _apply_all(session: AsyncSession, tenant_id: UUID, events: Sequence[RowMapping]) -> None:
    for ev in events:
        try:
            async with session.begin_nested():
                await apply_event(session, tenant_id, ev)
        except (ValueError, TypeError, KeyError, DataError, IntegrityError):
            # A malformed event must not wedge the projector forever. It stays in the log for replay after a
            # fix; the skip is logged loudly.
            log.exception("projector.event_skipped", extra={"event_id": ev["id"], "tenant": str(tenant_id)})


async def fold_events(session: AsyncSession, tenant_id: UUID, until: datetime) -> int:
    """Apply every event that occurred at or before `until` in the caller's transaction (time machine). The
    caller owns the transaction and typically rolls it back."""
    result = await session.execute(
        text("SELECT * FROM events WHERE occurred_at <= :until ORDER BY id"), {"until": until}
    )
    events = result.mappings().all()
    await _apply_all(session, tenant_id, events)
    return len(events)


async def project_batch(tenant_id: UUID, batch_size: int) -> int:
    """Project up to `batch_size` new events in one transaction, advancing the cursor atomically."""
    async with tenant_tx(tenant_id) as session:
        await session.execute(
            text("""
                INSERT INTO connector_cursors (tenant_id, connector, position)
                VALUES (:t, :c, '{"last_event_id": 0}') ON CONFLICT DO NOTHING
            """),
            {"t": tenant_id, "c": CURSOR_NAME},
        )
        # Row lock serialises projectors for this tenant.
        cursor = await get_cursor(session, CURSOR_NAME, lock=True) or {"last_event_id": 0}
        result = await session.execute(
            text("SELECT * FROM events WHERE id > :after ORDER BY id LIMIT :n"),
            {"after": cursor["last_event_id"], "n": batch_size},
        )
        events = result.mappings().all()
        await _apply_all(session, tenant_id, events)
        if events:
            await set_cursor(session, tenant_id, CURSOR_NAME, {"last_event_id": events[-1]["id"]})
    return len(events)


async def project_tenant(tenant_id: UUID, batch_size: int = 200) -> int:
    total = 0
    while True:
        n = await project_batch(tenant_id, batch_size)
        total += n
        if n < batch_size:
            if total:
                log.info("projector.done", extra={"tenant": str(tenant_id), "events": total})
            return total
