"""Plan models. `LLM*` is what the model returns (verbatim quotes); the validated `Plan` carries character
offsets into the brief instead, computed by us, so every item provably maps to real brief text."""

from typing import Literal

from pydantic import BaseModel, Field

Lane = Literal["Product", "Engineering", "Marketing"]


class LLMItem(BaseModel):
    local_id: str
    title: str
    lane: Lane
    owner_role: str
    estimate_days: float
    depends_on: list[str]
    source_quote: str


class LLMQuestion(BaseModel):
    text: str
    source_quotes: list[str]


class LLMPlan(BaseModel):
    items: list[LLMItem]
    open_questions: list[LLMQuestion]


class SourceSpan(BaseModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class WorkItemDraft(BaseModel):
    local_id: str
    title: str
    lane: Lane
    owner_role: str
    estimate_days: float = Field(gt=0, le=365)
    depends_on: list[str]
    source_span: SourceSpan


class OpenQuestion(BaseModel):
    text: str
    source_spans: list[SourceSpan]


class Plan(BaseModel):
    items: list[WorkItemDraft]
    open_questions: list[OpenQuestion]


class Dropped(BaseModel):
    kind: Literal["item", "dependency", "question"]
    ref: str
    reason: str


class RawStats(BaseModel):
    """Measured on the model's first answer, before any repair (used by DaelysBench)."""

    items: int
    uncited_items: int
    had_cycle: bool


class PlanResult(BaseModel):
    plan: Plan
    dropped: list[Dropped]
    repaired: bool
    raw: RawStats
    agent_run_ids: list[str]
