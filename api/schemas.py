from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from agents.plan_schema import Dropped, Plan


class LoginRequest(BaseModel):
    tenant: str = Field(min_length=2, max_length=63)
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+$")
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth token type, not a secret
    expires_in: int


class WorkItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    key: str
    kind: str
    title: str | None
    status: str
    lane: str | None
    owner_id: UUID | None
    owner_login: str | None
    due_at: datetime | None
    url: str | None
    ci_status: str | None
    project_id: UUID | None
    owner_role: str | None
    estimate_days: float | None
    source_span: dict[str, int] | None
    risk_score: int
    risk_level: str
    projected_slip_days: float
    last_activity_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WorkItemPage(BaseModel):
    items: list[WorkItemOut]
    limit: int
    offset: int


class EventOut(BaseModel):
    id: int
    source: str
    type: str
    occurred_at: datetime
    ingested_at: datetime
    actor: str | None
    subject_key: str | None
    payload: dict[str, Any]


class EventPage(BaseModel):
    items: list[EventOut]
    next_after_id: int | None


class WebhookAck(BaseModel):
    status: str
    inserted: int = 0
    duplicates: int = 0
    reason: str | None = None


class JobAccepted(BaseModel):
    job_id: str | None


class MeOut(BaseModel):
    user_id: UUID
    display_name: str
    email: str
    tenant_name: str
    role: str
    is_demo: bool
    clock_now: datetime  # the workspace clock: wall time, or the latest event time for replayed scenarios


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ProjectOut(BaseModel):
    id: UUID
    name: str
    created_at: datetime


class BriefIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=60_000)


class BriefOut(BaseModel):
    artifact_id: UUID
    chunks: int
    embedded: bool


class BriefDetail(BaseModel):
    id: UUID
    title: str
    content_type: str
    content: str
    created_at: datetime


class PlanRequest(BaseModel):
    artifact_id: UUID


class PlanDraftOut(BaseModel):
    id: UUID
    project_id: UUID
    artifact_id: UUID
    status: str
    plan: Plan
    dropped: list[Dropped]
    repaired: bool
    created_at: datetime


class AcceptRequest(BaseModel):
    draft_id: UUID


class AcceptOut(BaseModel):
    items: int
    edges: int
    inserted_events: int


class LeadIn(BaseModel):
    user_id: UUID


class GenerateOut(BaseModel):
    created: list[UUID]
    auto_approved: list[UUID]
    rejected: list[dict[str, str]]


class ApproveIn(BaseModel):
    args: dict[str, Any] | None = None


class RejectIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)
