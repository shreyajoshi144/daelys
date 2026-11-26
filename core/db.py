from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from core.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(
        get_settings().app_database_url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=10,
        connect_args={"command_timeout": 30},
    )


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def set_tenant(session: AsyncSession, tenant_id: UUID) -> None:
    """Equivalent to `SET LOCAL app.tenant_id`, but parameterised. Scoped to the current transaction."""
    await session.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant_id)})


@asynccontextmanager
async def tenant_tx(tenant_id: UUID) -> AsyncIterator[AsyncSession]:
    """One transaction with RLS scoped to `tenant_id`. Commits on success, rolls back on error."""
    async with get_sessionmaker()() as session, session.begin():
        await set_tenant(session, tenant_id)
        yield session


@asynccontextmanager
async def readonly_tenant_tx(tenant_id: UUID) -> AsyncIterator[AsyncSession]:
    """Tenant-scoped transaction that Postgres itself refuses to write in. Agent tools use only this."""
    async with get_sessionmaker()() as session, session.begin():
        await session.execute(text("SET TRANSACTION READ ONLY"))
        await set_tenant(session, tenant_id)
        yield session


async def fetch_all(
    session: AsyncSession, sql: str, params: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Read helper: rows as dicts. Agent code reads only through these, never `execute`."""
    return [dict(r) for r in (await session.execute(text(sql), params or {})).mappings()]


async def fetch_one(
    session: AsyncSession, sql: str, params: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    row = (await session.execute(text(sql), params or {})).mappings().first()
    return dict(row) if row is not None else None


@asynccontextmanager
async def global_tx() -> AsyncIterator[AsyncSession]:
    """A transaction with no tenant set: RLS hides every tenant row. Only for SECURITY DEFINER lookups."""
    async with get_sessionmaker()() as session, session.begin():
        yield session


async def resolve_tenant_slug(slug: str) -> UUID | None:
    async with global_tx() as s:
        value = await s.scalar(text("SELECT daelys_resolve_tenant(:slug)"), {"slug": slug})
    return UUID(str(value)) if value else None


async def resolve_integration(provider: str, external_id: str) -> UUID | None:
    async with global_tx() as s:
        value = await s.scalar(
            text("SELECT daelys_resolve_integration(:p, :e)"), {"p": provider, "e": external_id}
        )
    return UUID(str(value)) if value else None


async def all_tenant_ids() -> list[UUID]:
    async with global_tx() as s:
        rows = await s.scalars(text("SELECT daelys_tenant_ids()"))
        return [UUID(str(r)) for r in rows]
