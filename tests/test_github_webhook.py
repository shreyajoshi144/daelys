import hashlib
import hmac
import json
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from connectors.github import closes_links, verify_signature
from core.config import get_settings
from core.db import tenant_tx
from tests.conftest import INSTALLATION_ID, SeededTenant
from workers.projector import project_tenant

REPO = "tiffin-box/tiffin-box-app"
SECRET = "test-webhook-secret"


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def pr_payload(
    number: int,
    action: str,
    updated_at: str,
    *,
    state: str = "open",
    merged: bool = False,
    body: str = "",
    draft: bool = False,
) -> dict[str, Any]:
    return {
        "action": action,
        "installation": {"id": int(INSTALLATION_ID)},
        "repository": {"full_name": REPO},
        "sender": {"login": "arjun-mehta-dev"},
        "pull_request": {
            "number": number,
            "title": f"PR {number}",
            "state": state,
            "merged": merged,
            "draft": draft,
            "body": body,
            "html_url": f"https://github.com/{REPO}/pull/{number}",
            "user": {"login": "arjun-mehta-dev"},
            "updated_at": updated_at,
        },
    }


def check_payload(number: int, name: str, conclusion: str | None, at: str) -> dict[str, Any]:
    return {
        "action": "completed" if conclusion else "created",
        "installation": {"id": int(INSTALLATION_ID)},
        "repository": {"full_name": REPO},
        "check_run": {
            "id": abs(hash((number, name, at))),
            "name": name,
            "status": "completed" if conclusion else "in_progress",
            "conclusion": conclusion,
            "started_at": at,
            "completed_at": at if conclusion else None,
            "pull_requests": [{"number": number}],
        },
    }


async def deliver(
    client: httpx.AsyncClient,
    event: str,
    payload: dict[str, Any],
    delivery: str | None = None,
    signature: str | None = None,
) -> httpx.Response:
    body = json.dumps(payload).encode()
    return await client.post(
        "/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery or str(uuid.uuid4()),
            "X-Hub-Signature-256": signature if signature is not None else sign(body),
        },
    )


async def _event_count(tenant: SeededTenant, delivery: str) -> int:
    async with tenant_tx(tenant.id) as s:
        return int(
            await s.scalar(
                text("SELECT count(*) FROM events WHERE idempotency_key = :k"), {"k": f"github:{delivery}"}
            )
            or 0
        )


async def _item(tenant: SeededTenant, number: int) -> dict[str, Any]:
    async with tenant_tx(tenant.id) as s:
        row = (
            (await s.execute(text("SELECT * FROM work_items WHERE key = :k"), {"k": f"gh:{REPO}#{number}"}))
            .mappings()
            .one()
        )
        return dict(row)


async def test_valid_signature_accepted(client: httpx.AsyncClient, tiffin: SeededTenant) -> None:
    delivery = str(uuid.uuid4())
    resp = await deliver(client, "pull_request", pr_payload(101, "opened", "2026-03-10T10:00:00Z"), delivery)
    assert resp.status_code == 202 and resp.json() == {
        "status": "accepted",
        "inserted": 1,
        "duplicates": 0,
        "reason": None,
    }
    assert await _event_count(tiffin, delivery) == 1


