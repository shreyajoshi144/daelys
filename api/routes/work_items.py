from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import text

from api.deps import AnyMember
from api.schemas import WorkItemOut, WorkItemPage

router = APIRouter(prefix="/work-items", tags=["work-items"])


@router.get("", response_model=WorkItemPage)
async def list_work_items(
    ctx: AnyMember,
    status_filter: Annotated[str | None, Query(alias="status", max_length=20)] = None,
    kind: Annotated[str | None, Query(max_length=10)] = None,
    project_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> WorkItemPage:
    sql = text("""
        SELECT id, key, kind, title, status, lane, owner_id, owner_login, due_at, url, ci_status,
            last_activity_at, created_at, updated_at, project_id, owner_role, estimate_days, source_span,
            risk_score, risk_level, projected_slip_days
        FROM work_items
        WHERE (CAST(:status AS text) IS NULL OR status = :status)
          AND (CAST(:kind AS text) IS NULL OR kind = :kind)
          AND (CAST(:project AS uuid) IS NULL OR project_id = :project)
        ORDER BY updated_at DESC, id LIMIT :limit OFFSET :offset
    """)
    rows = await ctx.session.execute(
        sql, {"status": status_filter, "kind": kind, "project": project_id, "limit": limit, "offset": offset}
    )
    return WorkItemPage(
        items=[WorkItemOut.model_validate(r) for r in rows.mappings()], limit=limit, offset=offset
    )


@router.get("/{item_id}", response_model=WorkItemOut)
async def get_work_item(item_id: UUID, ctx: AnyMember) -> WorkItemOut:
    # RLS makes another tenant's id indistinguishable from a missing one: always 404, never 403.
    result = await ctx.session.execute(
        text("""
            SELECT id, key, kind, title, status, lane, owner_id, owner_login, due_at, url, ci_status,
                last_activity_at, created_at, updated_at, project_id, owner_role, estimate_days, source_span,
            risk_score, risk_level, projected_slip_days
            FROM work_items WHERE id = :id
        """),
        {"id": item_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="work item not found")
    return WorkItemOut.model_validate(row)
