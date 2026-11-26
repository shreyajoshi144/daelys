import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from connectors.replay import ReplayConnector
from core.db import global_tx, tenant_tx
from core.realtime import CLOSED, Broadcaster
from core.security import Role
from core.seed import set_policy
from rules.engine import evaluate_tenant
from tests.conftest import PASSWORD, SeededTenant, auth_headers
from workers.projector import project_tenant

WEEK1_END = datetime(2026, 3, 6, 23, 59, tzinfo=UTC)
WEEK2_END = datetime(2026, 3, 13, 23, 59, tzinfo=UTC)
CHECKOUT = "gh:tiffin-box/tiffin-box-app#12"


async def replayed(tenant: SeededTenant) -> None:
    await set_policy(tenant.id, clock="event")
    for week in ("week1", "week2"):
        await ReplayConnector("tiffin_box", week).replay(tenant.id)
    await project_tenant(tenant.id)
    await evaluate_tenant(tenant.id)


async def item_id(tenant: SeededTenant, key: str) -> str:
    async with tenant_tx(tenant.id) as s:
        return str(await s.scalar(text("SELECT id FROM work_items WHERE key = :k"), {"k": key}))


async def test_health_signals_and_event_detail(
    client: httpx.AsyncClient, fresh_tenant: SeededTenant, decoy: SeededTenant
) -> None:
    await replayed(fresh_tenant)
    viewer = await auth_headers(client, fresh_tenant, Role.viewer)
    health = (await client.get("/risk/health", headers=viewer)).json()
    assert health["health"] == "At risk" and health["counts"]["at_risk"] >= 1

    checkout = await item_id(fresh_tenant, CHECKOUT)
    why = (await client.get(f"/work-items/{checkout}/signals", headers=viewer)).json()
    assert why["item"]["risk_level"] == "at_risk"
    rules = {s["rule"]: s for s in why["signals"]}
    assert set(rules) >= {"ci_failing_near_due", "explicit_blocker"}
    evidence = rules["ci_failing_near_due"]["evidence"]
    assert evidence and all(e["id"] for e in evidence) and "ci.completed" in {e["type"] for e in evidence}
    event = (await client.get(f"/events/{evidence[0]['id']}", headers=viewer)).json()
    assert event["id"] == evidence[0]["id"]

    items = (await client.get("/work-items", headers=viewer)).json()["items"]
    assert {i["key"]: i["risk_level"] for i in items}[CHECKOUT] == "at_risk"

    outsider = await auth_headers(client, decoy, Role.owner)
    assert (await client.get(f"/work-items/{checkout}/signals", headers=outsider)).status_code == 404
    assert (await client.get(f"/events/{evidence[0]['id']}", headers=outsider)).status_code == 404
    assert (await client.get("/risk/health", headers=outsider)).json()["health"] == "On track"


