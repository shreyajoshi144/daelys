import logging
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from agents.planner import plan_from_brief
from api.bodies import read_limited
from api.deps import AnyMember, LeadPrincipal, OwnerPrincipal
from api.schemas import (
    AcceptOut,
    AcceptRequest,
    BriefDetail,
    BriefIn,
    BriefOut,
    LeadIn,
    PlanDraftOut,
    PlanRequest,
    ProjectIn,
    ProjectOut,
)
from core import queue
from core.briefs import MAX_BYTES, UnsupportedBrief, chunk_text, embed, extract_text, store_brief
from core.config import get_settings
from core.db import tenant_tx
from core.llm import LLMUnavailable, ProviderError, get_llm
from core.plans import DraftAlreadyAccepted, DraftNotFound, accept_draft, save_draft
from core.ratelimit import allow
from core.security import Principal, Role
from rules.engine import evaluate_tenant
from workers.projector import project_tenant

router = APIRouter(prefix="/projects", tags=["projects"])
log = logging.getLogger(__name__)

_NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
_DRAFT_SELECT = text("""
    SELECT id, project_id, artifact_id, status, plan, dropped, repaired, created_at FROM plan_drafts
    WHERE id = :d AND project_id = :p
""")
_DRAFT_LIST = text("""
    SELECT id, project_id, artifact_id, status, plan, dropped, repaired, created_at FROM plan_drafts
    WHERE project_id = :p ORDER BY created_at DESC LIMIT 20
""")


async def _require_project(session: AsyncSession, project_id: UUID) -> None:
    # RLS hides other tenants' projects, so they 404 exactly like missing ones.
    if await session.scalar(text("SELECT 1 FROM projects WHERE id = :p"), {"p": project_id}) is None:
        raise _NOT_FOUND


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ProjectOut)
async def create_project(body: ProjectIn, principal: LeadPrincipal) -> ProjectOut:
    async with tenant_tx(principal.tenant_id) as s:
        row = (
            (
                await s.execute(
                    text("""
                    INSERT INTO projects (tenant_id, name, created_by) VALUES (:t, :n, :u)
                    RETURNING id, name, created_at
                """),
                    {"t": principal.tenant_id, "n": body.name.strip(), "u": principal.user_id},
                )
            )
            .mappings()
            .one()
        )
        if principal.role == Role.lead:  # the creating lead leads the project (owners decide everywhere)
            await s.execute(
                text("INSERT INTO project_members (tenant_id, project_id, user_id) VALUES (:t, :p, :u)"),
                {"t": principal.tenant_id, "p": row["id"], "u": principal.user_id},
            )
    return ProjectOut.model_validate(dict(row))


@router.post("/{project_id}/leads", status_code=status.HTTP_204_NO_CONTENT)
async def add_project_lead(project_id: UUID, body: LeadIn, principal: OwnerPrincipal) -> None:
    """Owners choose who leads a project; only project leads (and owners) decide its proposals."""
    async with tenant_tx(principal.tenant_id) as s:
        await _require_project(s, project_id)
        role = await s.scalar(text("SELECT role FROM memberships WHERE user_id = :u"), {"u": body.user_id})
        if role != Role.lead:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="user is not a lead"
            )
        await s.execute(
            text("""
                INSERT INTO project_members (tenant_id, project_id, user_id) VALUES (:t, :p, :u)
                ON CONFLICT DO NOTHING
            """),
            {"t": principal.tenant_id, "p": project_id, "u": body.user_id},
        )


