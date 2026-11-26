"""Explains one signal from its evidence events. Read-only: returns checked sentences, never writes.
The score is not an input the model can change (design rule 2); it only explains the deterministic flag."""

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from agents.citations import CheckedText, check
from agents.prompt_store import load_prompt
from core.llm import LLM

PROMPT = "explain_v1"
BODY_LIMIT = 600


class Explanation(BaseModel):
    sentences: list[str]


@dataclass(frozen=True)
class EvidenceEvent:
    id: int
    type: str
    occurred_at: str
    actor: str | None
    subject_key: str | None
    payload: dict[str, Any]


def evidence_block(events: list[EvidenceEvent]) -> str:
    lines = []
    for ev in events:
        payload = {k: v for k, v in ev.payload.items() if k in ("fields", "text", "body", "conclusion")}
        if "body" in payload:
            payload["body"] = str(payload["body"])[:BODY_LIMIT]
        lines.append(
            f"[evt:{ev.id}] {ev.occurred_at} {ev.type} on {ev.subject_key or '-'} "
            f"by {ev.actor or 'unknown'}: " + json.dumps(payload, ensure_ascii=False, default=str)
        )
    return "\n".join(lines)


def describe_signal(rule: str, severity: str, details: dict[str, Any], item_title: str | None) -> str:
    return json.dumps(
        {"item": item_title, "rule": rule, "severity": severity, "details": details}, default=str
    )


async def explain(
    llm: LLM, tenant_id: UUID, signal_description: str, events: list[EvidenceEvent]
) -> CheckedText:
    if not events:
        raise ValueError("a signal without evidence cannot be explained")
    prompt = load_prompt(PROMPT)
    result = await llm.structured(
        tenant_id=tenant_id,
        agent="explainer",
        prompt_version=prompt.version,
        system=prompt.system,
        user=prompt.render_user(signal=signal_description, evidence=evidence_block(events)),
        output_type=Explanation,
        max_tokens=2000,
    )
    return check(result.parsed.sentences, {ev.id for ev in events})
