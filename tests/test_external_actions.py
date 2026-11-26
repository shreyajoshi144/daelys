"""Tier 3: `notify` against the real local Mailpit sandbox; `comment_on_pr` against a local stand-in for the
GitHub API (httpx.MockTransport). The stand-in proves our request flow and exactly-once logic, not GitHub
itself; the real end-to-end comment needs Shreya's GitHub App (sprint report, Needs Shreya)."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import text

from core.config import get_settings
from core.db import tenant_tx
from core.errors import NotConfigured
from core.security import Role
from tests.actions_helpers import approve_as, audit_actions, build_scenario, mailpit_api, proposal, propose
from tests.conftest import auth_headers
from workers import execute as executor


async def test_notify_sends_to_mailpit_once(client: httpx.AsyncClient, database: None) -> None:
    sc = await build_scenario()
    lead = await auth_headers(client, sc.tenant, Role.lead)
    member = sc.user_ids[Role.member]
    subject = f"Checkout at risk {sc.tenant.slug}"
    pid = await propose(
        sc,
        "notify",
        {
            "to_user_ids": [str(member)],
            "subject": subject,
            "body": "CI on the checkout PR is failing two days before the due date.",
        },
    )
    listed = (await client.get(f"/proposals/{pid}", headers=lead)).json()
    assert listed["preview"]["subject"] == subject and listed["preview"]["channel"].startswith("email")
    await approve_as(client, lead, pid)
    assert (await executor.execute(sc.tenant.id, pid)).status == "executed"
    assert (await executor.execute(sc.tenant.id, pid)).status == "executed"  # second run: no second email
    async with httpx.AsyncClient() as mp:
        found = (await mp.get(f"{mailpit_api()}/search", params={"query": f'subject:"{subject}"'})).json()
    assert found["messages_count"] == 1
    message = found["messages"][0]
    assert message["To"][0]["Address"] == sc.tenant.users[Role.member].email
    assert "execute.notify" in await audit_actions(sc, pid)


@pytest.fixture
def github_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = tmp_path / "app.pem"
    pem.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    settings = get_settings()
    monkeypatch.setattr(settings, "github_app_id", "12345")
    monkeypatch.setattr(settings, "github_app_private_key_path", str(pem))
    monkeypatch.setattr(settings, "github_api_url", "https://api.github.test")


class FakeGitHub:
    def __init__(self) -> None:
        self.comments: list[dict[str, Any]] = []
        self.posts = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "t"})
        if request.url.path == "/repos/tiffin-box/tiffin-box-app/issues/21/comments":
            if request.method == "POST":
                self.posts += 1
                body = json.loads(request.content)["body"]
                self.comments.append({"id": 900 + self.posts, "body": body})
                return httpx.Response(201, json=self.comments[-1])
            return httpx.Response(200, json=self.comments)
        return httpx.Response(404)


async def test_comment_on_pr_posts_exact_previewed_text_once(
    client: httpx.AsyncClient, github_app: None, monkeypatch: pytest.MonkeyPatch, database: None
) -> None:
    fake = FakeGitHub()
    real = executor.GitHubConnector
    monkeypatch.setattr(
        executor, "GitHubConnector", lambda: real(transport=httpx.MockTransport(fake.handler))
    )
    sc = await build_scenario()
    async with tenant_tx(sc.tenant.id) as s:
        await s.execute(
            text("INSERT INTO integrations (tenant_id, provider, external_id) VALUES (:t, 'github', :e)"),
            {"t": sc.tenant.id, "e": f"inst-{sc.tenant.slug}"},
        )
    lead = await auth_headers(client, sc.tenant, Role.lead)
    pid = await propose(
        sc, "comment_on_pr", {"body": "Payment webhook tests are still failing; is there an ETA?"}
    )
    preview = (await client.get(f"/proposals/{pid}", headers=lead)).json()["preview"]
    assert preview["pull_request"] == "gh:tiffin-box/tiffin-box-app#21"
    await approve_as(client, lead, pid)
    assert (await executor.execute(sc.tenant.id, pid)).status == "executed"
    assert fake.posts == 1 and fake.comments[0]["body"] == preview["text"]  # byte for byte

    # A crash after posting but before recording: the sweep finds the marker and finishes without re-posting.
    async with tenant_tx(sc.tenant.id) as s:
        await s.execute(
            text("""
            UPDATE proposals SET status = 'executing', updated_at = now() - interval '1 hour' WHERE id = :id
        """),
            {"id": pid},
        )
    assert (await executor.sweep(sc.tenant.id))["recovered"] == 1
    assert fake.posts == 1 and (await proposal(sc, pid)).status == "executed"


async def test_tier3_not_configured_stays_approved(client: httpx.AsyncClient, database: None) -> None:
    sc = await build_scenario()
    lead = await auth_headers(client, sc.tenant, Role.lead)
    pid = await propose(sc, "comment_on_pr", {"body": "Status?"})
    await approve_as(client, lead, pid)
    with pytest.raises(NotConfigured):
        await executor.execute(sc.tenant.id, pid)
    assert (await proposal(sc, pid)).status == "approved"


async def test_interrupted_notify_is_failed_not_resent(client: httpx.AsyncClient, database: None) -> None:
    sc = await build_scenario()
    lead = await auth_headers(client, sc.tenant, Role.lead)
    pid = await propose(
        sc, "notify", {"to_user_ids": [str(sc.user_ids[Role.member])], "subject": "x", "body": "y"}
    )
    await approve_as(client, lead, pid)
    async with tenant_tx(sc.tenant.id) as s:
        await s.execute(
            text("""
            UPDATE proposals SET status = 'executing', updated_at = now() - interval '1 hour' WHERE id = :id
        """),
            {"id": pid},
        )
    assert (await executor.sweep(sc.tenant.id))["failed"] == 1
    row = await proposal(sc, pid)
    assert row.status == "failed" and "not retried" in row.execution_result["detail"]
    assert (await executor.execute(sc.tenant.id, pid)).status == "failed"
