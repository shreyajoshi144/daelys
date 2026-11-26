"""Ask Daelys (role + tenant filtering, abstention), duplicates (suggest only), summaries (cited, versioned),
comments, calendar upload and the demo reset."""

from datetime import date
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from agents.writers import NO_EVIDENCE, DuplicateVerdict, Sentences
from core.db import tenant_tx
from core.events import NewEvent, append_events
from core.insights import ask, detect_duplicates, generate_summary
from core.llm import LLM, Completion, Route
from core.retrieval import search_documents
from core.security import Role
from tests.actions_helpers import build_scenario
from tests.conftest import SeededTenant, auth_headers
from workers.projector import project_tenant

SECRET = "The AutoPay launch is blocked on the acquirer contract renewal."


class Scripted:
    name = "scripted"

    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.users: list[str] = []

    async def complete(
        self, model: str, system: str, user: str, output_type: Any, max_tokens: int
    ) -> Completion[Any]:
        self.users.append(user)
        value = self.reply(user) if callable(self.reply) else self.reply
        return Completion(parsed=value, input_tokens=10, output_tokens=5)


def llm(model: Scripted) -> LLM:
    return LLM([Route("scripted", "m")], {"scripted": model})


async def lead_only_doc(client: httpx.AsyncClient, tenant: SeededTenant, project_id: Any) -> str:
    lead = await auth_headers(client, tenant, Role.lead)
    resp = await client.post(
        f"/projects/{project_id}/briefs", json={"title": "Board notes", "content": SECRET}, headers=lead
    )
    async with tenant_tx(tenant.id) as s:
        await s.execute(
            text("UPDATE artifacts SET min_role = 'lead' WHERE id = :a"), {"a": resp.json()["artifact_id"]}
        )
        return str(
            await s.scalar(
                text("SELECT id FROM chunks WHERE artifact_id = :a"), {"a": resp.json()["artifact_id"]}
            )
        )


async def test_role_filter_happens_before_ranking(client: httpx.AsyncClient, database: None) -> None:
    sc = await build_scenario()
    chunk = await lead_only_doc(client, sc.tenant, sc.project_id)
    async with tenant_tx(sc.tenant.id) as s:
        as_viewer = await search_documents(s, "what is blocking the AutoPay launch", Role.viewer)
        as_lead = await search_documents(s, "what is blocking the AutoPay launch", Role.lead)
    assert chunk not in {str(r["id"]) for r in as_viewer.rows}
    assert chunk in {str(r["id"]) for r in as_lead.rows} and as_lead.mode == "lexical"


async def test_ask_never_leaks_a_restricted_doc(client: httpx.AsyncClient, database: None) -> None:
    sc = await build_scenario()
    chunk = await lead_only_doc(client, sc.tenant, sc.project_id)
    # A model that tries to cite the restricted chunk anyway: the checker drops it, so nothing is shown.
    model = Scripted(Sentences(sentences=[f"AutoPay is blocked on the acquirer contract [doc:{chunk}]."]))
    viewer_answer = await ask(llm(model), sc.tenant.id, Role.viewer, "What is blocking the AutoPay launch?")
    assert viewer_answer["text"] == NO_EVIDENCE and viewer_answer["sources"] == []
    assert all(SECRET not in u for u in model.users)  # it never even reached the model for a viewer
    lead_answer = await ask(llm(model), sc.tenant.id, Role.lead, "What is blocking the AutoPay launch?")
    assert lead_answer["enough_evidence"] and lead_answer["sources"][0]["id"] == chunk


async def test_other_tenant_and_unanswerable_questions(
    client: httpx.AsyncClient, decoy: SeededTenant, database: None
) -> None:
    sc = await build_scenario()
    await lead_only_doc(client, sc.tenant, sc.project_id)
    model = Scripted(Sentences(sentences=["It is blocked on the contract [evt:1]."]))
    other = await ask(llm(model), decoy.id, Role.owner, "What is blocking the AutoPay launch?")
    assert other["text"] == NO_EVIDENCE and model.users == []  # no evidence, so no model call at all
    abstain = Scripted(Sentences(sentences=[NO_EVIDENCE]))
    result = await ask(
        llm(abstain), sc.tenant.id, Role.owner, "What is the CEO's favourite colour in checkout?"
    )
    assert result["text"] == NO_EVIDENCE and result["enough_evidence"] is False


