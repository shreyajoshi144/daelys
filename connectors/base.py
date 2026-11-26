import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import NotSupported
from core.events import NewEvent


@dataclass(frozen=True)
class WebhookResult:
    tenant_id: UUID | None
    events: list[NewEvent]


class Connector(ABC):
    """Contract for every signal source. Connectors only produce normalized events; they never touch
    work_items. Progress that must survive a restart goes in `connector_cursors`."""

    name: str

    @abstractmethod
    def normalize(self, kind: str, raw: Mapping[str, Any], dedupe_key: str) -> list[NewEvent]:
        """Map one raw source record to zero or more normalized events."""

    @abstractmethod
    async def backfill(self, tenant_id: UUID) -> int:
        """Pull history from the source, resuming from the stored cursor. Returns events appended."""

    async def handle_webhook(self, headers: Mapping[str, str], body: bytes) -> WebhookResult:
        raise NotSupported(f"{self.name} has no webhooks")


async def get_cursor(session: AsyncSession, connector: str, *, lock: bool = False) -> dict[str, Any] | None:
    sql = "SELECT position FROM connector_cursors WHERE connector = :c"
    if lock:
        sql += " FOR UPDATE"
    value = await session.scalar(text(sql), {"c": connector})
    return dict(value) if value is not None else None


async def set_cursor(
    session: AsyncSession, tenant_id: UUID, connector: str, position: dict[str, Any]
) -> None:
    await session.execute(
        text("""
            INSERT INTO connector_cursors (tenant_id, connector, position, updated_at)
            VALUES (:t, :c, CAST(:p AS jsonb), now())
            ON CONFLICT (tenant_id, connector) DO UPDATE SET position = EXCLUDED.position, updated_at = now()
        """),
        {"t": tenant_id, "c": connector, "p": json.dumps(position)},
    )
