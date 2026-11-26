"""Projection worker: resume from `connector_cursors` after a restart or crash, and survive poison events."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import text

from connectors.base import get_cursor
from core.db import tenant_tx
from core.events import NewEvent, append_events
from core.security import Role
from tests.conftest import SeededTenant
from workers import main as worker
from workers import projector
from workers.projector import CURSOR_NAME, ci_rollup, project_batch, project_tenant

T0 = datetime(2026, 3, 9, 9, 0, tzinfo=UTC)


def make_events(n: int) -> list[NewEvent]:
    return [
        NewEvent(
            source="test",
            type="issue.opened",
            idempotency_key=f"evt-{i}",
            occurred_at=T0 + timedelta(minutes=i),
            subject_key=f"test:item-{i}",
            payload={"fields": {"kind": "task", "title": f"Item {i}", "status": "open"}},
        )
        for i in range(n)
    ]


async def append(tenant_id: UUID, events: list[NewEvent]) -> list[int]:
    async with tenant_tx(tenant_id) as s:
        return (await append_events(s, tenant_id, events)).inserted_ids


async def cursor(tenant_id: UUID) -> int:
    async with tenant_tx(tenant_id) as s:
        return int((await get_cursor(s, CURSOR_NAME) or {"last_event_id": 0})["last_event_id"])


async def item_count(tenant_id: UUID) -> int:
    async with tenant_tx(tenant_id) as s:
        return int(await s.scalar(text("SELECT count(*) FROM work_items")) or 0)


async def test_restart_resumes_from_cursor(fresh_tenant: SeededTenant) -> None:
    ids = await append(fresh_tenant.id, make_events(12))
    assert await project_batch(fresh_tenant.id, batch_size=5) == 5  # worker stops after one batch
    assert await cursor(fresh_tenant.id) == ids[4] and await item_count(fresh_tenant.id) == 5

    assert await worker.project({}, str(fresh_tenant.id)) == 7  # restarted worker job picks up the rest
    assert await cursor(fresh_tenant.id) == ids[-1] and await item_count(fresh_tenant.id) == 12
    assert await project_tenant(fresh_tenant.id) == 0


async def test_crash_mid_batch_rolls_back_and_retries_cleanly(
    fresh_tenant: SeededTenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = await append(fresh_tenant.id, make_events(8))
    real_apply = projector.apply_event

    async def crashing_apply(session, tenant_id, ev):  # type: ignore[no-untyped-def]
        if ev["id"] == ids[5]:
            raise ConnectionError("worker killed")
        await real_apply(session, tenant_id, ev)

    monkeypatch.setattr(projector, "apply_event", crashing_apply)
    with pytest.raises(ConnectionError):
        await project_tenant(fresh_tenant.id, batch_size=4)
    assert await cursor(fresh_tenant.id) == ids[3]  # first batch committed, second rolled back
    assert await item_count(fresh_tenant.id) == 4

    monkeypatch.setattr(projector, "apply_event", real_apply)
    assert await project_tenant(fresh_tenant.id, batch_size=4) == 4
    assert await item_count(fresh_tenant.id) == 8


async def test_poison_event_is_skipped_not_wedged(
    fresh_tenant: SeededTenant, caplog: pytest.LogCaptureFixture
) -> None:
    bad = NewEvent(
        source="test",
        type="issue.opened",
        idempotency_key="bad",
        occurred_at=T0,
        subject_key="test:bad",
        payload={"fields": {"kind": "task", "due_at": "not-a-date"}},
    )
    await append(fresh_tenant.id, [bad, *make_events(2)])
    assert await project_tenant(fresh_tenant.id) == 3
    assert "projector.event_skipped" in caplog.text
    async with tenant_tx(fresh_tenant.id) as s:
        assert await s.scalar(text("SELECT count(*) FROM work_items WHERE key = 'test:bad'")) == 0
        assert await s.scalar(text("SELECT count(*) FROM work_items WHERE key LIKE 'test:item-%'")) == 2


async def test_invalid_field_values_are_ignored(fresh_tenant: SeededTenant) -> None:
    ev = NewEvent(
        source="test",
        type="x",
        idempotency_key="inv",
        occurred_at=T0,
        subject_key="test:inv",
        payload={"fields": {"kind": "task", "status": "exploded", "owner_id": "hijack", "title": "ok"}},
    )
    await append(fresh_tenant.id, [ev])
    await project_tenant(fresh_tenant.id)
    async with tenant_tx(fresh_tenant.id) as s:
        row = (
            await s.execute(text("SELECT status, title, owner_id FROM work_items WHERE key = 'test:inv'"))
        ).one()
    assert (row.status, row.title, row.owner_id) == ("open", "ok", None)


async def test_owner_login_resolves_to_user(fresh_tenant: SeededTenant) -> None:
    login = fresh_tenant.users[Role.lead].github_login
    ev = NewEvent(
        source="test",
        type="x",
        idempotency_key="own",
        occurred_at=T0,
        subject_key="test:own",
        payload={"fields": {"kind": "task", "owner_login": login}},
    )
    await append(fresh_tenant.id, [ev])
    await project_tenant(fresh_tenant.id)
    async with tenant_tx(fresh_tenant.id) as s:
        owner = await s.scalar(text("SELECT owner_id FROM work_items WHERE key = 'test:own'"))
        expected = await s.scalar(text("SELECT id FROM users WHERE github_login = :l"), {"l": login})
    assert owner == expected and owner is not None


async def test_sweep_enqueues_every_tenant(fresh_tenant: SeededTenant) -> None:
    enqueued: list[str] = []

    class FakeRedis:
        async def enqueue_job(self, name: str, tenant: str, _job_id: str) -> None:
            enqueued.append(tenant)

    await worker.sweep({"redis": FakeRedis()})
    assert str(fresh_tenant.id) in enqueued


def test_ci_rollup() -> None:
    assert ci_rollup({}) is None
    assert ci_rollup({"a": "success", "b": "pending"}) == "pending"
    assert ci_rollup({"a": "success", "b": "failure", "c": "pending"}) == "failure"
    assert ci_rollup({"a": "success"}) == "success"


def test_naive_timestamps_rejected() -> None:
    with pytest.raises(ValueError):
        NewEvent(source="t", type="t", idempotency_key="k", occurred_at=datetime(2026, 1, 1))