async def test_ask_endpoint_needs_key(client: httpx.AsyncClient, database: None) -> None:
    sc = await build_scenario()
    viewer = await auth_headers(client, sc.tenant, Role.viewer)
    resp = await client.post("/ask", json={"question": "What is blocking checkout?"}, headers=viewer)
    assert resp.status_code == 503 and resp.json()["error"] == "not_configured"


async def test_duplicates_are_suggested_never_merged(database: None) -> None:
    sc = await build_scenario()
    async with tenant_tx(sc.tenant.id) as s:
        await s.execute(text("UPDATE work_items SET duplicates_checked_at = now()"))
        await append_events(
            s,
            sc.tenant.id,
            [
                NewEvent(
                    source="test",
                    type="plan.item_created",
                    idempotency_key=f"dup-{n}",
                    occurred_at=sc_time(),
                    subject_key=f"task:dup-{n}",
                    payload={
                        "fields": {"kind": "task", "title": title, "status": "open", "lane": "Marketing"}
                    },
                )
                for n, title in enumerate(
                    ["Write the launch FAQ page", "Write launch FAQ page", "Write the pricing FAQ page"]
                )
            ],
        )
    await project_tenant(sc.tenant.id)
    before = await items(sc)

    def judge(user: str) -> DuplicateVerdict:
        same = user.count("launch FAQ") == 2  # only the two launch-FAQ titles are the same work
        return DuplicateVerdict(duplicate=same, reason="same deliverable" if same else "different page")

    suggested = await detect_duplicates(llm(Scripted(judge)), sc.tenant.id)
    async with tenant_tx(sc.tenant.id) as s:
        pairs = (
            await s.execute(
                text("""
            SELECT a.title AS a, b.title AS b FROM duplicate_suggestions d
            JOIN work_items a ON a.id = d.item_id JOIN work_items b ON b.id = d.duplicate_of
        """)
            )
        ).all()
    assert suggested >= 1 and all("pricing" not in p.a + p.b for p in pairs)
    assert {frozenset((p.a, p.b)) for p in pairs} == {
        frozenset(("Write the launch FAQ page", "Write launch FAQ page"))
    }
    assert await items(sc) == before  # nothing merged or changed


def sc_time() -> Any:
    from datetime import UTC, datetime

    return datetime(2026, 3, 12, 9, 0, tzinfo=UTC)


async def items(sc: Any) -> list[Any]:
    async with tenant_tx(sc.tenant.id) as s:
        return list((await s.execute(text("SELECT key, title, status FROM work_items ORDER BY key"))).all())


async def test_summary_is_cited_and_versioned(client: httpx.AsyncClient, database: None) -> None:
    sc = await build_scenario()

    def writer(user: str) -> Sentences:
        first = user.split("[evt:")[1].split("]")[0]
        return Sentences(sentences=[f"Checkout CI kept failing [evt:{first}].", "Morale is low."])

    model = Scripted(writer)
    one = await generate_summary(
        llm(model), sc.tenant.id, sc.project_id, "Engineering", date(2026, 3, 9), None
    )
    two = await generate_summary(  # a Wednesday: normalised to the same Monday-to-Monday week
        llm(model), sc.tenant.id, sc.project_id, "Engineering", date(2026, 3, 11), None
    )
    assert (one["version"], two["version"]) == (1, 2) and one["dropped"] == 1
    assert len(one["sentences"]) == 1 and one["sentences"][0]["event_ids"]
    async with tenant_tx(sc.tenant.id) as s:
        runs = await s.scalar(text("SELECT count(*) FROM agent_runs WHERE agent = 'summarizer'"))
    assert runs == 2
    lead = await auth_headers(client, sc.tenant, Role.lead)
    listed = (await client.get("/summaries", params={"project_id": str(sc.project_id)}, headers=lead)).json()
    assert [(s["version"], s["week_start"]) for s in listed] == [(2, "2026-03-09"), (1, "2026-03-09")]