async def test_explain_needs_key_then_caches(
    client: httpx.AsyncClient, fresh_tenant: SeededTenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    await replayed(fresh_tenant)
    viewer = await auth_headers(client, fresh_tenant, Role.viewer)
    checkout = await item_id(fresh_tenant, CHECKOUT)
    signal = (await client.get(f"/work-items/{checkout}/signals", headers=viewer)).json()["signals"][0]
    missing = await client.post(f"/signals/{signal['id']}/explain", headers=viewer)
    assert missing.status_code == 503 and missing.json()["error"] == "not_configured"

    from agents.explainer import Explanation
    from api.routes import risk
    from core.llm import LLM, Completion, Route

    first_id = signal["evidence"][0]["id"]
    calls = []

    class Scripted:
        name = "scripted"

        async def complete(
            self, model: str, system: str, user: str, output_type: Any, max_tokens: int
        ) -> Completion[Any]:
            calls.append(1)
            return Completion(
                parsed=Explanation(sentences=[f"It failed [evt:{first_id}].", "Made up [evt:1]."]),
                input_tokens=1,
                output_tokens=1,
            )

    monkeypatch.setattr(risk, "get_llm", lambda: LLM([Route("scripted", "m")], {"scripted": Scripted()}))
    explained = (await client.post(f"/signals/{signal['id']}/explain", headers=viewer)).json()
    assert explained == {
        "sentences": [{"text": f"It failed [evt:{first_id}].", "event_ids": [first_id]}],
        "dropped": 1,
    }
    again = (await client.post(f"/signals/{signal['id']}/explain", headers=viewer)).json()
    assert again == explained and len(calls) == 1  # cached for the same evidence
    why = (await client.get(f"/work-items/{checkout}/signals", headers=viewer)).json()
    assert next(s for s in why["signals"] if s["id"] == signal["id"])["explanation"] == explained


async def test_time_machine_as_of_is_computed_and_never_persisted(
    client: httpx.AsyncClient, fresh_tenant: SeededTenant
) -> None:
    await replayed(fresh_tenant)
    viewer = await auth_headers(client, fresh_tenant, Role.viewer)
    async with tenant_tx(fresh_tenant.id) as s:
        before = (await s.execute(text("SELECT id, key, risk_score FROM work_items ORDER BY key"))).all()
        signals_before = await s.scalar(text("SELECT count(*) FROM signals"))
    early = (
        await client.get("/time-machine/state", params={"as_of": WEEK1_END.isoformat()}, headers=viewer)
    ).json()
    late = (
        await client.get("/time-machine/state", params={"as_of": WEEK2_END.isoformat()}, headers=viewer)
    ).json()
    assert early["health"] == "On track" and late["health"] == "At risk"
    assert {i["key"]: i["risk_level"] for i in late["items"]}[CHECKOUT] == "at_risk"
    assert len(early["items"]) < len(late["items"])  # week 2 items did not exist yet
    async with tenant_tx(fresh_tenant.id) as s:
        assert (
            await s.execute(text("SELECT id, key, risk_score FROM work_items ORDER BY key"))
        ).all() == before
        assert await s.scalar(text("SELECT count(*) FROM signals")) == signals_before
    naive = await client.get("/time-machine/state", params={"as_of": "2026-03-06T23:59:00"}, headers=viewer)
    assert naive.status_code == 422


async def test_replay_into_copy_is_owner_only_and_copies_people(
    client: httpx.AsyncClient, fresh_tenant: SeededTenant
) -> None:
    body = {"scenario": "tiffin_box", "timelines": ["week1"], "speed": 0}
    lead = await auth_headers(client, fresh_tenant, Role.lead)
    assert (await client.post("/time-machine/replays", json=body, headers=lead)).status_code == 403
    owner = await auth_headers(client, fresh_tenant, Role.owner)
    bad = await client.post("/time-machine/replays", json=body | {"timelines": ["nope"]}, headers=owner)
    assert bad.status_code == 400
    created = await client.post("/time-machine/replays", json=body, headers=owner)
    assert created.status_code == 202
    slug = created.json()["tenant_slug"]
    assert slug.startswith(fresh_tenant.slug) and "-replay-" in slug
    login = await client.post(
        "/auth/login",
        json={"tenant": slug, "email": fresh_tenant.users[Role.viewer].email, "password": PASSWORD},
    )
    assert login.status_code == 200
    copy = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (await client.get("/events", headers=copy)).json()["items"] == []  # no events copied


async def test_broadcaster_routes_notifications_by_tenant(
    fresh_tenant: SeededTenant, decoy: SeededTenant
) -> None:
    hub = Broadcaster()
    try:
        async with hub.subscribe(str(fresh_tenant.id)) as mine, hub.subscribe(str(decoy.id)) as theirs:
            await evaluate_tenant(fresh_tenant.id, datetime.now(UTC) + timedelta(days=1))
            assert await asyncio.wait_for(mine.get(), 5) == "update"
            await asyncio.sleep(0.2)
            assert theirs.empty()
    finally:
        await hub.close()


async def test_lost_listener_ends_streams_so_clients_reconnect(fresh_tenant: SeededTenant) -> None:
    """A Postgres restart must not leave open streams silently stale."""
    hub = Broadcaster()
    try:
        async with hub.subscribe(str(fresh_tenant.id)) as mine:
            async with global_tx() as s:  # what a database restart does to the LISTEN connection
                await s.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE query ILIKE 'LISTEN%' AND usename = 'daelys_app'"
                    )
                )
            assert await asyncio.wait_for(mine.get(), 5) == CLOSED
        async with hub.subscribe(str(fresh_tenant.id)) as again:  # the next subscriber listens again
            await evaluate_tenant(fresh_tenant.id, datetime.now(UTC) + timedelta(days=1))
            assert await asyncio.wait_for(again.get(), 5) == "update"
    finally:
        await hub.close()


async def test_stream_requires_auth(client: httpx.AsyncClient) -> None:
    assert (await client.get("/stream")).status_code == 401


async def test_project_graph(
    client: httpx.AsyncClient, fresh_tenant: SeededTenant, decoy: SeededTenant
) -> None:
    owner = await auth_headers(client, fresh_tenant, Role.owner)
    project = (await client.post("/projects", json={"name": "Subscriptions launch"}, headers=owner)).json()
    await replayed(fresh_tenant)  # the scenario stamps its items with the project of the same name
    graph = (await client.get(f"/projects/{project['id']}/graph", headers=owner)).json()
    keys = {n["key"] for n in graph["nodes"]}
    assert CHECKOUT in keys and "gh:tiffin-box/tiffin-box-app#21" in keys
    assert {"closes"} == {e["kind"] for e in graph["edges"]}
    outsider = await auth_headers(client, decoy, Role.owner)
    assert (await client.get(f"/projects/{project['id']}/graph", headers=outsider)).status_code == 404
