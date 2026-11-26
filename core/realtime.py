"""Live updates: one LISTEN connection per API process fans Postgres NOTIFY out to per-tenant SSE subscribers.

The notification carries only a tenant id; subscribers refetch through the normal (RLS-scoped) API, so nothing
tenant-specific travels over this channel. A subscriber only ever receives its own tenant's notifications.
"""

import asyncio
import logging
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from sqlalchemy.engine import make_url

from core.config import get_settings

log = logging.getLogger(__name__)

CHANNEL = "daelys_updates"
QUEUE_SIZE = 16
CLOSED = "closed"  # the LISTEN connection dropped: streams refetch once and end, and the browser reconnects


class Broadcaster:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[str]]] = defaultdict(set)
        self._conn: asyncpg.Connection | None = None
        self._lock = asyncio.Lock()

    async def _ensure_listening(self) -> None:
        async with self._lock:
            if self._conn is not None and not self._conn.is_closed():
                return
            dsn = make_url(get_settings().app_database_url).set(drivername="postgresql")
            self._conn = await asyncpg.connect(dsn.render_as_string(hide_password=False), timeout=10)
            await self._conn.add_listener(CHANNEL, self._on_notify)
            self._conn.add_termination_listener(self._on_terminated)
            log.info("realtime.listening")

    def _on_terminated(self, conn: Any) -> None:
        """Postgres restarted or the connection broke. Without this, open streams would keep sending
        keepalives and never see another update; ending them makes EventSource reconnect and re-listen."""
        if conn is self._conn:
            self._conn = None
        log.warning("realtime.listener_lost", extra={"streams": sum(map(len, self._subscribers.values()))})
        for queues in self._subscribers.values():
            for queue in queues:
                while queue.full():
                    queue.get_nowait()
                queue.put_nowait(CLOSED)

    def _on_notify(self, conn: Any, pid: int, channel: str, payload: str) -> None:
        for queue in self._subscribers.get(payload, ()):
            if queue.full():
                continue  # a slow client only needs to know "something changed"; drop duplicates
            queue.put_nowait("update")

    @asynccontextmanager
    async def subscribe(self, tenant_id: str) -> AsyncIterator[asyncio.Queue[str]]:
        await self._ensure_listening()
        queue: asyncio.Queue[str] = asyncio.Queue(QUEUE_SIZE)
        self._subscribers[tenant_id].add(queue)
        try:
            yield queue
        finally:
            self._subscribers[tenant_id].discard(queue)
            if not self._subscribers[tenant_id]:
                del self._subscribers[tenant_id]

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None


broadcaster = Broadcaster()
