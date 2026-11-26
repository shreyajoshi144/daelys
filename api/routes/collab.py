"""Sprint 5 APIs: meetings + speakers, weekly summaries, duplicate suggestions, Ask Daelys, comments, calendar
upload, and the demo reset."""

from datetime import UTC, date, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from api.bodies import read_limited
from api.deps import HUMAN_ROLES, AnyMember, LeadPrincipal, OwnerPrincipal, require_principal
from connectors.ics import MAX_BYTES as ICS_MAX_BYTES
from connectors.ics import IcsConnector
from connectors.notiv import parse_transcript, transcribe_audio
from core import queue
from core.config import get_settings
from core.db import tenant_tx
from core.events import append_events
from core.insights import ask, generate_summary
from core.llm import LLMUnavailable, ProviderError, get_llm
from core.meetings import MeetingNotFound, create_meeting, parse_rendered, resolve_speakers
from core.ratelimit import allow
from core.security import Principal, Role
from rules.engine import evaluate_tenant
from workers.projector import project_tenant

router = APIRouter(tags=["collaboration"])

Member = Annotated[Principal, Depends(require_principal(*HUMAN_ROLES))]
Writer = Annotated[Principal, Depends(require_principal(Role.owner, Role.lead, Role.member))]
_NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
MAX_AUDIO_BYTES = 50 * 1024 * 1024
AUDIO_TYPES = {
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/aiff": ".aiff",
    "audio/x-aiff": ".aiff",
}
LANGUAGES = {"auto": None, "en": "en", "hi": "hi"}


class TranscriptIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    occurred_at: datetime
    language: str = Field(default="auto", pattern="^(auto|en|hi)$")
    transcript: str = Field(min_length=1, max_length=400_000)
    project_id: UUID | None = None


