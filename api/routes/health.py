import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from core.db import get_engine
from core.queue import get_redis

router = APIRouter(tags=["health"])
log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 3.0


async def _check_db() -> None:
    async with get_engine().connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _check_redis() -> None:
    await get_redis().ping()


@router.get("/health")
async def health() -> JSONResponse:
    checks: dict[str, str] = {}
    for name, probe in (("db", _check_db), ("redis", _check_redis)):
        try:
            await asyncio.wait_for(probe(), TIMEOUT_SECONDS)
            checks[name] = "ok"
        except Exception as exc:  # any failure means unhealthy; reported, never swallowed
            log.error("health.check_failed", extra={"check": name, "error": repr(exc)})
            checks[name] = "error"
    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(
        {"status": "ok" if healthy else "degraded", "checks": checks}, status_code=200 if healthy else 503
    )
