import logging

from redis.exceptions import RedisError

from core.queue import get_redis

log = logging.getLogger(__name__)


async def allow(key: str, limit: int, window_seconds: int = 60) -> bool:
    """Fixed-window counter. Fails open if Redis is down (logged), so an outage does not lock out logins."""
    redis = get_redis()
    full_key = f"rl:{key}"
    try:
        async with redis.pipeline(transaction=True) as pipe:
            pipe.incr(full_key)
            pipe.expire(full_key, window_seconds, nx=True)
            count, _ = await pipe.execute()
    except (RedisError, OSError):
        log.error("ratelimit.redis_unavailable", extra={"key": key}, exc_info=True)
        return True
    return int(count) <= limit