@router.get("", response_model=list[ProjectOut])
async def list_projects(ctx: AnyMember) -> list[ProjectOut]:
    rows = await ctx.session.execute(
        text("SELECT id, name, created_at FROM projects ORDER BY created_at DESC LIMIT 200")
    )
    return [ProjectOut.model_validate(dict(r)) for r in rows.mappings()]


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: UUID, ctx: AnyMember) -> ProjectOut:
    row = (
        (
            await ctx.session.execute(
                text("SELECT id, name, created_at FROM projects WHERE id = :p"), {"p": project_id}
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise _NOT_FOUND
    return ProjectOut.model_validate(dict(row))


async def _ingest(
    principal: Principal, project_id: UUID, title: str, content: str, content_type: str
) -> BriefOut:
    if not content.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="brief has no text")
    async with tenant_tx(principal.tenant_id) as s:
        await _require_project(s, project_id)
    chunks = chunk_text(content)
    try:
        vectors = await embed([c.content for c in chunks])
    except (httpx.HTTPError, ValueError) as exc:
        log.error("briefs.embedding_failed", extra={"error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="embedding provider error"
        ) from exc
    async with tenant_tx(principal.tenant_id) as s:
        stored = await store_brief(
            s,
            principal.tenant_id,
            project_id,
            title,
            content,
            content_type,
            principal.user_id,
            vectors,
            chunks,
        )
    return BriefOut(artifact_id=stored.artifact_id, chunks=stored.chunks, embedded=stored.embedded)


@router.post("/{project_id}/briefs", status_code=status.HTTP_201_CREATED, response_model=BriefOut)
async def paste_brief(project_id: UUID, body: BriefIn, principal: LeadPrincipal) -> BriefOut:
    return await _ingest(principal, project_id, body.title.strip(), body.content, "text/markdown")


@router.post("/{project_id}/briefs/upload", status_code=status.HTTP_201_CREATED, response_model=BriefOut)
async def upload_brief(
    project_id: UUID,
    request: Request,
    principal: LeadPrincipal,
    title: Annotated[str, Query(min_length=1, max_length=200)],
) -> BriefOut:
    """Raw file body (markdown, plain text or PDF); the type comes from Content-Type."""
    body = await read_limited(request, MAX_BYTES, "brief too large")
    content_type = request.headers.get("content-type", "")
    try:
        content = extract_text(content_type, body)
    except UnsupportedBrief as exc:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)) from None
    return await _ingest(principal, project_id, title.strip(), content, content_type.split(";")[0].strip())


@router.get("/{project_id}/briefs", response_model=list[BriefDetail])
async def list_briefs(project_id: UUID, ctx: AnyMember) -> list[BriefDetail]:
    await _require_project(ctx.session, project_id)
    rows = await ctx.session.execute(
        text("""
            SELECT id, title, content_type, content, created_at FROM artifacts
            WHERE project_id = :p AND kind = 'brief' ORDER BY created_at DESC LIMIT 50
        """),
        {"p": project_id},
    )
    return [BriefDetail.model_validate(dict(r)) for r in rows.mappings()]


@router.get("/{project_id}/briefs/{artifact_id}", response_model=BriefDetail)
async def get_brief(project_id: UUID, artifact_id: UUID, ctx: AnyMember) -> BriefDetail:
    row = (
        (
            await ctx.session.execute(
                text("""
                SELECT id, title, content_type, content, created_at FROM artifacts
                WHERE id = :a AND project_id = :p AND kind = 'brief'
            """),
                {"a": artifact_id, "p": project_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise _NOT_FOUND
    return BriefDetail.model_validate(dict(row))


@router.post("/{project_id}/plan", status_code=status.HTTP_201_CREATED, response_model=PlanDraftOut)
async def generate_plan(project_id: UUID, body: PlanRequest, principal: LeadPrincipal) -> PlanDraftOut:
    """Draft a plan from a stored brief. Slow (LLM), so no transaction is held during the model calls."""
    if not await allow(f"llm:plan:{principal.tenant_id}", get_settings().llm_rate_limit_per_minute):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="too many planning requests"
        )
    async with tenant_tx(principal.tenant_id) as s:
        brief = await s.scalar(
            text("SELECT content FROM artifacts WHERE id = :a AND project_id = :p AND kind = 'brief'"),
            {"a": body.artifact_id, "p": project_id},
        )
    if brief is None:
        raise _NOT_FOUND
    llm = get_llm()  # raises NotConfigured -> 503 when no key is set
    try:
        result = await plan_from_brief(llm, principal.tenant_id, brief)
    except (LLMUnavailable, ProviderError) as exc:
        log.error("plan.llm_failed", extra={"project": str(project_id), "error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="language model unavailable"
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    async with tenant_tx(principal.tenant_id) as s:
        draft_id = await save_draft(
            s, principal.tenant_id, project_id, body.artifact_id, result, principal.user_id
        )
        return await _load_draft(s, project_id, draft_id)


async def _load_draft(session: AsyncSession, project_id: UUID, draft_id: UUID) -> PlanDraftOut:
    row = (
        (
            await session.execute(
                _DRAFT_SELECT,
                {"d": draft_id, "p": project_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise _NOT_FOUND
    return PlanDraftOut.model_validate(dict(row))


@router.get("/{project_id}/plan/drafts", response_model=list[PlanDraftOut])
async def list_drafts(project_id: UUID, ctx: AnyMember) -> list[PlanDraftOut]:
    await _require_project(ctx.session, project_id)
    rows = await ctx.session.execute(
        _DRAFT_LIST,
        {"p": project_id},
    )
    return [PlanDraftOut.model_validate(dict(r)) for r in rows.mappings()]


@router.get("/{project_id}/plan/drafts/{draft_id}", response_model=PlanDraftOut)
async def get_draft(project_id: UUID, draft_id: UUID, ctx: AnyMember) -> PlanDraftOut:
    return await _load_draft(ctx.session, project_id, draft_id)


@router.post("/{project_id}/plan/accept", response_model=AcceptOut)
async def accept_plan(project_id: UUID, body: AcceptRequest, principal: LeadPrincipal) -> AcceptOut:
    """Writes plan events; work items are then rebuilt from the event log by the projector."""
    try:
        async with tenant_tx(principal.tenant_id) as s:
            result = await accept_draft(s, principal.tenant_id, project_id, body.draft_id, principal.user_id)
    except DraftNotFound:
        raise _NOT_FOUND from None
    except DraftAlreadyAccepted:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="plan already accepted") from None
    try:
        await project_tenant(principal.tenant_id)  # so the lanes show the items (and their risk) right away
        await evaluate_tenant(principal.tenant_id)
    except DBAPIError:
        log.exception("plan.inline_projection_failed", extra={"tenant": str(principal.tenant_id)})
        await queue.enqueue_projection(principal.tenant_id)
    return AcceptOut(items=result.items, edges=result.edges, inserted_events=result.inserted_events)
