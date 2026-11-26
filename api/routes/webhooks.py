import json
import logging

from fastapi import APIRouter, HTTPException, Request, status

from api.bodies import read_limited
from api.schemas import WebhookAck
from connectors.github import GitHubConnector, InvalidSignature
from core import queue
from core.db import tenant_tx
from core.events import append_events

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
log = logging.getLogger(__name__)

MAX_BODY_BYTES = 5 * 1024 * 1024


@router.post("/github", status_code=status.HTTP_202_ACCEPTED, response_model=WebhookAck)
async def github_webhook(request: Request) -> WebhookAck:
    body = await read_limited(request, MAX_BODY_BYTES)
    try:
        result = await GitHubConnector().handle_webhook(request.headers, body)
    except InvalidSignature:
        log.warning("github.bad_signature", extra={"delivery": request.headers.get("x-github-delivery")})
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid signature") from None
    except (ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"malformed webhook: {exc}"
        ) from None

    if result.tenant_id is None:
        log.warning(
            "github.unknown_installation", extra={"delivery": request.headers.get("x-github-delivery")}
        )
        return WebhookAck(status="ignored", reason="unknown installation")
    if not result.events:
        return WebhookAck(status="ignored", reason="unsupported event")

    async with tenant_tx(result.tenant_id) as session:
        appended = await append_events(session, result.tenant_id, result.events)
    if appended.inserted_ids:
        await queue.enqueue_projection(result.tenant_id)
    return WebhookAck(status="accepted", inserted=len(appended.inserted_ids), duplicates=appended.duplicates)
