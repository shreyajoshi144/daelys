from collections.abc import AsyncIterator

import httpx
import pytest

from api.routes import health
from core.security import Role
from tests.conftest import SeededTenant, auth_headers


async def test_health_ok(client: httpx.AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "checks": {"db": "ok", "redis": "ok"}}
    assert resp.headers["x-request-id"]


async def test_health_reports_failed_dependency(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken() -> None:
        raise ConnectionError("redis down")

    monkeypatch.setattr(health, "_check_redis", broken)
    resp = await client.get("/health")
    assert resp.status_code == 503
    assert resp.json() == {"status": "degraded", "checks": {"db": "ok", "redis": "error"}}


async def test_security_headers_and_request_id(client: httpx.AsyncClient) -> None:
    resp = await client.get("/", headers={"x-request-id": "trace-123"})
    assert resp.headers["x-request-id"] == "trace-123"
    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in resp.headers["content-security-policy"]
    hostile = (await client.get("/health", headers={"x-request-id": "x" * 500 + "\tinjected"})).headers
    assert hostile["x-request-id"] != "x" * 500 and len(hostile["x-request-id"]) == 32  # replaced, not echoed


async def test_chunked_upload_over_the_cap_is_refused_while_streaming(
    client: httpx.AsyncClient, fresh_tenant: SeededTenant
) -> None:
    """No Content-Length to check up front: the body is cut off once it passes the cap (413, not 5xx)."""
    sent = 0

    async def body() -> AsyncIterator[bytes]:
        nonlocal sent
        for _ in range(64):  # 4 MB in 64 KB chunks, twice the .ics cap
            sent += 1
            yield b"x" * 65536

    lead = await auth_headers(client, fresh_tenant, Role.lead)
    resp = await client.post(
        "/calendar/ics", content=body(), headers={**lead, "content-type": "text/calendar"}
    )
    assert resp.status_code == 413
    assert sent < 64  # the server stopped reading; the rest of the upload was never consumed
