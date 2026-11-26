import logging
import mimetypes
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from api.routes import (
    auth,
    collab,
    connectors,
    events,
    health,
    projects,
    proposals,
    risk,
    webhooks,
    work_items,
)
from core import queue
from core.config import get_settings
from core.db import get_engine
from core.errors import NotConfigured
from core.logging import configure_logging
from core.realtime import broadcaster

log = logging.getLogger("daelys.api")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
# The web app loads only same-origin files (React and htm are vendored), so the policy can be strict.
WEB_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)
_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")
_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
_WEB_ASSETS = (".js", ".css", ".html")
# StaticFiles uses the OS mime table, which may lack these; pin them (nosniff is on).
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("image/webp", ".webp")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(get_settings().log_level)
    yield
    await broadcaster.close()
    await queue.close()
    await get_engine().dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Daelys API", version="0.1.0", lifespan=lifespan)
    secure = get_settings().cookie_secure  # served over HTTPS: tell browsers to stay on HTTPS

    @app.middleware("http")
    async def access_log(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        # A caller's id is kept only if it is a plain token; anything else could pollute logs and headers.
        claimed = request.headers.get("x-request-id", "")
        request_id = claimed if _REQUEST_ID.fullmatch(claimed) else uuid.uuid4().hex
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["referrer-policy"] = "strict-origin-when-cross-origin"
        if secure:
            response.headers["strict-transport-security"] = "max-age=31536000; includeSubDomains"
        if not request.url.path.startswith(_DOCS_PATHS):
            response.headers["content-security-policy"] = WEB_CSP
        if request.url.path == "/" or request.url.path.endswith(_WEB_ASSETS):
            # Revalidate on every load (ETag -> 304), so a deploy never leaves users on stale JS modules.
            response.headers["cache-control"] = "no-cache"
        log.info(
            "http.request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        return response

    @app.exception_handler(NotConfigured)
    async def not_configured(request: Request, exc: NotConfigured) -> JSONResponse:
        log.warning("not_configured", extra={"path": request.url.path, "detail": str(exc)})
        return JSONResponse({"error": "not_configured", "detail": str(exc)}, status_code=503)

    for module in (health, auth, work_items, events, webhooks, connectors, projects, risk, proposals, collab):
        app.include_router(module.router)
    # Registered last, so every API route above takes precedence over a static file of the same path.
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


app = create_app()