class SpeakerIn(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    user_id: UUID


class SummaryIn(BaseModel):
    project_id: UUID
    lane: str = Field(pattern="^(Product|Engineering|Marketing)$")
    week_start: date


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


async def _queue_extraction(tenant_id: UUID, meeting_id: UUID) -> None:
    pool = await queue.get_pool()
    await pool.enqueue_job(
        "extract_meeting", str(tenant_id), str(meeting_id), _job_id=f"extract:{meeting_id}"
    )


async def _refresh(tenant_id: UUID) -> None:
    await project_tenant(tenant_id)
    await evaluate_tenant(tenant_id)


# --- meetings ---


@router.post("/meetings/transcript", status_code=status.HTTP_201_CREATED)
async def upload_transcript(body: TranscriptIn, principal: Writer) -> dict[str, Any]:
    """A speaker-labelled transcript ("Name: text" lines). Extraction runs in the worker."""
    if body.occurred_at.tzinfo is None:
        raise HTTPException(status_code=422, detail="occurred_at needs a UTC offset")
    try:
        lines = parse_transcript(body.transcript)
        meeting_id = await create_meeting(
            principal.tenant_id,
            title=body.title,
            occurred_at=body.occurred_at,
            language=body.language,
            source="transcript",
            lines=lines,
            project_id=body.project_id,
            created_by=principal.user_id,
        )
    except MeetingNotFound:
        raise _NOT_FOUND from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await _queue_extraction(principal.tenant_id, meeting_id)
    return {"meeting_id": meeting_id, "lines": len(lines)}


@router.post("/meetings/audio", status_code=status.HTTP_201_CREATED)
async def upload_audio(
    request: Request,
    principal: Writer,
    title: Annotated[str, Query(min_length=1, max_length=200)],
    occurred_at: datetime,
    language: Annotated[str, Query(pattern="^(auto|en|hi)$")] = "auto",
    project_id: UUID | None = None,
) -> dict[str, Any]:
    """A recording, transcribed by Notiv (local Whisper). No speakers: lines are 'Unknown' until mapped."""
    if occurred_at.tzinfo is None:
        raise HTTPException(status_code=422, detail="occurred_at needs a UTC offset")
    suffix = AUDIO_TYPES.get(request.headers.get("content-type", "").split(";")[0].strip())
    if suffix is None:
        raise HTTPException(
            status_code=415, detail=f"unsupported audio type; use one of {sorted(AUDIO_TYPES)}"
        )
    body = await read_limited(request, MAX_AUDIO_BYTES, "recording too large")
    try:
        lines = await transcribe_audio(body, suffix, LANGUAGES[language])  # NotConfigured -> 503
        meeting_id = await create_meeting(
            principal.tenant_id,
            title=title,
            occurred_at=occurred_at,
            language=language,
            source="audio",
            lines=lines,
            project_id=project_id,
            created_by=principal.user_id,
        )
    except MeetingNotFound:
        raise _NOT_FOUND from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await _queue_extraction(principal.tenant_id, meeting_id)
    return {"meeting_id": meeting_id, "lines": len(lines)}


@router.get("/meetings")
async def list_meetings(ctx: AnyMember) -> list[dict[str, Any]]:
    rows = await ctx.session.execute(
        text("""
        SELECT id, title, occurred_at, language, source, status, extraction, project_id FROM meetings
        ORDER BY occurred_at DESC LIMIT 100
    """)
    )
    return [dict(r) for r in rows.mappings()]


@router.get("/meetings/{meeting_id}")
async def get_meeting(meeting_id: UUID, ctx: AnyMember) -> dict[str, Any]:
    if ctx.principal.role == Role.viewer:
        raise HTTPException(status_code=403, detail="transcripts are visible to members and above")
    row = (
        (
            await ctx.session.execute(
                text("""
        SELECT m.id, m.title, m.occurred_at, m.language, m.source, m.status, m.extraction, a.content
        FROM meetings m JOIN artifacts a ON a.id = m.artifact_id WHERE m.id = :id
    """),
                {"id": meeting_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise _NOT_FOUND
    lines = parse_rendered(row["content"])
    speakers = await resolve_speakers(ctx.session, {line.speaker for line in lines if line.speaker})
    events = await ctx.session.execute(
        text("""
        SELECT id, type, subject_key, payload FROM events
        WHERE payload->>'meeting_id' = :m OR subject_key = 'meeting:' || :m ORDER BY id
    """),
        {"m": str(meeting_id)},
    )
    return {
        **{k: v for k, v in row.items() if k != "content"},
        "lines": [line.__dict__ for line in lines],
        "speakers": {label: (str(uid) if uid else None) for label, uid in speakers.items()},
        "events": [dict(e) for e in events.mappings()],
    }


@router.post("/meetings/{meeting_id}/extract", status_code=status.HTTP_202_ACCEPTED)
async def retry_extraction(meeting_id: UUID, principal: LeadPrincipal) -> dict[str, str]:
    get_llm()  # NotConfigured -> 503 before queueing
    await _queue_extraction(principal.tenant_id, meeting_id)
    return {"status": "queued"}


@router.get("/speakers")
async def list_speakers(ctx: AnyMember) -> list[dict[str, Any]]:
    rows = await ctx.session.execute(
        text("""
        SELECT s.label, s.user_id, u.display_name FROM speaker_mappings s JOIN users u ON u.id = s.user_id
        ORDER BY s.label
    """)
    )
    return [dict(r) for r in rows.mappings()]


@router.put("/speakers", status_code=status.HTTP_204_NO_CONTENT)
async def map_speaker(body: SpeakerIn, principal: LeadPrincipal) -> None:
    async with tenant_tx(principal.tenant_id) as s:
        role = await s.scalar(text("SELECT role FROM memberships WHERE user_id = :u"), {"u": body.user_id})
        if role is None or role == "agent":
            raise HTTPException(status_code=422, detail="user is not a member")
        await s.execute(
            text("""
            INSERT INTO speaker_mappings (tenant_id, label, user_id) VALUES (:t, :l, :u)
            ON CONFLICT (tenant_id, label) DO UPDATE SET user_id = EXCLUDED.user_id
        """),
            {"t": principal.tenant_id, "l": body.label.strip(), "u": body.user_id},
        )


# --- summaries, duplicates, ask ---


@router.post("/summaries", status_code=status.HTTP_201_CREATED)
async def create_summary(body: SummaryIn, principal: LeadPrincipal) -> dict[str, Any]:
    """Generate (or regenerate) a lane summary. Earlier versions are kept."""
    if not await allow(f"llm:summary:{principal.tenant_id}", get_settings().llm_rate_limit_per_minute):
        raise HTTPException(status_code=429, detail="too many summary requests")
    llm = get_llm()
    try:
        return await generate_summary(
            llm, principal.tenant_id, body.project_id, body.lane, body.week_start, principal.user_id
        )
    except LookupError:
        raise _NOT_FOUND from None
    except (LLMUnavailable, ProviderError) as exc:
        raise HTTPException(status_code=502, detail="language model unavailable") from exc


@router.get("/summaries")
async def list_summaries(ctx: AnyMember, project_id: UUID, lane: str | None = None) -> list[dict[str, Any]]:
    rows = await ctx.session.execute(
        text("""
        SELECT id, lane, week_start, version, sentences, dropped, created_at FROM summaries
        WHERE project_id = :p AND (CAST(:lane AS text) IS NULL OR lane = :lane)
        ORDER BY week_start DESC, lane, version DESC LIMIT 60
    """),
        {"p": project_id, "lane": lane},
    )
    return [dict(r) for r in rows.mappings()]


@router.get("/duplicates")
async def list_duplicates(ctx: AnyMember) -> list[dict[str, Any]]:
    rows = await ctx.session.execute(
        text("""
        SELECT d.id, d.similarity, d.reason, d.status, d.created_at, a.id AS item_id, a.title AS item_title,
               b.id AS duplicate_of_id, b.title AS duplicate_of_title
        FROM duplicate_suggestions d JOIN work_items a ON a.id = d.item_id
        JOIN work_items b ON b.id = d.duplicate_of
        WHERE d.status = 'open' ORDER BY d.created_at DESC LIMIT 100
    """)
    )
    return [dict(r) for r in rows.mappings()]


@router.post("/duplicates/{suggestion_id}/{decision}", status_code=status.HTTP_204_NO_CONTENT)
async def decide_duplicate(suggestion_id: UUID, decision: str, principal: LeadPrincipal) -> None:
    """Records a human's call. Confirming does not merge anything; it only closes the suggestion."""
    if decision not in ("dismiss", "confirm"):
        raise _NOT_FOUND
    async with tenant_tx(principal.tenant_id) as s:
        result = await s.execute(
            text("UPDATE duplicate_suggestions SET status = :st WHERE id = :id AND status = 'open'"),
            {"st": "dismissed" if decision == "dismiss" else "confirmed", "id": suggestion_id},
        )
        if result.rowcount == 0:  # type: ignore[attr-defined]
            raise _NOT_FOUND


@router.post("/ask")
async def ask_daelys(body: AskIn, principal: Member) -> dict[str, Any]:
    """Answers only from evidence the caller may see (tenant + role filtered in SQL), or says it can't."""
    if not await allow(f"llm:ask:{principal.tenant_id}", get_settings().llm_rate_limit_per_minute):
        raise HTTPException(status_code=429, detail="too many questions")
    llm = get_llm()
    try:
        return await ask(llm, principal.tenant_id, principal.role, body.question)
    except (LLMUnavailable, ProviderError) as exc:
        raise HTTPException(status_code=502, detail="language model unavailable") from exc


# --- comments, calendar, demo reset -------------------------------------------------------------------------


@router.get("/work-items/{item_id}/comments")
async def list_comments(item_id: UUID, ctx: AnyMember) -> list[dict[str, Any]]:
    rows = await ctx.session.execute(
        text("""
        SELECT c.id, c.body, c.created_at, u.display_name AS author
        FROM comments c JOIN users u ON u.id = c.author_id
        WHERE c.work_item_id = :i ORDER BY c.created_at LIMIT 500
    """),
        {"i": item_id},
    )
    return [dict(r) for r in rows.mappings()]


@router.post("/work-items/{item_id}/comments", status_code=status.HTTP_201_CREATED)
async def add_comment(item_id: UUID, body: CommentIn, principal: Writer) -> dict[str, Any]:
    async with tenant_tx(principal.tenant_id) as s:
        if await s.scalar(text("SELECT 1 FROM work_items WHERE id = :i"), {"i": item_id}) is None:
            raise _NOT_FOUND
        row = (
            await s.execute(
                text("""
            INSERT INTO comments (tenant_id, work_item_id, author_id, body) VALUES (:t, :i, :u, :b)
            RETURNING id, created_at
        """),
                {"t": principal.tenant_id, "i": item_id, "u": principal.user_id, "b": body.body.strip()},
            )
        ).one()
        await s.execute(text("SELECT pg_notify('daelys_updates', :t)"), {"t": str(principal.tenant_id)})
    return {"id": row.id, "created_at": row.created_at}


@router.post("/calendar/ics", status_code=status.HTTP_202_ACCEPTED)
async def upload_calendar(request: Request, principal: LeadPrincipal) -> dict[str, int]:
    try:
        body = await read_limited(request, ICS_MAX_BYTES, "calendar file too large")
        events = IcsConnector().from_upload(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    async with tenant_tx(principal.tenant_id) as s:
        result = await append_events(s, principal.tenant_id, events)
    await _refresh(principal.tenant_id)
    return {"events": len(events), "inserted": len(result.inserted_ids), "duplicates": result.duplicates}


@router.post("/admin/reset-scenario", status_code=status.HTTP_202_ACCEPTED)
async def reset_scenario(principal: OwnerPrincipal) -> dict[str, Any]:
    """Demo tenants only: clear the log and replay the scenario baseline (week 1)."""
    from connectors.replay import ReplayConnector

    async with tenant_tx(principal.tenant_id) as s:
        if not await s.scalar(text("SELECT is_demo FROM tenants")):
            raise HTTPException(status_code=403, detail="reset is only available in demo workspaces")
        await s.execute(text("SELECT daelys_reset_demo(:t)"), {"t": principal.tenant_id})
    result = await ReplayConnector("tiffin_box", "week1").replay(principal.tenant_id)
    await _refresh(principal.tenant_id)
    return {"status": "reset", "replayed_events": result.appended, "at": datetime.now(UTC)}


@router.get("/members")
async def list_members(ctx: AnyMember) -> list[dict[str, Any]]:
    rows = await ctx.session.execute(
        text("""
        SELECT u.id, u.display_name, u.department, m.role FROM users u JOIN memberships m ON m.user_id = u.id
        WHERE m.role <> 'agent' ORDER BY u.display_name
    """)
    )
    return [dict(r) for r in rows.mappings()]
