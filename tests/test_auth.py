from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import jwt
import pytest

from core.config import get_settings
from core.queue import get_redis
from core.ratelimit import allow
from core.security import AUDIENCE, ISSUER, Principal, Role, hash_password, issue_token, verify_password
from tests.conftest import PASSWORD, SeededTenant, auth_headers

LEAD_ONLY = "/connectors/github/backfill"


async def test_login_success_and_failure(client: httpx.AsyncClient, tiffin: SeededTenant) -> None:
    email = tiffin.users[Role.member].email
    ok = await client.post(
        "/auth/login", json={"tenant": tiffin.slug, "email": email.upper(), "password": PASSWORD}
    )
    assert ok.status_code == 200
    claims = jwt.decode(ok.json()["access_token"], options={"verify_signature": False})
    assert claims["tenant_id"] == str(tiffin.id) and claims["role"] == "member"

    for body in (
        {"tenant": tiffin.slug, "email": email, "password": "wrong"},
        {"tenant": tiffin.slug, "email": "nobody@nowhere.example", "password": PASSWORD},
        {"tenant": "no-such-tenant", "email": email, "password": PASSWORD},
    ):
        resp = await client.post("/auth/login", json=body)
        assert resp.status_code == 401 and resp.json()["detail"] == "invalid credentials"


async def test_agent_principal_cannot_log_in(client: httpx.AsyncClient, tiffin: SeededTenant) -> None:
    resp = await client.post(
        "/auth/login", json={"tenant": tiffin.slug, "email": "agent@daelys.internal", "password": PASSWORD}
    )
    assert resp.status_code == 401


async def test_user_cannot_log_into_another_tenant(
    client: httpx.AsyncClient, tiffin: SeededTenant, decoy: SeededTenant
) -> None:
    resp = await client.post(
        "/auth/login",
        json={"tenant": decoy.slug, "email": tiffin.users[Role.owner].email, "password": PASSWORD},
    )
    assert resp.status_code == 401


def _claims(tenant: SeededTenant, role: str, exp: datetime) -> dict[str, object]:
    return {
        "sub": "00000000-0000-0000-0000-000000000001",
        "tenant_id": str(tenant.id),
        "role": role,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": datetime.now(UTC) - timedelta(hours=2),
        "exp": exp,
    }


async def test_bad_tokens_are_401(client: httpx.AsyncClient, tiffin: SeededTenant) -> None:
    valid = (await auth_headers(client, tiffin, Role.viewer))["Authorization"].removeprefix("Bearer ")
    secret = get_settings().jwt_secret.get_secret_value()
    header, payload, sig = valid.split(".")
    tampered_claims = jwt.decode(valid, options={"verify_signature": False}) | {"role": "owner"}
    tampered = jwt.encode(tampered_claims, "not-the-secret-" + "y" * 32, algorithm="HS256")
    unsigned = jwt.encode(tampered_claims, key="", algorithm="none")
    expired = jwt.encode(
        _claims(tiffin, "owner", datetime.now(UTC) - timedelta(minutes=1)), secret, algorithm="HS256"
    )
    for token in (tampered, unsigned, expired, f"{header}.{payload}.{sig[::-1]}", "garbage"):
        resp = await client.get("/work-items", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401, token
    assert (await client.get("/work-items")).status_code == 401


async def test_token_for_unknown_user_is_401(client: httpx.AsyncClient, tiffin: SeededTenant) -> None:
    secret = get_settings().jwt_secret.get_secret_value()
    token = jwt.encode(
        _claims(tiffin, "owner", datetime.now(UTC) + timedelta(minutes=5)), secret, algorithm="HS256"
    )
    assert (await client.get("/work-items", headers={"Authorization": f"Bearer {token}"})).status_code == 401


async def test_viewer_gets_403_on_lead_endpoint(client: httpx.AsyncClient, tiffin: SeededTenant) -> None:
    for role in (Role.viewer, Role.member):
        resp = await client.post(LEAD_ONLY, headers=await auth_headers(client, tiffin, role))
        assert resp.status_code == 403
    # A lead passes authorization; the backfill then reports the missing GitHub App config, never fakes it.
    resp = await client.post(LEAD_ONLY, headers=await auth_headers(client, tiffin, Role.lead))
    assert resp.status_code == 503 and resp.json()["error"] == "not_configured"


async def test_role_comes_from_membership_not_token(client: httpx.AsyncClient, tiffin: SeededTenant) -> None:
    viewer = await client.post(
        "/auth/login",
        json={"tenant": tiffin.slug, "email": tiffin.users[Role.viewer].email, "password": PASSWORD},
    )
    user_id = jwt.decode(viewer.json()["access_token"], options={"verify_signature": False})["sub"]
    escalated = issue_token(Principal(UUID(user_id), tiffin.id, Role.owner))
    resp = await client.post(LEAD_ONLY, headers={"Authorization": f"Bearer {escalated}"})
    assert resp.status_code == 403


async def test_login_rate_limited(
    client: httpx.AsyncClient, fresh_tenant: SeededTenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "login_rate_limit_per_minute", 3)
    await get_redis().delete("rl:login:ip:127.0.0.1")  # earlier tests log in from the same client IP
    body = {"tenant": fresh_tenant.slug, "email": fresh_tenant.users[Role.owner].email, "password": "wrong"}
    codes = [(await client.post("/auth/login", json=body)).status_code for _ in range(5)]
    assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]


async def test_ratelimit_window(database: None) -> None:
    results = [await allow("unit-test-key", limit=2, window_seconds=60) for _ in range(3)]
    assert results == [True, True, False]


def test_password_hashing() -> None:
    hashed = hash_password("s3cret-pass")
    assert verify_password("s3cret-pass", hashed) and not verify_password("other", hashed)
    assert not verify_password("anything", None)
    with pytest.raises(ValueError):
        hash_password("x" * 73)
