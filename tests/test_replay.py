import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text

from connectors.replay import SCENARIOS_DIR, ReplayConnector
from core.db import tenant_tx
from tests.conftest import SeededTenant, make_tenant
from workers.projector import project_tenant

TIMELINE = SCENARIOS_DIR / "tiffin_box" / "timeline_week2.json"
PROJECTED = (
    "key",
    "kind",
    "title",
    "status",
    "lane",
    "owner_login",
    "due_at",
    "url",
    "ci_status",
    "ci_checks",
    "last_activity_at",
)


def expected_counts(path: Path) -> tuple[int, int]:
    """Independent of the projector: every event is stored; every subject or link target is one work item."""
    entries = json.loads(path.read_text())["events"]
    keys = {
        e["subject"]
        for e in entries
        if e.get("subject") and ("fields" in e["payload"] or "links" in e["payload"])
    }
    keys |= {link for e in entries for link in e["payload"].get("links", [])}
    return len(entries), len(keys)


async def counts(tenant_id: UUID) -> tuple[int, int]:
    async with tenant_tx(tenant_id) as s:
        events = await s.scalar(text("SELECT count(*) FROM events"))
        items = await s.scalar(text("SELECT count(*) FROM work_items"))
    return int(events or 0), int(items or 0)


async def snapshot(tenant_id: UUID) -> dict[str, dict[str, Any]]:
    async with tenant_tx(tenant_id) as s:
        rows = (await s.execute(text("SELECT " + ", ".join(PROJECTED) + " FROM work_items"))).mappings()  # noqa: S608
        return {r["key"]: dict(r) for r in rows}


async def test_week2_replay_produces_expected_events_and_items(fresh_tenant: SeededTenant) -> None:
    result = await ReplayConnector("tiffin_box", "week2").replay(fresh_tenant.id)
    await project_tenant(fresh_tenant.id)
    want_events, want_items = expected_counts(TIMELINE)
    assert want_events >= 25
    assert (result.appended, result.duplicates) == (want_events, 0)
    assert await counts(fresh_tenant.id) == (want_events, want_items)
    items = await snapshot(fresh_tenant.id)
    pr21 = items["gh:tiffin-box/tiffin-box-app#21"]
    assert (pr21["kind"], pr21["ci_status"]) == ("pr", "failure")


async def test_replay_is_idempotent(fresh_tenant: SeededTenant) -> None:
    connector = ReplayConnector("tiffin_box", "week2")
    await connector.replay(fresh_tenant.id)
    again = await connector.replay(fresh_tenant.id)
    assert again.appended == 0 and again.resumed_from == again.total
    assert (await counts(fresh_tenant.id))[0] == expected_counts(TIMELINE)[0]


async def test_killed_replay_resumes_from_cursor_without_duplicates(fresh_tenant: SeededTenant) -> None:
    calls = 0

    async def crash_after_ten() -> None:
        nonlocal calls
        calls += 1
        if calls == 10:
            raise KeyboardInterrupt  # simulates the process being killed

    with pytest.raises(KeyboardInterrupt):
        await ReplayConnector("tiffin_box", "week2").replay(fresh_tenant.id, on_event=crash_after_ten)
    assert (await counts(fresh_tenant.id))[0] == 10

    resumed = await ReplayConnector("tiffin_box", "week2").replay(fresh_tenant.id)
    assert resumed.resumed_from == 10 and resumed.appended == resumed.total - 10
    async with tenant_tx(fresh_tenant.id) as s:
        dupes = await s.scalar(
            text("SELECT count(*) FROM (SELECT idempotency_key FROM events GROUP BY 1 HAVING count(*) > 1) d")
        )
    assert dupes == 0


async def test_reverse_order_replay_matches_forward(database: None) -> None:
    forward, backward = await make_tenant(), await make_tenant()
    await ReplayConnector("tiffin_box", "week2").replay(forward.id)
    await ReplayConnector("tiffin_box", "week2").replay(backward.id, reverse=True)
    await project_tenant(forward.id)
    await project_tenant(backward.id)
    assert await snapshot(forward.id) == await snapshot(backward.id)


async def test_simulated_clock_sleeps_are_scaled_and_capped(
    fresh_tenant: SeededTenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("connectors.replay.asyncio.sleep", fake_sleep)
    await ReplayConnector("tiffin_box", "week2").replay(fresh_tenant.id, speed=600, max_sleep=1.5)
    assert len(sleeps) == expected_counts(TIMELINE)[0] - 1
    assert max(sleeps) == 1.5 and min(sleeps) >= 0
    assert pytest.approx(120 / 600) in sleeps  # 10:05 -> 10:07 on day 0 at 600x


@pytest.mark.parametrize("name", ["../etc", "Tiffin", "a/b"])
def test_rejects_unsafe_names(name: str) -> None:
    with pytest.raises((ValueError, FileNotFoundError)):
        ReplayConnector(name, "week2")
    with pytest.raises(ValueError):
        ReplayConnector("tiffin_box", name)
