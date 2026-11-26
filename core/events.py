import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class NewEvent:
    """A normalized event, ready to append. Connectors produce these; the projector consumes them."""

    source: str
    type: str
    idempotency_key: str
    occurred_at: datetime
    subject_key: str | None = None
    actor: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")


@dataclass(frozen=True)
class AppendResult:
    inserted_ids: list[int]
    duplicates: int


_INSERT = text("""
    INSERT INTO events (tenant_id, source, type, idempotency_key, occurred_at, actor, subject_key, payload)
    VALUES (:tenant_id, :source, :type, :key, :occurred_at, :actor, :subject_key, CAST(:payload AS jsonb))
    ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
    RETURNING id
""")


async def append_events(session: AsyncSession, tenant_id: UUID, events: list[NewEvent]) -> AppendResult:
    """Append events idempotently inside the caller's tenant transaction.

    A per-tenant transaction-scoped advisory lock serialises appends, so event ids commit in id order
    within a tenant. That lets the projector use `id > cursor` without skipping late-committing rows.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:tid, 0))"), {"tid": str(tenant_id)}
    )
    inserted: list[int] = []
    for ev in events:
        new_id = await session.scalar(
            _INSERT,
            {
                "tenant_id": tenant_id,
                "source": ev.source,
                "type": ev.type,
                "key": ev.idempotency_key,
                "occurred_at": ev.occurred_at.astimezone(UTC),
                "actor": ev.actor,
                "subject_key": ev.subject_key,
                "payload": json.dumps(ev.payload),
            },
        )
        if new_id is not None:
            inserted.append(new_id)
    return AppendResult(inserted_ids=inserted, duplicates=len(events) - len(inserted))
