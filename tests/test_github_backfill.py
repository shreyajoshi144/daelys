"""REST backfill against a local stand-in for the GitHub API (httpx.MockTransport).

This exercises our request flow, pagination, retries and cursor logic. It does NOT prove the real GitHub API
works with our App; that needs real credentials (see the sprint report's "Needs Shreya").
"""

from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import text

from connectors.github import GitHubConnector
from core.config import get_settings
from core.db import tenant_tx
from core.errors import NotConfigured
from tests.conftest import SeededTenant, make_tenant
from workers.projector import project_tenant

REPO = "acme/app"
API = "https://api.github.test"


@pytest.fixture
def app_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> rsa.RSAPrivateKey:
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
    monkeypatch.setattr(settings, "github_api_url", API)
    return key


class FakeGitHub:
    def __init__(self, key: rsa.RSAPrivateKey) -> None:
        self.public_key = key.public_key()
        self.calls: list[str] = []
        self.flaky_once = True
        self.issues = [
            {
                "number": 1,
                "title": "Old bug",
                "state": "closed",
                "state_reason": "completed",
                "updated_at": "2026-03-01T10:00:00Z",
                "html_url": "u1",
                "assignee": None,
                "milestone": {"due_on": "2026-03-20T00:00:00Z"},
            },
            {
                "number": 2,
                "title": "Live issue",
                "state": "open",
                "updated_at": "2026-03-02T10:00:00Z",
                "html_url": "u2",
                "assignee": {"login": "dev"},
                "milestone": None,
            },
            {
                "number": 3,
                "title": "PR shadow",
                "state": "open",
                "updated_at": "2026-03-03T10:00:00Z",
                "pull_request": {},
            },
        ]
        self.pulls = [
            {
                "number": 3,
                "title": "Fix live issue",
                "state": "open",
                "draft": False,
                "merged_at": None,
                "updated_at": "2026-03-03T10:00:00Z",
                "html_url": "p3",
                "user": {"login": "dev"},
                "body": "Fixes #2",
            },
        ]

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(f"{request.method} {path}?{request.url.query.decode()}")
        if path.startswith("/app/installations/") and path.endswith("/access_tokens"):
            app_jwt = request.headers["authorization"].removeprefix("Bearer ")
            assert jwt.decode(app_jwt, self.public_key, algorithms=["RS256"])["iss"] == "12345"
            return httpx.Response(201, json={"token": "inst-token"})
        assert request.headers["authorization"] == "Bearer inst-token"
        if path == "/installation/repositories":
            return httpx.Response(200, json={"repositories": [{"full_name": REPO}]})
        if path == f"/repos/{REPO}/issues":
            if self.flaky_once:
                self.flaky_once = False
                return httpx.Response(502, headers={"retry-after": "0"})
            since = request.url.params.get("since")
            page = int(request.url.params.get("page", "1"))
            items = [i for i in self.issues if not since or i["updated_at"] >= since]
            chunk = items[(page - 1) * 2 : page * 2]
            headers = {}
            if page * 2 < len(items):
                headers["link"] = (
                    f'<{API}/repos/{REPO}/issues?page={page + 1}&since={since or ""}>; rel="next"'
                )
            return httpx.Response(200, json=chunk, headers=headers)
        if path == f"/repos/{REPO}/pulls":
            return httpx.Response(200, json=self.pulls)
        return httpx.Response(404)


async def test_backfill_ingests_resumes_and_projects(app_key: rsa.RSAPrivateKey, database: None) -> None:
    tenant: SeededTenant = await make_tenant(installation_id="777001")
    fake = FakeGitHub(app_key)
    connector = GitHubConnector(transport=httpx.MockTransport(fake.handler))

    assert await connector.backfill(tenant.id) == 3  # issues 1, 2 and PR 3 (issue-list shadow of PR skipped)
    await project_tenant(tenant.id)
    async with tenant_tx(tenant.id) as s:
        rows = {
            r.key: r
            for r in (await s.execute(text("SELECT key, kind, status, due_at FROM work_items"))).all()
        }
        cursor = await s.scalar(
            text("SELECT position->>'since' FROM connector_cursors WHERE connector = :c"),
            {"c": f"github:backfill:{REPO}"},
        )
        edges = await s.scalar(text("SELECT count(*) FROM edges"))
    assert rows[f"gh:{REPO}#1"].status == "done" and rows[f"gh:{REPO}#1"].due_at is not None
    assert rows[f"gh:{REPO}#3"].kind == "pr" and rows[f"gh:{REPO}#3"].status == "in_review"
    assert cursor == "2026-03-03T10:00:00Z" and edges == 1
    assert sum("issues" in c for c in fake.calls) == 3  # one 502 retry + two pages

    fake.calls.clear()
    assert await connector.backfill(tenant.id) == 0  # resumed from cursor; nothing new
    assert any("since=2026-03-03T10" in c for c in fake.calls)


async def test_backfill_not_configured(database: None, monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = await make_tenant()
    with pytest.raises(NotConfigured):
        await GitHubConnector().backfill(tenant.id)
    monkeypatch.setattr(get_settings(), "github_app_id", "1")
    monkeypatch.setattr(get_settings(), "github_app_private_key_path", "/nonexistent")
    with pytest.raises(NotConfigured, match="no GitHub installation"):
        await GitHubConnector().backfill(tenant.id)


async def test_persistent_server_errors_raise(
    app_key: rsa.RSAPrivateKey, database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = await make_tenant(installation_id="777002")
    monkeypatch.setattr("connectors.github.asyncio.sleep", _no_sleep)
    always_down = httpx.MockTransport(lambda r: httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await GitHubConnector(transport=always_down).backfill(tenant.id)


async def _no_sleep(_: float) -> Any:
    return None
