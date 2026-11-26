import logging
from functools import lru_cache
from uuid import UUID

from arq.connections import ArqRedis, RedisSettings, create_pool
from redis.asyncio import Redis
from redis.exceptions import RedisError

from core.config import get_settings

log = logging.getLogger(__name__)

_pool: ArqRedis | None = None


@lru_cache
def redis_settings() -> RedisSettings:
    settings = RedisSettings.from_dsn(get_settings().redis_url)
    settings.conn_timeout = 2
    return settings


@lru_cache
def get_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url, socket_timeout=2, socket_connect_timeout=2)


async def get_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(redis_settings())
    return _pool


async def enqueue_projection(tenant_id: UUID) -> None:
    """Best effort: events are already committed, and the worker's periodic sweep projects anything missed."""
    try:
        pool = await get_pool()
        # Fixed job id: arq drops the enqueue while one is already queued or running for this tenant.
        await pool.enqueue_job("project", str(tenant_id), _job_id=f"project:{tenant_id}")
    except (RedisError, OSError):
        log.warning("queue.enqueue_failed", extra={"tenant": str(tenant_id)}, exc_info=True)


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
    await get_redis().aclose()
    get_redis.cache_clear()
