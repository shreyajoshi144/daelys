"""Tenant B must never see tenant A: through the API, and through raw SQL as the app role."""

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from connectors.replay import ReplayConnector
from core.db import global_tx, tenant_tx
from core.security import Role
from tests.conftest import SeededTenant, auth_headers
from workers.projector import project_tenant

TENANT_TABLES = [
    "users",
    "memberships",
    "integrations",
    "events",
    "work_items",
    "edges",
    "artifacts",
    "chunks",
    "signals",
    "agent_runs",
    "proposals",
    "audit_log",
    "connector_cursors",
]


@pytest.fixture(scope="module")
async def tiffin_data(tiffin: SeededTenant) -> SeededTenant:
    await ReplayConnector("tiffin_box", "week1").replay(tiffin.id)
    await project_tenant(tiffin.id)
    return tiffin


async def _tiffin_ids(tiffin: SeededTenant) -> tuple[list[str], list[int]]:
    async with tenant_tx(tiffin.id) as s:
        items = [str(i) for i in await s.scalars(text("SELECT id FROM work_items"))]
        events = list(await s.scalars(text("SELECT id FROM events")))
    assert items and events, "fixture must give tenant A real rows"
    return items, events


async def test_decoy_user_gets_404_or_empty_for_every_tiffin_id(
    client: httpx.AsyncClient, tiffin_data: SeededTenant, decoy: SeededTenant
) -> None:
    items, _ = await _tiffin_ids(tiffin_data)
    for role in (Role.owner, Role.viewer):
        headers = await auth_headers(client, decoy, role)
        for item_id in items:
            resp = await client.get(f"/work-items/{item_id}", headers=headers)
            assert resp.status_code == 404, (item_id, resp.status_code)
        listing = await client.get("/work-items", headers=headers)
        assert listing.status_code == 200 and listing.json()["items"] == []
        events = await client.get("/events", params={"since": "2000-01-01T00:00:00Z"}, headers=headers)
        assert events.status_code == 200 and events.json()["items"] == []


async def test_owner_sees_own_items(client: httpx.AsyncClient, tiffin_data: SeededTenant) -> None:
    items, events = await _tiffin_ids(tiffin_data)
    headers = await auth_headers(client, tiffin_data, Role.viewer)
    assert (await client.get(f"/work-items/{items[0]}", headers=headers)).status_code == 200
    page = (await client.get("/events", params={"limit": 500}, headers=headers)).json()
    assert {e["id"] for e in page["items"]} >= set(events)


async def test_raw_sql_as_app_role_with_tenant_b_returns_zero_tenant_a_rows(
    tiffin_data: SeededTenant, decoy: SeededTenant
) -> None:
    async with tenant_tx(decoy.id) as s:
        assert await s.scalar(text("SELECT current_user")) == "daelys_app"
        for table in TENANT_TABLES:
            leaked = await s.scalar(
                text("SELECT count(*) FROM " + table + " WHERE tenant_id = :a"),  # noqa: S608 - fixed list
                {"a": tiffin_data.id},
            )
            assert leaked == 0, table
        assert await s.scalar(text("SELECT count(*) FROM tenants WHERE id = :a"), {"a": tiffin_data.id}) == 0


async def test_no_tenant_set_fails_closed(tiffin_data: SeededTenant) -> None:
    async with global_tx() as s:
        for table in ["tenants", *TENANT_TABLES]:
            assert await s.scalar(text("SELECT count(*) FROM " + table)) == 0, table  # noqa: S608


async def test_cannot_write_into_another_tenant(tiffin_data: SeededTenant, decoy: SeededTenant) -> None:
    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_tx(decoy.id) as s:
            await s.execute(
                text("INSERT INTO work_items (tenant_id, key, kind) VALUES (:a, 'smuggled', 'task')"),
                {"a": tiffin_data.id},
            )


async def test_cannot_update_into_another_tenant(tiffin_data: SeededTenant, decoy: SeededTenant) -> None:
    async with tenant_tx(decoy.id) as s:
        result = await s.execute(
            text("UPDATE work_items SET title = 'pwned' WHERE tenant_id = :a"), {"a": tiffin_data.id}
        )
        assert result.rowcount == 0  # type: ignore[attr-defined]


async def test_every_table_has_rls_and_app_role_is_restricted(database: None) -> None:
    async with global_tx() as s:
        unprotected = set(
            await s.scalars(
                text("""
                    SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relkind = 'r'
                      AND (NOT c.relrowsecurity
                           OR NOT EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid))
                """)
            )
        )
        assert unprotected == {"alembic_version"}
        role = (
            await s.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'daelys_app'"))
        ).one()
        assert (role.rolsuper, role.rolbypassrls) == (False, False)
        for table in ("events", "audit_log"):
            for priv in ("UPDATE", "DELETE"):
                assert not await s.scalar(
                    text("SELECT has_table_privilege('daelys_app', :t, :p)"), {"t": table, "p": priv}
                ), (table, priv)
        assert not await s.scalar(
            text("SELECT has_table_privilege('daelys_app', 'alembic_version', 'SELECT')")
        )


async def test_events_are_append_only(tiffin_data: SeededTenant) -> None:
    for stmt in ("UPDATE events SET type = 'x'", "DELETE FROM events"):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with tenant_tx(tiffin_data.id) as s:
                await s.execute(text(stmt))
