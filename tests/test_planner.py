"""Planner orchestration with a scripted model: first answer is cyclic, the one repair call fixes it."""

from typing import Any

import pytest

from agents.plan_schema import LLMPlan
from agents.planner import plan_from_brief
from core.llm import LLM, Completion, Route
from tests.conftest import SeededTenant

BRIEF = "Build checkout.\nSend launch emails after checkout.\nLaunch is 20 March. Or is it 27 March?\n"


def plan(
    items: list[tuple[str, str, list[str]]], questions: list[tuple[str, list[str]]] | None = None
) -> LLMPlan:
    return LLMPlan.model_validate(
        {
            "items": [
                {
                    "local_id": i,
                    "title": f"Do {q}",
                    "lane": "Engineering",
                    "owner_role": "eng",
                    "estimate_days": 2,
                    "depends_on": d,
                    "source_quote": q,
                }
                for i, q, d in items
            ],
            "open_questions": [{"text": t, "source_quotes": qs} for t, qs in questions or []],
        }
    )


class Scripted:
    name = "scripted"

    def __init__(self, answers: list[LLMPlan]) -> None:
        self.answers = answers
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self, model: str, system: str, user: str, output_type: Any, max_tokens: int
    ) -> Completion[Any]:
        self.calls.append({"system": system, "user": user})
        return Completion(parsed=self.answers.pop(0), input_tokens=10, output_tokens=10)


async def test_repair_retry_then_valid_plan(fresh_tenant: SeededTenant) -> None:
    cyclic = plan([("a", "Build checkout.", ["b"]), ("b", "Send launch emails", ["a"])])
    fixed = plan(
        [("a", "Build checkout.", []), ("b", "Send launch emails", ["a"])],
        [("Which launch date?", ["20 March", "27 March"])],
    )
    model = Scripted([cyclic, fixed])
    result = await plan_from_brief(LLM([Route("scripted", "m")], {"scripted": model}), fresh_tenant.id, BRIEF)
    assert result.repaired and len(result.agent_run_ids) == 2
    assert result.raw.had_cycle and result.raw.items == 2
    assert [(i.local_id, i.depends_on) for i in result.plan.items] == [("a", []), ("b", ["a"])]
    assert len(result.plan.open_questions) == 1 and result.dropped == []
    assert "dependency cycle" in model.calls[1]["user"]
    for call in model.calls:
        assert "Build checkout" not in call["system"] and "Build checkout" in call["user"]


async def test_clean_plan_makes_one_call(fresh_tenant: SeededTenant) -> None:
    model = Scripted([plan([("a", "Build checkout.", [])])])
    result = await plan_from_brief(LLM([Route("scripted", "m")], {"scripted": model}), fresh_tenant.id, BRIEF)
    assert not result.repaired and len(model.calls) == 1


async def test_rejects_empty_brief(fresh_tenant: SeededTenant) -> None:
    with pytest.raises(ValueError):
        await plan_from_brief(
            LLM([Route("scripted", "m")], {"scripted": Scripted([])}), fresh_tenant.id, "  "
        )
