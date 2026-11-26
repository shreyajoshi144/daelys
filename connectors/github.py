"""GitHub App connector: signed webhooks plus REST backfill with an installation token."""

import asyncio
import hashlib
import hmac
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import jwt
from sqlalchemy import text

from connectors.base import Connector, WebhookResult, get_cursor, set_cursor
from core.config import get_settings
from core.db import resolve_integration, tenant_tx
from core.errors import NotConfigured
from core.events import NewEvent, append_events

log = logging.getLogger(__name__)

SOURCE = "github"
BODY_LIMIT = 4000
MAX_PAGES = 20
RETRY_STATUSES = {429, 502, 503, 504}
FAILED_CONCLUSIONS = {"failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale"}
_CLOSES = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+(?:([\w.-]+/[\w.-]+))?#(\d+)", re.I)


class InvalidSignature(Exception):
    pass


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def item_key(repo: str, number: int) -> str:
    return f"gh:{repo}#{number}"


def parse_item_key(key: str) -> tuple[str, int]:
    """Inverse of item_key: "gh:owner/repo#12" -> ("owner/repo", 12)."""
    match = re.fullmatch(r"gh:([\w.-]+/[\w.-]+)#(\d+)", key)
    if not match:
        raise ValueError(f"not a GitHub item key: {key!r}")
    return match.group(1), int(match.group(2))


def closes_links(body: str | None, repo: str) -> list[str]:
    links = {item_key(m.group(1) or repo, int(m.group(2))) for m in _CLOSES.finditer(body or "")}
    return sorted(links)


