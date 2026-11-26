"""Replay connector: feeds a scenario timeline into the event log on a simulated clock.

    python -m connectors.replay tiffin_box week2 --speed 60

Each event is appended in its own transaction together with the cursor, so a killed replay resumes where it
stopped, and idempotency keys make a re-run a no-op.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from datetime import time as clock_time
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from connectors.base import Connector, get_cursor, set_cursor
from core.config import get_settings
from core.db import resolve_tenant_slug, tenant_tx
from core.events import NewEvent, append_events
from core.logging import configure_logging

log = logging.getLogger(__name__)

SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios"
MAX_FILE_BYTES = 5 * 1024 * 1024
_NAME = re.compile(r"^[a-z0-9_]{1,64}$")


class TimelineEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    day: int = Field(ge=0, le=31)
    time: clock_time
    source: str
    type: str
    actor: str | None = None
    subject: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class Timeline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft: bool = False
    week_start: datetime
    events: list[TimelineEntry]


class ScenarioMeta(BaseModel):
    tenant_slug: str
    project: str | None = None


@dataclass(frozen=True)
class ReplayResult:
    appended: int
    duplicates: int
    resumed_from: int
    total: int


def _scenario_dir(scenario: str) -> Path:
    if not _NAME.fullmatch(scenario):
        raise ValueError(f"invalid scenario name: {scenario!r}")
    path = SCENARIOS_DIR / scenario
    if not path.is_dir():
        raise FileNotFoundError(f"scenario not found: {scenario}")
    return path


def _read_json(path: Path) -> Any:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"{path.name} exceeds {MAX_FILE_BYTES} bytes")
    return json.loads(path.read_text())


def load_meta(scenario: str) -> ScenarioMeta:
    return ScenarioMeta.model_validate(_read_json(_scenario_dir(scenario) / "scenario.json"))


class ReplayConnector(Connector):
    name = "replay"

    def __init__(self, scenario: str, timeline: str) -> None:
        if not _NAME.fullmatch(timeline):
            raise ValueError(f"invalid timeline name: {timeline!r}")
        self.scenario = scenario
        self.timeline = timeline
        self._path = _scenario_dir(scenario) / f"timeline_{timeline}.json"
        self._timeline: Timeline | None = None

    def _data(self) -> Timeline:
        if self._timeline is None:
            data = Timeline.model_validate(_read_json(self._path))
            if data.week_start.tzinfo is None:
                raise ValueError("week_start must include a UTC offset")
            self._timeline = data
        return self._timeline

    def digest(self) -> str:
        return hashlib.sha256(self._path.read_bytes()).hexdigest()[:16]

    @property
    def week_start(self) -> datetime:
        return self._data().week_start

    def load(self) -> list[NewEvent]:
        """All timeline events, oldest first (ties broken by idempotency key for determinism)."""
        events = [
            ev
            for entry in self._data().events
            for ev in self.normalize("entry", entry.model_dump(), entry.id)
        ]
        return sorted(events, key=lambda e: (e.occurred_at, e.idempotency_key))

    def normalize(self, kind: str, raw: Mapping[str, Any], dedupe_key: str) -> list[NewEvent]:
        entry = TimelineEntry.model_validate(raw)
        day_start = self._data().week_start + timedelta(days=entry.day)
        occurred = day_start.replace(hour=entry.time.hour, minute=entry.time.minute, second=entry.time.second)
        return [
            NewEvent(
                source=entry.source,
                type=entry.type,
                idempotency_key=f"replay:{self.scenario}:{self.timeline}:{dedupe_key}",
                occurred_at=occurred,
                subject_key=entry.subject,
                actor=entry.actor,
                payload=entry.payload,
            )
        ]

    async def _with_project(self, tenant_id: UUID, events: list[NewEvent]) -> list[NewEvent]:
        """Attach the scenario's items to its project (by name) when the tenant has that project."""
        project = load_meta(self.scenario).project
        if not project:
            return events
        async with tenant_tx(tenant_id) as s:
            project_id = await s.scalar(
                text("SELECT id FROM projects WHERE name = :n ORDER BY created_at LIMIT 1"), {"n": project}
            )
        if project_id is None:
            return events
        return [
            replace(
                ev, payload={**ev.payload, "fields": {**ev.payload["fields"], "project_id": str(project_id)}}
            )
            if "fields" in ev.payload
            else ev
            for ev in events
        ]

    async def backfill(self, tenant_id: UUID) -> int:
        return (await self.replay(tenant_id)).appended

    async def replay(
        self,
        tenant_id: UUID,
        *,
        speed: float | None = None,
        max_sleep: float = 2.0,
        reverse: bool = False,
        on_event: Callable[[], Awaitable[None]] | None = None,
    ) -> ReplayResult:
        """`speed` = simulated seconds per real second; None replays instantly. Sleeps are capped at
        `max_sleep` so overnight gaps don't stall a demo."""
        events = await self._with_project(tenant_id, self.load())
        if reverse:
            events.reverse()
        cursor_name = f"replay:{self.scenario}:{self.timeline}" + (":reverse" if reverse else "")
        async with tenant_tx(tenant_id) as s:
            position = await get_cursor(s, cursor_name) or {}
        digest = self.digest()
        # The cursor is an index into this exact timeline; if the file changed, start over (idempotency keys
        # make already-appended events no-ops) instead of skipping events inserted before the old index.
        start = int(position.get("next_index", 0)) if position.get("digest") == digest else 0
        appended = duplicates = 0
        previous: datetime | None = events[start - 1].occurred_at if 0 < start <= len(events) else None
        for index in range(start, len(events)):
            ev = events[index]
            if speed and previous is not None:
                gap = abs((ev.occurred_at - previous).total_seconds())
                await asyncio.sleep(min(gap / speed, max_sleep))
            async with tenant_tx(tenant_id) as s:
                result = await append_events(s, tenant_id, [ev])
                await set_cursor(s, tenant_id, cursor_name, {"next_index": index + 1, "digest": digest})
            appended += len(result.inserted_ids)
            duplicates += result.duplicates
            previous = ev.occurred_at
            if on_event is not None:
                await on_event()
        return ReplayResult(appended=appended, duplicates=duplicates, resumed_from=start, total=len(events))


async def _main(args: argparse.Namespace) -> None:
    from core import queue

    slug = args.tenant or load_meta(args.scenario).tenant_slug
    tenant_id = await resolve_tenant_slug(slug)
    if tenant_id is None:
        raise SystemExit(f"tenant not found: {slug} (run `make seed` first)")
    connector = ReplayConnector(args.scenario, args.timeline)
    try:
        result = await connector.replay(
            tenant_id,
            speed=args.speed or None,
            max_sleep=args.max_sleep,
            reverse=args.reverse,
            on_event=lambda: queue.enqueue_projection(tenant_id),
        )
    finally:
        await queue.close()
    log.info("replay.done", extra={"tenant": slug, **result.__dict__})


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m connectors.replay")
    parser.add_argument("scenario")
    parser.add_argument("timeline")
    parser.add_argument(
        "--speed", type=float, default=60.0, help="simulated seconds per real second; 0 = instant"
    )
    parser.add_argument("--max-sleep", type=float, default=2.0, help="cap on any single real-time wait (s)")
    parser.add_argument("--tenant", help="tenant slug (default: the scenario's tenant)")
    parser.add_argument("--reverse", action="store_true", help="replay newest-first (out-of-order check)")
    args = parser.parse_args()
    configure_logging(get_settings().log_level)
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
