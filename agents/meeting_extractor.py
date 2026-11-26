"""Meeting transcript -> decisions, action items, blockers, each citing transcript lines.

Read-only (no writes)."""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from uuid import UUID

from pydantic import BaseModel

from agents.prompt_store import load_prompt
from connectors.notiv import Line, render
from core.llm import LLM

PROMPT = "meeting_v1"
MAX_DUE_DAYS = 120


class LLMDecision(BaseModel):
    text: str
    lines: list[int]


class LLMAction(BaseModel):
    text: str
    owner_speaker: str | None
    due_date: str | None
    lines: list[int]


class LLMMeeting(BaseModel):
    decisions: list[LLMDecision]
    action_items: list[LLMAction]
    blockers: list[LLMDecision]


@dataclass
class Extracted:
    decisions: list[LLMDecision] = field(default_factory=list)
    action_items: list[LLMAction] = field(default_factory=list)
    blockers: list[LLMDecision] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    agent_run_id: UUID | None = None
    model: str | None = None


def validate(raw: LLMMeeting, lines: list[Line], meeting_day: date) -> Extracted:
    """Keep only items citing real lines; owners must be transcript speakers; dates plausible."""
    ids = {line.n for line in lines}
    speakers = {line.speaker for line in lines if line.speaker}
    out = Extracted()

    def cited(kind: str, item_text: str, item_lines: list[int]) -> bool:
        if not item_text.strip() or not item_lines or not set(item_lines) <= ids:
            out.dropped.append(f"{kind}: {item_text[:80]!r} cites no valid transcript line")
            return False
        return True

    out.decisions = [d for d in raw.decisions if cited("decision", d.text, d.lines)]
    out.blockers = [b for b in raw.blockers if cited("blocker", b.text, b.lines)]
    for action in raw.action_items:
        if not cited("action", action.text, action.lines):
            continue
        if action.owner_speaker and action.owner_speaker not in speakers:
            out.dropped.append(f"owner {action.owner_speaker!r} is not a speaker; owner cleared")
            action.owner_speaker = None
        if action.due_date:
            try:
                due = date.fromisoformat(action.due_date)
            except ValueError:
                due = None
            if due is None or not meeting_day - timedelta(days=1) <= due <= meeting_day + timedelta(
                days=MAX_DUE_DAYS
            ):
                out.dropped.append(f"due date {action.due_date!r} not plausible; cleared")
                action.due_date = None
        out.action_items.append(action)
    return out


async def extract(llm: LLM, tenant_id: UUID, lines: list[Line], meeting_day: date, title: str) -> Extracted:
    prompt = load_prompt(PROMPT)
    weekday = meeting_day.strftime("%A")
    safe_title = re.sub(r"[<>]", "", title)
    result = await llm.structured(
        tenant_id=tenant_id,
        agent="meeting_extractor",
        prompt_version=prompt.version,
        system=prompt.system,
        user=prompt.render_user(
            context=f"meeting date: {meeting_day.isoformat()} ({weekday}); title: {safe_title}",
            transcript=render(lines),
        ),
        output_type=LLMMeeting,
        max_tokens=8000,
    )
    extracted = validate(result.parsed, lines, meeting_day)
    extracted.agent_run_id, extracted.model = result.run_id, result.route.model
    return extracted
