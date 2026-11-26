"""Confirms (or rejects) a retrieval candidate for a meeting item. Read-only. Never merges anything itself."""

import json
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from agents.prompt_store import load_prompt
from core.llm import LLM

PROMPT = "match_v1"


class MatchVerdict(BaseModel):
    match_key: str | None
    reason: str


async def confirm(
    llm: LLM, tenant_id: UUID, item_text: str, candidates: list[dict[str, Any]]
) -> MatchVerdict:
    """Returns the confirmed candidate key, or None. A key that isn't a candidate is treated as no match."""
    prompt = load_prompt(PROMPT)
    result = await llm.structured(
        tenant_id=tenant_id,
        agent="matcher",
        prompt_version=prompt.version,
        system=prompt.system,
        user=prompt.render_user(
            item=item_text,
            candidates=json.dumps(
                [{"key": c["key"], "title": c["title"]} for c in candidates], ensure_ascii=False
            ),
        ),
        output_type=MatchVerdict,
        max_tokens=1000,
    )
    verdict = result.parsed
    if verdict.match_key not in {c["key"] for c in candidates}:
        verdict.match_key = None
    return verdict
