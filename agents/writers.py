"""LLM text producers that go through the citation checker: weekly summary, Ask Daelys, duplicate judge.
All read-only: they return checked output; callers decide what to store."""

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from agents.citations import CheckedText, check
from agents.prompt_store import load_prompt
from core.llm import LLM

NO_EVIDENCE = "Not enough evidence in this workspace to answer that."
TEXT_LIMIT = 500


class Sentences(BaseModel):
    sentences: list[str]


class DuplicateVerdict(BaseModel):
    duplicate: bool
    reason: str


@dataclass
class Written:
    checked: CheckedText
    agent_run_id: UUID
    model: str


def _event_line(e: dict[str, Any]) -> str:
    payload = {k: e["payload"][k] for k in ("fields", "text", "quotes", "conclusion") if k in e["payload"]}
    if e["payload"].get("body"):
        payload["body"] = str(e["payload"]["body"])[:TEXT_LIMIT]
    return f"[evt:{e['id']}] {e['occurred_at']} {e['type']} on {e.get('subject_key') or '-'}: " + json.dumps(
        payload, ensure_ascii=False, default=str
    )


async def weekly_summary(
    llm: LLM, tenant_id: UUID, scope: str, flags: list[dict[str, Any]], events: list[dict[str, Any]]
) -> Written:
    prompt = load_prompt("summary_v1")
    result = await llm.structured(
        tenant_id=tenant_id,
        agent="summarizer",
        prompt_version=prompt.version,
        system=prompt.system,
        user=prompt.render_user(
            scope=scope,
            flags=json.dumps(flags, default=str),
            events="\n".join(_event_line(e) for e in events) or "(no events)",
        ),
        output_type=Sentences,
        max_tokens=3000,
    )
    return Written(
        check(result.parsed.sentences, {e["id"] for e in events}), result.run_id, result.route.model
    )


async def answer(
    llm: LLM, tenant_id: UUID, question: str, docs: list[dict[str, Any]], events: list[dict[str, Any]]
) -> Written:
    prompt = load_prompt("ask_v1")
    documents = "\n".join(f"[doc:{d['id']}] ({d['artifact_title']}) {d['content'][:1500]}" for d in docs)
    result = await llm.structured(
        tenant_id=tenant_id,
        agent="ask",
        prompt_version=prompt.version,
        system=prompt.system,
        user=prompt.render_user(
            question=question,
            documents=documents or "(none)",
            events="\n".join(_event_line(e) for e in events) or "(none)",
        ),
        output_type=Sentences,
        max_tokens=2000,
    )
    checked = check(result.parsed.sentences, {e["id"] for e in events}, frozenset(str(d["id"]) for d in docs))
    return Written(checked, result.run_id, result.route.model)


async def judge_duplicate(
    llm: LLM, tenant_id: UUID, a: dict[str, Any], b: dict[str, Any]
) -> tuple[DuplicateVerdict, UUID]:
    prompt = load_prompt("duplicate_v1")
    fields = ("title", "kind", "lane", "status")
    result = await llm.structured(
        tenant_id=tenant_id,
        agent="duplicate_judge",
        prompt_version=prompt.version,
        system=prompt.system,
        user=prompt.render_user(
            a=json.dumps({k: a.get(k) for k in fields}), b=json.dumps({k: b.get(k) for k in fields})
        ),
        output_type=DuplicateVerdict,
        max_tokens=500,
    )
    return result.parsed, result.run_id
