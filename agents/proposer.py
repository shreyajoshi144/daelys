"""Proposer: suggests typed fixes for one at-risk item. It reads (through read-only tools) and returns
validated drafts; it never writes anything (design rule 4). Persisting proposals and executing them happen
elsewhere.

LangGraph flow:  gather (read-only tools) -> propose (one model call) -> validate (deterministic) -> END"""

import json
import logging
from types import SimpleNamespace
from typing import Any, TypedDict
from uuid import UUID

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from agents import tools
from agents.prompt_store import load_prompt
from core.actions import ACTIONS, TIERS, InvalidAction, check_against_state, parse_args
from core.db import fetch_one, readonly_tenant_tx
from core.llm import LLM
from rules.engine import tenant_now

log = logging.getLogger(__name__)

PROMPT = "propose_v1"
MAX_PROPOSALS = 3


class LLMSplitPart(BaseModel):
    title: str
    estimate_days: float


class LLMProposal(BaseModel):
    """Flat on purpose: `action_type` is free text so an off-list action is visible and can be rejected +
    logged."""

    action_type: str = Field(description="one of: reassign, move_due, split, notify, comment_on_pr")
    to_user_id: str | None = None
    new_due_date: str | None = None
    parts: list[LLMSplitPart] | None = None
    to_user_ids: list[str] | None = None
    subject: str | None = None
    body: str | None = None
    evidence_event_ids: list[int]
    expected_effect: str
    reversible: bool


class LLMProposalSet(BaseModel):
    proposals: list[LLMProposal]


class Draft(BaseModel):
    action_type: str
    args: dict[str, Any]
    tier: int
    evidence_event_ids: list[int]
    expected_effect: str
    reversible: bool


class Rejected(BaseModel):
    action_type: str
    reason: str


class ProposerResult(BaseModel):
    item_id: UUID
    drafts: list[Draft]
    rejected: list[Rejected]
    agent_run_id: UUID | None
    prompt_version: str
    model: str | None


class State(TypedDict, total=False):
    tenant_id: UUID
    item_id: UUID
    llm: LLM
    context: dict[str, Any]
    raw: LLMProposalSet
    run_id: UUID
    model: str
    result: ProposerResult


def _args_for(p: LLMProposal) -> dict[str, Any]:
    if p.action_type == "reassign":
        return {"to_user_id": p.to_user_id}
    if p.action_type == "move_due":
        return {"new_due_date": p.new_due_date}
    if p.action_type == "split":
        return {"parts": [part.model_dump() for part in p.parts or []]}
    if p.action_type == "notify":
        return {"to_user_ids": p.to_user_ids or [], "subject": p.subject, "body": p.body}
    return {"body": p.body}


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=1, default=str)


async def gather(state: State) -> State:
    async with readonly_tenant_tx(state["tenant_id"]) as s:
        item = await tools.get_item(s, state["item_id"])
        if item is None:
            raise LookupError("work item not found")
        state["context"] = {
            "item": item,
            "dependents": await tools.get_dependents(s, state["item_id"]),
            "team": await tools.get_owner_load(s),
            "events": await tools.search_events(s, item["key"]),
            "rejections": await tools.get_rejections(s, state["item_id"]),
            "now": await tenant_now(s),
        }
    return state


async def propose(state: State) -> State:
    ctx = state["context"]
    prompt = load_prompt(PROMPT)
    events = "\n".join(
        f"[evt:{e['id']}] {e['occurred_at']} {e['type']} on {e['subject_key']} by {e['actor'] or 'unknown'}: "
        + _dump(e["payload"])
        for e in ctx["events"]
    )
    result = await state["llm"].structured(
        tenant_id=state["tenant_id"],
        agent="proposer",
        prompt_version=prompt.version,
        system=prompt.system,
        user=prompt.render_user(
            context=_dump({"today": ctx["now"].date().isoformat(), "max_proposals": MAX_PROPOSALS}),
            item=_dump(ctx["item"]),
            dependents=_dump(ctx["dependents"]),
            team=_dump(ctx["team"]),
            events=events or "(no events)",
            rejections=_dump(ctx["rejections"]),
        ),
        output_type=LLMProposalSet,
        max_tokens=4000,
    )
    state["raw"], state["run_id"], state["model"] = result.parsed, result.run_id, result.route.model
    return state


async def validate(state: State) -> State:
    ctx = state["context"]
    allowed_evidence = {e["id"] for e in ctx["events"]}
    drafts: list[Draft] = []
    rejected: list[Rejected] = []

    def reject(action: str, reason: str) -> None:
        rejected.append(Rejected(action_type=action[:50], reason=reason))
        log.warning(
            "proposer.draft_rejected",
            extra={"item": str(state["item_id"]), "action": action[:50], "reason": reason},
        )

    async with readonly_tenant_tx(state["tenant_id"]) as s:
        item = await fetch_one(s, "SELECT * FROM work_items WHERE id = :id", {"id": state["item_id"]})
        assert item is not None
        row = SimpleNamespace(**item)  # attribute access for the shared state checks
        for p in state["raw"].proposals:
            if len(drafts) >= MAX_PROPOSALS:
                reject(p.action_type, f"more than {MAX_PROPOSALS} proposals")
                continue
            if p.action_type not in ACTIONS:
                reject(p.action_type, "not one of the five typed actions")
                continue
            try:
                args = parse_args(p.action_type, _args_for(p))
            except InvalidAction as exc:
                reject(p.action_type, str(exc))
                continue
            evidence = list(dict.fromkeys(p.evidence_event_ids))
            if not evidence or not set(evidence) <= allowed_evidence:
                reject(p.action_type, "evidence must cite events from the evidence block")
                continue
            if problems := await check_against_state(s, p.action_type, args, row, state["context"]["now"]):
                reject(p.action_type, "; ".join(problems))
                continue
            normalized = args.model_dump(mode="json")
            if any(d.action_type == p.action_type and d.args == normalized for d in drafts):
                reject(p.action_type, "duplicate proposal")
                continue
            tier = TIERS[p.action_type]
            drafts.append(
                Draft(
                    action_type=p.action_type,
                    args=normalized,
                    tier=tier,
                    evidence_event_ids=evidence,
                    expected_effect=p.expected_effect.strip()[:500] or "(none given)",
                    reversible=tier == 2,  # the truth, whatever the model claimed
                )
            )
    state["result"] = ProposerResult(
        item_id=state["item_id"],
        drafts=drafts,
        rejected=rejected,
        agent_run_id=state["run_id"],
        prompt_version=PROMPT,
        model=state["model"],
    )
    return state


def build_graph() -> Any:
    graph = StateGraph(State)
    graph.add_node("gather", gather)
    graph.add_node("propose", propose)
    graph.add_node("validate", validate)
    graph.set_entry_point("gather")
    graph.add_edge("gather", "propose")
    graph.add_edge("propose", "validate")
    graph.add_edge("validate", END)
    return graph.compile()


_GRAPH = build_graph()


async def run_proposer(llm: LLM, tenant_id: UUID, item_id: UUID) -> ProposerResult:
    final = await _GRAPH.ainvoke({"tenant_id": tenant_id, "item_id": item_id, "llm": llm})
    result: ProposerResult = final["result"]
    return result