async def test_comments(client: httpx.AsyncClient, database: None) -> None:
    sc = await build_scenario()
    member = await auth_headers(client, sc.tenant, Role.member)
    viewer = await auth_headers(client, sc.tenant, Role.viewer)
    url = f"/work-items/{sc.checkout_id}/comments"
    assert (await client.post(url, json={"body": "On it"}, headers=viewer)).status_code == 403
    assert (await client.post(url, json={"body": "On it"}, headers=member)).status_code == 201
    assert [c["body"] for c in (await client.get(url, headers=viewer)).json()] == ["On it"]


ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:launch-go-no-go@example
DTSTART;TZID=Asia/Kolkata:20260320T170000
SUMMARY:Launch go/no-go\\, final c
 all
SEQUENCE:1
END:VEVENT
BEGIN:VEVENT
UID:holi@example
DTSTART;VALUE=DATE:20260304
SUMMARY:Holi
END:VEVENT
BEGIN:VEVENT
UID:old@example
DTSTART:20260301T090000Z
SUMMARY:Cancelled sync
STATUS:CANCELLED
END:VEVENT
END:VCALENDAR
"""


async def test_calendar_upload(client: httpx.AsyncClient, database: None) -> None:
    sc = await build_scenario()
    lead = await auth_headers(client, sc.tenant, Role.lead)
    first = (await client.post("/calendar/ics", content=ICS, headers=lead)).json()
    again = (await client.post("/calendar/ics", content=ICS, headers=lead)).json()
    assert first == {"events": 3, "inserted": 3, "duplicates": 0} and again["duplicates"] == 3
    async with tenant_tx(sc.tenant.id) as s:
        rows = {
            r.payload["uid"]: r
            for r in await s.execute(
                text(
                    "SELECT type, occurred_at, payload FROM events "
                    "WHERE source = 'calendar' AND payload ? 'uid'"
                )
            )
        }
    assert rows["launch-go-no-go@example"].payload["title"] == "Launch go/no-go, final call"
    assert rows["launch-go-no-go@example"].occurred_at.isoformat() == "2026-03-20T11:30:00+00:00"
    assert rows["old@example"].type == "calendar.event_cancelled"
    bad = await client.post(
        "/calendar/ics", content="BEGIN:VEVENT\nUID:x\nDTSTART:20260101T100000\nEND:VEVENT", headers=lead
    )
    assert bad.status_code == 422


async def test_demo_reset(client: httpx.AsyncClient, database: None) -> None:
    sc = await build_scenario()
    owner = await auth_headers(client, sc.tenant, Role.owner)
    assert (await client.post("/admin/reset-scenario", headers=owner)).status_code == 403  # not a demo tenant
    async with tenant_tx(sc.tenant.id) as s:
        await s.execute(text("UPDATE tenants SET is_demo = true"))
        before = await s.scalar(text("SELECT count(*) FROM events"))
    lead = await auth_headers(client, sc.tenant, Role.lead)
    assert (await client.post("/admin/reset-scenario", headers=lead)).status_code == 403
    resp = await client.post("/admin/reset-scenario", headers=owner)
    assert resp.status_code == 202 and resp.json()["replayed_events"] == 30
    async with tenant_tx(sc.tenant.id) as s:
        after = await s.scalar(text("SELECT count(*) FROM events"))
        health = await s.scalar(text("SELECT count(*) FROM work_items WHERE risk_level = 'at_risk'"))
    assert before > 30 and after == 30 and health == 0  # back to the week-1 baseline (On track)


@pytest.mark.parametrize("role", [Role.viewer, Role.member])
async def test_reset_requires_owner(client: httpx.AsyncClient, role: Role, database: None) -> None:
    sc = await build_scenario()
    headers = await auth_headers(client, sc.tenant, role)
    assert (await client.post("/admin/reset-scenario", headers=headers)).status_code == 403
