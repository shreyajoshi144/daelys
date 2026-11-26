from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import text

from api.deps import AnyMember
from api.schemas import EventOut, EventPage

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=EventPage)
async def list_events(
    ctx: AnyMember,
    since: Annotated[
        datetime | None, Query(description="only events that occurred at or after this time")
    ] = None,
    after_id: Annotated[
        int, Query(ge=0, description="keyset cursor: next_after_id from the previous page")
    ] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> EventPage:
    rows = await ctx.session.execute(
        text("""
            SELECT id, source, type, occurred_at, ingested_at, actor, subject_key, payload FROM events
            WHERE id > :after_id AND (CAST(:since AS timestamptz) IS NULL OR occurred_at >= :since)
            ORDER BY id LIMIT :limit
        """),
        {"after_id": after_id, "since": since, "limit": limit},
    )
    items = [EventOut.model_validate(dict(r)) for r in rows.mappings()]
    return EventPage(items=items, next_after_id=items[-1].id if len(items) == limit else None)


@router.get("/{event_id}")
async def get_event(event_id: int, ctx: AnyMember) -> dict[str, object]:
    """One event plus the work item it is about (for evidence links). Other tenants' ids are 404."""
    row = (
        (
            await ctx.session.execute(
                text("""
                SELECT e.id, e.source, e.type, e.occurred_at, e.ingested_at, e.actor, e.subject_key,
                    e.payload,
                       w.id AS item_id, w.title AS item_title, w.url AS item_url
                FROM events e LEFT JOIN work_items w ON w.key = e.subject_key WHERE e.id = :id
            """),
                {"id": event_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="event not found")
    item_id: UUID | None = row["item_id"]
    return {**dict(row), "item_id": item_id}
