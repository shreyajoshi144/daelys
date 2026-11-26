import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from agents.plan_schema import Dropped, Plan, PlanResult, RawStats
from core.db import tenant_tx
from core.plans import save_draft
from core.security import Role
from tests.conftest import PASSWORD, SeededTenant, auth_headers
from workers.projector import project_tenant

BRIEF = (
    "Build checkout with Razorpay.\n\nSend launch emails after checkout works.\n\n"
    "Launch 20 March or 27 March?\n"
)


def pdf_with_text(line: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({line}) Tj ET".encode()
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length %d>>stream\n" % len(stream) + stream + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer <</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return out


async def create_project(client: httpx.AsyncClient, tenant: SeededTenant, name: str = "Launch") -> str:
    resp = await client.post(
        "/projects", json={"name": name}, headers=await auth_headers(client, tenant, Role.lead)
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def test_project_crud_rbac_and_isolation(
    client: httpx.AsyncClient, fresh_tenant: SeededTenant, decoy: SeededTenant
) -> None:
    project_id = await create_project(client, fresh_tenant)
    viewer = await auth_headers(client, fresh_tenant, Role.viewer)
    assert (await client.post("/projects", json={"name": "x"}, headers=viewer)).status_code == 403
    assert [p["id"] for p in (await client.get("/projects", headers=viewer)).json()] == [project_id]
    assert (await client.get(f"/projects/{project_id}", headers=viewer)).status_code == 200
    outsider = await auth_headers(client, decoy, Role.owner)
    for path in (
        f"/projects/{project_id}",
        f"/projects/{project_id}/briefs",
        f"/projects/{project_id}/plan/drafts",
    ):
        assert (await client.get(path, headers=outsider)).status_code == 404, path
    resp = await client.post(
        f"/projects/{project_id}/briefs", json={"title": "t", "content": "x" * 20}, headers=outsider
    )
    assert resp.status_code == 404


async def test_brief_paste_and_uploads(client: httpx.AsyncClient, fresh_tenant: SeededTenant) -> None:
    project_id = await create_project(client, fresh_tenant)
    lead = await auth_headers(client, fresh_tenant, Role.lead)
    pasted = await client.post(
        f"/projects/{project_id}/briefs", json={"title": "Brief", "content": BRIEF}, headers=lead
    )
    assert pasted.status_code == 201 and pasted.json()["chunks"] == 1 and pasted.json()["embedded"] is False

    md = await client.post(
        f"/projects/{project_id}/briefs/upload?title=b.md",
        content=BRIEF.encode(),
        headers=lead | {"Content-Type": "text/markdown"},
    )
    pdf = await client.post(
        f"/projects/{project_id}/briefs/upload?title=b.pdf",
        content=pdf_with_text("Hello PDF brief"),
        headers=lead | {"Content-Type": "application/pdf"},
    )
    bad = await client.post(
        f"/projects/{project_id}/briefs/upload?title=x.png",
        content=b"\x89PNG",
        headers=lead | {"Content-Type": "image/png"},
    )
    assert (md.status_code, pdf.status_code, bad.status_code) == (201, 201, 415)
    detail = await client.get(f"/projects/{project_id}/briefs/{pdf.json()['artifact_id']}", headers=lead)
    assert "Hello PDF brief" in detail.json()["content"]
    async with tenant_tx(fresh_tenant.id) as s:
        chunks = (
            await s.execute(
                text("""
            SELECT c.char_start, c.char_end, c.content, a.content AS full FROM chunks c
            JOIN artifacts a ON a.id = c.artifact_id WHERE a.id = :a
        """),
                {"a": pasted.json()["artifact_id"]},
            )
        ).all()
    assert all(c.full[c.char_start : c.char_end] == c.content for c in chunks)


async def test_generate_plan_without_key_is_not_configured(
    client: httpx.AsyncClient, fresh_tenant: SeededTenant
) -> None:
    project_id = await create_project(client, fresh_tenant)
    lead = await auth_headers(client, fresh_tenant, Role.lead)
    brief = await client.post(
        f"/projects/{project_id}/briefs", json={"title": "B", "content": BRIEF}, headers=lead
    )
    resp = await client.post(
        f"/projects/{project_id}/plan", json={"artifact_id": brief.json()["artifact_id"]}, headers=lead
    )
    assert resp.status_code == 503 and resp.json()["error"] == "not_configured"
    missing = await client.post(
        f"/projects/{project_id}/plan", json={"artifact_id": str(uuid.uuid4())}, headers=lead
    )
    assert missing.status_code == 404


def sample_result() -> PlanResult:
    plan = Plan.model_validate(
        {
            "items": [
                {
                    "local_id": "i1",
                    "title": "Build checkout",
                    "lane": "Engineering",
                    "owner_role": "backend engineer",
                    "estimate_days": 5,
                    "depends_on": [],
                    "source_span": {"start": 0, "end": 29},
                },
                {
                    "local_id": "i2",
                    "title": "Launch emails",
                    "lane": "Marketing",
                    "owner_role": "marketer",
                    "estimate_days": 2,
                    "depends_on": ["i1"],
                    "source_span": {"start": 31, "end": 70},
                },
            ],
            "open_questions": [{"text": "Which launch date?", "source_spans": [{"start": 72, "end": 80}]}],
        }
    )
    return PlanResult(
        plan=plan,
        dropped=[Dropped(kind="item", ref="i3", reason="test")],
        repaired=False,
        raw=RawStats(items=3, uncited_items=1, had_cycle=False),
        agent_run_ids=[],
    )


async def test_accept_plan_writes_events_and_rebuilds_work_items(
    client: httpx.AsyncClient, fresh_tenant: SeededTenant, decoy: SeededTenant
) -> None:
    project_id = await create_project(client, fresh_tenant)
    lead = await auth_headers(client, fresh_tenant, Role.lead)
    brief = await client.post(
        f"/projects/{project_id}/briefs", json={"title": "B", "content": BRIEF}, headers=lead
    )
    async with tenant_tx(fresh_tenant.id) as s:
        lead_id = await s.scalar(text("SELECT user_id FROM memberships WHERE role = 'lead'"))
        draft_id = await save_draft(
            s,
            fresh_tenant.id,
            uuid.UUID(project_id),
            uuid.UUID(brief.json()["artifact_id"]),
            sample_result(),
            lead_id,
        )

    draft = await client.get(f"/projects/{project_id}/plan/drafts/{draft_id}", headers=lead)
    assert draft.json()["status"] == "draft" and len(draft.json()["plan"]["items"]) == 2
    viewer = await auth_headers(client, fresh_tenant, Role.viewer)
    body = {"draft_id": str(draft_id)}
    assert (
        await client.post(f"/projects/{project_id}/plan/accept", json=body, headers=viewer)
    ).status_code == 403
    outsider = await auth_headers(client, decoy, Role.owner)
    assert (
        await client.post(f"/projects/{project_id}/plan/accept", json=body, headers=outsider)
    ).status_code == 404

    accepted = await client.post(f"/projects/{project_id}/plan/accept", json=body, headers=lead)
    assert accepted.json() == {"items": 2, "edges": 1, "inserted_events": 3}
    again = await client.post(f"/projects/{project_id}/plan/accept", json=body, headers=lead)
    assert again.status_code == 409

    items = (await client.get(f"/work-items?project_id={project_id}", headers=viewer)).json()["items"]
    by_title = {i["title"]: i for i in items}
    assert set(by_title) == {"Build checkout", "Launch emails"}
    assert (
        by_title["Launch emails"]["lane"] == "Marketing" and by_title["Build checkout"]["estimate_days"] == 5
    )
    assert by_title["Build checkout"]["source_span"] == {"start": 0, "end": 29}
    async with tenant_tx(fresh_tenant.id) as s:
        types = sorted(await s.scalars(text("SELECT type FROM events WHERE source = 'plan'")))
        edge = (
            await s.execute(
                text("""
            SELECT src.title AS src, dst.title AS dst FROM edges e
            JOIN work_items src ON src.id = e.src_id JOIN work_items dst ON dst.id = e.dst_id
            WHERE e.kind = 'depends_on'
        """)
            )
        ).one()
    assert types == ["plan.edge_created", "plan.item_created", "plan.item_created"]
    assert (edge.src, edge.dst) == ("Launch emails", "Build checkout")

    # Rule 1: work_items are a projection. Throw them away, replay the log, get the same rows back.
    snapshot = (
        "SELECT key, title, lane, status, owner_role, estimate_days, project_id, source_span"
        " FROM work_items ORDER BY key"
    )
    async with tenant_tx(fresh_tenant.id) as s:
        before = (await s.execute(text(snapshot))).all()
        await s.execute(text("DELETE FROM work_items"))
        await s.execute(text("DELETE FROM connector_cursors WHERE connector = 'projector'"))
    await project_tenant(fresh_tenant.id)
    async with tenant_tx(fresh_tenant.id) as s:
        assert (await s.execute(text(snapshot))).all() == before
        assert await s.scalar(text("SELECT count(*) FROM edges WHERE kind = 'depends_on'")) == 1


async def test_cookie_session_requires_csrf_header_for_writes(
    client: httpx.AsyncClient, fresh_tenant: SeededTenant
) -> None:
    creds = {"tenant": fresh_tenant.slug, "email": fresh_tenant.users[Role.lead].email, "password": PASSWORD}
    login = await client.post("/auth/session", json=creds)
    assert login.status_code == 204
    cookie = login.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "daelys_session=" in cookie
    me = await client.get("/auth/me")
    assert me.status_code == 200 and me.json()["role"] == "lead"
    assert (await client.post("/projects", json={"name": "x"})).status_code == 403
    assert (
        await client.post("/projects", json={"name": "x"}, headers={"X-Daelys-CSRF": "1"})
    ).status_code == 201
    assert (await client.delete("/auth/session", headers={"X-Daelys-CSRF": "1"})).status_code == 204
    client.cookies.clear()
    assert (await client.get("/auth/me")).status_code == 401


async def test_web_app_is_served_with_strict_csp(client: httpx.AsyncClient) -> None:
    index = await client.get("/")
    assert index.status_code == 200 and '<div id="root">' in index.text
    assert "script-src 'self'" in index.headers["content-security-policy"]
    assert (await client.get("/vendor/react.production.min.js")).status_code == 200
    app_js = await client.get("/js/app.js")
    assert app_js.headers["content-type"].startswith("text/javascript")
    assert app_js.headers["cache-control"] == "no-cache" and index.headers["cache-control"] == "no-cache"
    assert (await client.get("/health")).json()["status"] == "ok"


async def test_generate_plan_route_saves_a_draft(
    client: httpx.AsyncClient, fresh_tenant: SeededTenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agents.plan_schema import LLMPlan
    from api.routes import projects
    from core.llm import LLM, Completion, Route

    answer = LLMPlan.model_validate(
        {
            "items": [
                {
                    "local_id": "a",
                    "title": "Checkout",
                    "lane": "Engineering",
                    "owner_role": "eng",
                    "estimate_days": 4,
                    "depends_on": [],
                    "source_quote": "Build checkout with Razorpay.",
                }
            ],
            "open_questions": [{"text": "Which date?", "source_quotes": ["20 March", "27 March"]}],
        }
    )

    class Scripted:
        name = "scripted"

        async def complete(
            self, model: str, system: str, user: str, output_type: Any, max_tokens: int
        ) -> Completion[Any]:
            return Completion(parsed=answer, input_tokens=5, output_tokens=5)

    monkeypatch.setattr(projects, "get_llm", lambda: LLM([Route("scripted", "m")], {"scripted": Scripted()}))
    project_id = await create_project(client, fresh_tenant)
    lead = await auth_headers(client, fresh_tenant, Role.lead)
    brief = await client.post(
        f"/projects/{project_id}/briefs", json={"title": "B", "content": BRIEF}, headers=lead
    )
    resp = await client.post(
        f"/projects/{project_id}/plan", json={"artifact_id": brief.json()["artifact_id"]}, headers=lead
    )
    assert resp.status_code == 201, resp.text
    draft = resp.json()
    assert draft["status"] == "draft" and draft["repaired"] is False
    assert [i["title"] for i in draft["plan"]["items"]] == ["Checkout"]
    span = draft["plan"]["open_questions"][0]["source_spans"][0]
    assert BRIEF[span["start"] : span["end"]] == "20 March"
    listed = await client.get(f"/projects/{project_id}/plan/drafts", headers=lead)
    assert [d["id"] for d in listed.json()] == [draft["id"]]
    async with tenant_tx(fresh_tenant.id) as s:
        assert await s.scalar(text("SELECT count(*) FROM agent_runs WHERE agent = 'planner'")) == 1
