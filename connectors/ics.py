"""Read-only calendar fallback: an uploaded .ics export becomes `calendar.event_scheduled` events.

Upload only. Fetching a calendar URL server-side would let a user make the API request arbitrary
addresses (SSRF), so it is deliberately not supported. Parsing covers what calendar exports
actually contain: VEVENT with UID, SUMMARY, DTSTART (UTC, TZID-qualified or all-day) and SEQUENCE;
folded lines are unfolded.
"""

from collections.abc import Mapping
from datetime import UTC, date, datetime, time
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from connectors.base import Connector
from core.errors import NotSupported
from core.events import NewEvent

MAX_BYTES = 2 * 1024 * 1024
MAX_EVENTS = 2000


def _unfold(raw: str) -> list[str]:
    lines: list[str] = []
    for line in raw.replace("\r\n", "\n").split("\n"):
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        elif line:
            lines.append(line)
    return lines


def _unescape(value: str) -> str:
    return (
        value.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def parse_datetime(value: str, params: dict[str, str]) -> datetime:
    if params.get("VALUE") == "DATE" or len(value) == 8:
        return datetime.combine(
            date(int(value[:4]), int(value[4:6]), int(value[6:8])), time(0, 0), tzinfo=UTC
        )
    naive = datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M%S")
    if value.endswith("Z"):
        return naive.replace(tzinfo=UTC)
    if "TZID" in params:
        try:
            return naive.replace(tzinfo=ZoneInfo(params["TZID"]))
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown time zone {params['TZID']!r}") from exc
    raise ValueError("floating time without a time zone")


def parse(raw: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in _unfold(raw):
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current is not None and current.get("uid") and current.get("start"):
                events.append(current)
            current = None
            if len(events) > MAX_EVENTS:
                raise ValueError(f"more than {MAX_EVENTS} events")
            continue
        if current is None or ":" not in line:
            continue
        head, value = line.split(":", 1)
        name, *raw_params = head.split(";")
        params = dict(p.split("=", 1) for p in raw_params if "=" in p)
        name = name.upper()
        if name == "UID":
            current["uid"] = value.strip()[:200]
        elif name == "SUMMARY":
            current["title"] = _unescape(value)[:300]
        elif name == "DTSTART":
            current["start"] = parse_datetime(value.strip(), params)
        elif name == "SEQUENCE":
            current["sequence"] = int(value) if value.strip().isdigit() else 0
        elif name == "STATUS":
            current["status"] = value.strip().lower()
    return events


class IcsConnector(Connector):
    name = "ics"

    def normalize(self, kind: str, raw: Mapping[str, Any], dedupe_key: str) -> list[NewEvent]:
        cancelled = raw.get("status") == "cancelled"
        return [
            NewEvent(
                source="calendar",
                type="calendar.event_cancelled" if cancelled else "calendar.event_scheduled",
                idempotency_key=f"ics:{dedupe_key}",
                occurred_at=raw["start"],
                payload={
                    "title": raw.get("title", "(no title)"),
                    "starts_at": raw["start"].isoformat(),
                    "uid": raw["uid"],
                    "sequence": raw.get("sequence", 0),
                },
            )
        ]

    def from_upload(self, body: bytes) -> list[NewEvent]:
        if len(body) > MAX_BYTES:
            raise ValueError(f"calendar file larger than {MAX_BYTES} bytes")
        try:
            text_ = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("calendar file must be UTF-8") from exc
        out: list[NewEvent] = []
        for ev in parse(text_):
            out.extend(self.normalize("vevent", ev, f"{ev['uid']}:{ev.get('sequence', 0)}"))
        return out

    async def backfill(self, tenant_id: UUID) -> int:
        raise NotSupported("the .ics connector is upload-only")