def _ts(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _login(obj: Mapping[str, Any] | None) -> str | None:
    return obj.get("login") if obj else None


def issue_fields(issue: Mapping[str, Any]) -> dict[str, Any]:
    if issue["state"] == "open":
        status = "open"
    else:
        status = "cancelled" if issue.get("state_reason") == "not_planned" else "done"
    milestone = issue.get("milestone") or {}
    return {
        "kind": "issue",
        "title": issue["title"],
        "status": status,
        "url": issue.get("html_url"),
        "owner_login": _login(issue.get("assignee")),
        "due_at": milestone.get("due_on"),
    }


def pr_fields(pr: Mapping[str, Any]) -> dict[str, Any]:
    if pr["state"] == "open":
        status = "in_progress" if pr.get("draft") else "in_review"
    else:
        status = "done" if pr.get("merged") or pr.get("merged_at") else "cancelled"
    return {
        "kind": "pr",
        "title": pr["title"],
        "status": status,
        "url": pr.get("html_url"),
        "owner_login": _login(pr.get("user")),
    }


def check_state(check_run: Mapping[str, Any]) -> str:
    if check_run.get("status") != "completed":
        return "pending"
    return "failure" if check_run.get("conclusion") in FAILED_CONCLUSIONS else "success"


def _event(
    kind: str, key: str, at: str | None, actor: str | None, subject: str, payload: dict[str, Any]
) -> NewEvent:
    return NewEvent(
        source=SOURCE,
        type=kind,
        idempotency_key=key,
        occurred_at=_ts(at),
        subject_key=subject,
        actor=actor,
        payload=payload,
    )


class GitHubConnector(Connector):
    name = "github"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    def normalize(self, kind: str, raw: Mapping[str, Any], dedupe_key: str) -> list[NewEvent]:
        repo = raw.get("repository", {}).get("full_name")
        action = raw.get("action")
        actor = _login(raw.get("sender"))
        if not repo:
            return []
        if kind == "issues" and "issue" in raw:
            issue = raw["issue"]
            return [
                _event(
                    f"issue.{action}",
                    dedupe_key,
                    issue.get("updated_at"),
                    actor,
                    item_key(repo, issue["number"]),
                    {"fields": issue_fields(issue), "body": (issue.get("body") or "")[:BODY_LIMIT]},
                )
            ]
        if kind == "pull_request" and "pull_request" in raw:
            pr = raw["pull_request"]
            event_type = "pr.merged" if action == "closed" and pr.get("merged") else f"pr.{action}"
            return [
                _event(
                    event_type,
                    dedupe_key,
                    pr.get("updated_at"),
                    actor,
                    item_key(repo, pr["number"]),
                    {
                        "fields": pr_fields(pr),
                        "links": closes_links(pr.get("body"), repo),
                        "body": (pr.get("body") or "")[:BODY_LIMIT],
                    },
                )
            ]
        if kind == "issue_comment" and "comment" in raw and action == "created":
            issue, comment = raw["issue"], raw["comment"]
            on_pr = "pull_request" in issue
            return [
                _event(
                    "pr.commented" if on_pr else "issue.commented",
                    dedupe_key,
                    comment.get("created_at"),
                    actor,
                    item_key(repo, issue["number"]),
                    {
                        "fields": {"kind": "pr" if on_pr else "issue"},
                        "comment_id": comment["id"],
                        "body": (comment.get("body") or "")[:BODY_LIMIT],
                    },
                )
            ]
        if kind == "check_run" and "check_run" in raw:
            run = raw["check_run"]
            state = check_state(run)
            prs = run.get("pull_requests") or []
            return [
                _event(
                    f"ci.{action}",
                    f"{dedupe_key}:{pr['number']}" if len(prs) > 1 else dedupe_key,
                    run.get("completed_at") or run.get("started_at"),
                    actor,
                    item_key(repo, pr["number"]),
                    {
                        "fields": {"kind": "pr", f"ci_checks.{run['name']}": state},
                        "check_run_id": run["id"],
                        "conclusion": run.get("conclusion"),
                        "url": run.get("html_url"),
                    },
                )
                for pr in prs
            ]
        return []

    async def handle_webhook(self, headers: Mapping[str, str], body: bytes) -> WebhookResult:
        secret = get_settings().github_webhook_secret
        if secret is None or not secret.get_secret_value():
            raise NotConfigured("GITHUB_WEBHOOK_SECRET is not set")
        if not verify_signature(secret.get_secret_value(), body, headers.get("x-hub-signature-256")):
            raise InvalidSignature
        event_name = headers.get("x-github-event")
        delivery = headers.get("x-github-delivery")
        if not event_name or not delivery:
            raise ValueError("missing X-GitHub-Event or X-GitHub-Delivery")
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
        installation = (payload.get("installation") or {}).get("id")
        tenant_id = await resolve_integration("github", str(installation)) if installation else None
        return WebhookResult(tenant_id, self.normalize(event_name, payload, f"github:{delivery}"))

    @asynccontextmanager
    async def _app_client(self, tenant_id: UUID) -> AsyncIterator[httpx.AsyncClient]:
        """An HTTP client authenticated with this tenant's GitHub App installation token."""
        settings = get_settings()
        if not settings.github_app_id or not settings.github_app_private_key_path:
            raise NotConfigured("GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY_PATH are required")
        async with tenant_tx(tenant_id) as s:
            installation_id = await s.scalar(
                text("""
                    SELECT external_id FROM integrations WHERE provider = 'github'
                    ORDER BY created_at LIMIT 1
                """)
            )
        if installation_id is None:
            raise NotConfigured("no GitHub installation linked to this tenant")
        private_key = await asyncio.to_thread(Path(settings.github_app_private_key_path).read_text)
        async with httpx.AsyncClient(
            base_url=settings.github_api_url,
            timeout=httpx.Timeout(10.0),
            transport=self._transport,
            headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
        ) as client:
            token = await self._installation_token(
                client, settings.github_app_id, private_key, installation_id
            )
            client.headers["Authorization"] = f"Bearer {token}"
            yield client

    async def find_comment(self, tenant_id: UUID, repo: str, number: int, marker: str) -> int | None:
        """Id of an existing comment containing `marker` (crash recovery: never post twice)."""
        async with self._app_client(tenant_id) as client:
            async for comment in self._pages(
                client, f"/repos/{repo}/issues/{number}/comments", None, {"per_page": "100"}
            ):
                if marker in (comment.get("body") or ""):
                    return int(comment["id"])
        return None

    async def post_comment(self, tenant_id: UUID, repo: str, number: int, body: str, marker: str) -> int:
        existing = await self.find_comment(tenant_id, repo, number, marker)
        if existing is not None:
            return existing
        async with self._app_client(tenant_id) as client:
            resp = await self._request(
                client, "POST", f"/repos/{repo}/issues/{number}/comments", json={"body": body}
            )
            return int(resp.json()["id"])

    async def backfill(self, tenant_id: UUID) -> int:
        async with self._app_client(tenant_id) as client:
            repos = [
                r["full_name"]
                async for r in self._pages(client, "/installation/repositories", "repositories")
            ]
            total = 0
            for repo in repos:
                total += await self._backfill_repo(client, tenant_id, repo)
        log.info(
            "github.backfill_done", extra={"tenant": str(tenant_id), "events": total, "repos": len(repos)}
        )
        return total

    async def _backfill_repo(self, client: httpx.AsyncClient, tenant_id: UUID, repo: str) -> int:
        cursor_name = f"github:backfill:{repo}"
        async with tenant_tx(tenant_id) as s:
            since: str | None = (await get_cursor(s, cursor_name) or {}).get("since")
        events: list[NewEvent] = []
        newest = since or ""
        params: dict[str, str] = {"state": "all", "sort": "updated", "direction": "asc", "per_page": "100"}
        if since:
            params["since"] = since
        async for issue in self._pages(client, f"/repos/{repo}/issues", None, params):
            if "pull_request" not in issue:
                events.append(self._snapshot("issue", repo, issue, issue_fields(issue), []))
                newest = max(newest, issue["updated_at"])
        pr_params = {"state": "all", "sort": "updated", "direction": "desc", "per_page": "100"}
        async for pr in self._pages(client, f"/repos/{repo}/pulls", None, pr_params):
            if since and pr["updated_at"] < since:
                break
            events.append(self._snapshot("pr", repo, pr, pr_fields(pr), closes_links(pr.get("body"), repo)))
            newest = max(newest, pr["updated_at"])
        async with tenant_tx(tenant_id) as s:
            result = await append_events(s, tenant_id, events)
            if newest:
                await set_cursor(s, tenant_id, cursor_name, {"since": newest})
        return len(result.inserted_ids)

    @staticmethod
    def _snapshot(
        kind: str, repo: str, obj: Mapping[str, Any], fields: dict[str, Any], links: list[str]
    ) -> NewEvent:
        key = item_key(repo, obj["number"])
        payload: dict[str, Any] = {"fields": fields, "body": (obj.get("body") or "")[:BODY_LIMIT]}
        if links:
            payload["links"] = links
        return _event(
            f"{kind}.snapshot",
            f"github-backfill:{key}@{obj['updated_at']}",
            obj["updated_at"],
            None,
            key,
            payload,
        )

    async def _installation_token(
        self, client: httpx.AsyncClient, app_id: str, private_key: str, installation_id: str
    ) -> str:
        now = int(time.time())
        app_jwt = jwt.encode(
            {"iat": now - 60, "exp": now + 540, "iss": app_id}, private_key, algorithm="RS256"
        )
        resp = await self._request(
            client,
            "POST",
            f"/app/installations/{installation_id}/access_tokens",
            headers={"Authorization": f"Bearer {app_jwt}"},
        )
        return str(resp.json()["token"])

    async def _pages(
        self,
        client: httpx.AsyncClient,
        url: str,
        list_key: str | None,
        params: dict[str, str] | None = None,
    ) -> Any:
        next_url: str | None = url
        for _ in range(MAX_PAGES):
            if next_url is None:
                return
            resp = await self._request(client, "GET", next_url, params=params)
            params = None  # the `next` link already carries the query string
            data = resp.json()
            for item in data[list_key] if list_key else data:
                yield item
            next_url = resp.links.get("next", {}).get("url")
        if next_url:
            log.warning("github.page_cap_reached", extra={"url": url, "max_pages": MAX_PAGES})

    @staticmethod
    async def _request(client: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> httpx.Response:
        for attempt in range(3):
            resp = await client.request(method, url, **kwargs)
            limited = resp.status_code == 403 and resp.headers.get("x-ratelimit-remaining") == "0"
            if resp.status_code not in RETRY_STATUSES and not limited:
                break
            delay = min(float(resp.headers.get("retry-after", 2**attempt)), 30.0)
            log.warning(
                "github.retry", extra={"status": resp.status_code, "attempt": attempt, "delay": delay}
            )
            await asyncio.sleep(delay)
        resp.raise_for_status()
        return resp
