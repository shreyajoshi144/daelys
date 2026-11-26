"""Integration fixtures against real Postgres and Redis (never SQLite or fakes).

Tests run in a separate `<db>_test` database, rebuilt from migrations at session start.
"""

import asyncio
import os
import sys
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parent.parent

for var in ("DATABASE_URL", "APP_DATABASE_URL"):
    if var not in os.environ:
        raise RuntimeError(f"{var} must be set (run tests via `make test`, which loads .env)")
ADMIN_URL = make_url(os.environ["DATABASE_URL"])
TEST_DB = f"{ADMIN_URL.database}_test"
os.environ["DATABASE_URL"] = ADMIN_URL.set(database=TEST_DB).render_as_string(hide_password=False)
os.environ["APP_DATABASE_URL"] = (
    make_url(os.environ["APP_DATABASE_URL"]).set(database=TEST_DB).render_as_string(hide_password=False)
)
os.environ["REDIS_URL"] = os.environ.get("REDIS_URL", "redis://localhost:6379/0").rsplit("/", 1)[0] + "/15"
os.environ["JWT_SECRET"] = "test-jwt-secret-" + "x" * 32
os.environ["GITHUB_WEBHOOK_SECRET"] = "test-webhook-secret"
os.environ["LOGIN_RATE_LIMIT_PER_MINUTE"] = "1000"
os.environ.setdefault("DAELYS_APP_PASSWORD", make_url(os.environ["APP_DATABASE_URL"]).password or "")
for var in (
    "GITHUB_APP_ID",
    "GITHUB_APP_PRIVATE_KEY_PATH",
    "SEED_GITHUB_INSTALLATION_ID",
    "ANTHROPIC_API_KEY",
    "VOYAGE_API_KEY",
):
    os.environ[var] = ""

import asyncpg  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402

from api.main import app  # noqa: E402
from core.queue import get_redis  # noqa: E402
from core.security import Role  # noqa: E402
from core.seed import AGENT, SeedUser, ensure_tenant, link_github, seed_users  # noqa: E402

PASSWORD = "correct-horse-battery"
INSTALLATION_ID = "424242"


def _asyncpg_dsn(database: str) -> str:
    return ADMIN_URL.set(drivername="postgresql", database=database).render_as_string(hide_password=False)


@dataclass(frozen=True)
class SeededTenant:
    id: uuid.UUID
    slug: str
    users: dict[Role, SeedUser]


@pytest.fixture(scope="session")
async def database() -> AsyncIterator[None]:
    conn = await asyncpg.connect(_asyncpg_dsn(ADMIN_URL.database or "postgres"))
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{TEST_DB}"')
    finally:
        await conn.close()
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "alembic", "upgrade", "head", cwd=ROOT, env=os.environ.copy()
    )
    assert await proc.wait() == 0, "alembic upgrade failed"
    await get_redis().flushdb()
    yield


def _users(prefix: str) -> dict[Role, SeedUser]:
    return {
        Role.owner: SeedUser("Owner", f"owner@{prefix}.example", Role.owner, "Product"),
        Role.lead: SeedUser("Lead", f"lead@{prefix}.example", Role.lead, "Engineering", f"{prefix}-lead-gh"),
        Role.member: SeedUser(
            "Member", f"member@{prefix}.example", Role.member, "Engineering", f"{prefix}-dev"
        ),
        Role.viewer: SeedUser("Viewer", f"viewer@{prefix}.example", Role.viewer, "Marketing"),
    }


async def make_tenant(prefix: str | None = None, *, installation_id: str | None = None) -> SeededTenant:
    prefix = prefix or f"t{uuid.uuid4().hex[:10]}"
    tenant_id = await ensure_tenant(prefix, prefix.title())
    users = _users(prefix)
    await seed_users(tenant_id, list(users.values()), PASSWORD)
    if installation_id:
        await link_github(tenant_id, installation_id)
    return SeededTenant(tenant_id, prefix, users)


@pytest.fixture(scope="session")
async def tiffin(database: None) -> SeededTenant:
    return await make_tenant("tiffin-test", installation_id=INSTALLATION_ID)


@pytest.fixture(scope="session")
async def decoy(database: None) -> SeededTenant:
    return await make_tenant("decoy-test")


@pytest.fixture
async def fresh_tenant(database: None) -> SeededTenant:
    return await make_tenant()


@pytest.fixture
async def client(database: None) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def auth_headers(client: httpx.AsyncClient, tenant: SeededTenant, role: Role) -> dict[str, str]:
    resp = await client.post(
        "/auth/login", json={"tenant": tenant.slug, "email": tenant.users[role].email, "password": PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


__all__ = ["AGENT", "INSTALLATION_ID", "PASSWORD", "SeededTenant", "auth_headers", "make_tenant"]