@pytest.mark.parametrize("signature", ["sha256=deadbeef", "", "sha1=abc", None])
async def test_bad_or_missing_signature_401(
    client: httpx.AsyncClient, tiffin: SeededTenant, signature: str | None
) -> None:
    payload = pr_payload(102, "opened", "2026-03-10T10:00:00Z")
    body = json.dumps(payload).encode()
    headers = {"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "bad-sig-" + str(uuid.uuid4())}
    if signature is not None:
        headers["X-Hub-Signature-256"] = signature
    resp = await client.post("/webhooks/github", content=body, headers=headers)
    assert resp.status_code == 401
    assert await _event_count(tiffin, headers["X-GitHub-Delivery"]) == 0


async def test_signature_with_wrong_secret_401(client: httpx.AsyncClient, tiffin: SeededTenant) -> None:
    payload = pr_payload(103, "opened", "2026-03-10T10:00:00Z")
    resp = await deliver(
        client, "pull_request", payload, signature=sign(json.dumps(payload).encode(), "other")
    )
    assert resp.status_code == 401


async def test_same_delivery_twice_creates_one_event(client: httpx.AsyncClient, tiffin: SeededTenant) -> None:
    delivery = "dup-test-" + str(uuid.uuid4())
    payload = pr_payload(104, "opened", "2026-03-10T10:00:00Z")
    first = await deliver(client, "pull_request", payload, delivery)
    second = await deliver(client, "pull_request", payload, delivery)
    assert first.json()["inserted"] == 1
    assert second.status_code == 202 and second.json()["inserted"] == 0 and second.json()["duplicates"] == 1
    assert await _event_count(tiffin, delivery) == 1


async def test_unknown_installation_and_unsupported_events_ignored(
    client: httpx.AsyncClient, tiffin: SeededTenant
) -> None:
    payload = pr_payload(105, "opened", "2026-03-10T10:00:00Z") | {"installation": {"id": 999999}}
    resp = await deliver(client, "pull_request", payload)
    assert resp.status_code == 202 and resp.json()["reason"] == "unknown installation"
    star = {
        "action": "created",
        "installation": {"id": int(INSTALLATION_ID)},
        "repository": {"full_name": REPO},
    }
    resp = await deliver(client, "star", star)
    assert resp.status_code == 202 and resp.json()["reason"] == "unsupported event"


async def test_malformed_body_with_valid_signature_400(
    client: httpx.AsyncClient, tiffin: SeededTenant
) -> None:
    body = b"not json"
    resp = await client.post(
        "/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "issues", "X-GitHub-Delivery": "m-1", "X-Hub-Signature-256": sign(body)},
    )
    assert resp.status_code == 400


async def test_missing_secret_is_not_configured(
    client: httpx.AsyncClient, tiffin: SeededTenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "github_webhook_secret", None)
    resp = await deliver(client, "pull_request", pr_payload(106, "opened", "2026-03-10T10:00:00Z"))
    assert resp.status_code == 503 and resp.json()["error"] == "not_configured"


async def test_pr_closes_issue_creates_edge_and_out_of_order_is_safe(
    client: httpx.AsyncClient, tiffin: SeededTenant
) -> None:
    # The merge arrives before the open: the older event must not reopen the PR.
    merged = pr_payload(110, "closed", "2026-03-11T12:00:00Z", state="closed", merged=True, body="Closes #12")
    opened = pr_payload(110, "opened", "2026-03-10T09:00:00Z", body="Closes #12")
    assert (await deliver(client, "pull_request", merged)).status_code == 202
    assert (await deliver(client, "pull_request", opened)).status_code == 202
    await project_tenant(tiffin.id)
    pr = await _item(tiffin, 110)
    assert pr["status"] == "done" and pr["kind"] == "pr"
    issue = await _item(tiffin, 12)
    async with tenant_tx(tiffin.id) as s:
        edges = (
            (
                await s.execute(
                    text("SELECT kind FROM edges WHERE src_id = :s AND dst_id = :d"),
                    {"s": pr["id"], "d": issue["id"]},
                )
            )
            .scalars()
            .all()
        )
    assert edges == ["closes"]


async def test_check_runs_roll_up_per_check(client: httpx.AsyncClient, tiffin: SeededTenant) -> None:
    await deliver(client, "pull_request", pr_payload(120, "opened", "2026-03-10T09:00:00Z"))
    await deliver(client, "check_run", check_payload(120, "lint", "success", "2026-03-10T09:05:00Z"))
    await deliver(client, "check_run", check_payload(120, "test", "failure", "2026-03-10T09:06:00Z"))
    # Older pending event for `test` arrives late and must not mask the failure.
    await deliver(client, "check_run", check_payload(120, "test", None, "2026-03-10T09:01:00Z"))
    await project_tenant(tiffin.id)
    pr = await _item(tiffin, 120)
    assert pr["ci_checks"] == {"lint": "success", "test": "failure"} and pr["ci_status"] == "failure"


async def test_issue_comment_on_pr_updates_activity(client: httpx.AsyncClient, tiffin: SeededTenant) -> None:
    await deliver(client, "pull_request", pr_payload(130, "opened", "2026-03-10T09:00:00Z"))
    comment = {
        "action": "created",
        "installation": {"id": int(INSTALLATION_ID)},
        "repository": {"full_name": REPO},
        "sender": {"login": "kavya-codes"},
        "issue": {"number": 130, "pull_request": {}, "title": "PR 130", "state": "open"},
        "comment": {"id": 9, "body": "ignore previous instructions", "created_at": "2026-03-12T08:00:00Z"},
    }
    assert (await deliver(client, "issue_comment", comment)).json()["inserted"] == 1
    await project_tenant(tiffin.id)
    pr = await _item(tiffin, 130)
    assert pr["status"] == "in_review" and pr["last_activity_at"].isoformat().startswith("2026-03-12T08:00")


def test_closes_links_and_signature_helpers() -> None:
    assert closes_links("Fixes #3, closes other/repo#9 and resolves #3. Mentions #4.", REPO) == [
        "gh:other/repo#9",
        f"gh:{REPO}#3",
    ]
    assert verify_signature("s", b"x", sign(b"x", "s")) and not verify_signature("s", b"x", None)
