"""Brief -> validated plan. One model call, deterministic validation, at most one repair call.

Read-only: it returns a draft and never writes work items (design rule 4).
"""

from uuid import UUID

from agents.plan_schema import LLMPlan, PlanResult
from agents.plan_validators import validate
from agents.prompt_store import load_prompt
from core.llm import LLM

PLAN_PROMPT = "plan_v1"
REPAIR_PROMPT = "plan_repair_v1"
MAX_BRIEF_CHARS = 60_000


async def plan_from_brief(llm: LLM, tenant_id: UUID, brief: str) -> PlanResult:
    if not brief.strip():
        raise ValueError("brief is empty")
    if len(brief) > MAX_BRIEF_CHARS:
        raise ValueError(f"brief longer than {MAX_BRIEF_CHARS} characters")

    prompt = load_prompt(PLAN_PROMPT)
    first = await llm.structured(
        tenant_id=tenant_id,
        agent="planner",
        prompt_version=prompt.version,
        system=prompt.system,
        user=prompt.render_user(evidence=brief),
        output_type=LLMPlan,
    )
    result = validate(first.parsed, brief)
    raw_stats = result.raw
    run_ids = [str(first.run_id)]
    repaired = False

    if result.problems:
        repair = load_prompt(REPAIR_PROMPT)
        second = await llm.structured(
            tenant_id=tenant_id,
            agent="planner",
            prompt_version=repair.version,
            system=repair.system,
            user=repair.render_user(
                problems="\n".join(f"- {p}" for p in result.problems),
                evidence=brief,
                previous=first.parsed.model_dump_json(indent=2),
            ),
            output_type=LLMPlan,
        )
        run_ids.append(str(second.run_id))
        result = validate(second.parsed, brief)
        repaired = True

    assert raw_stats is not None
    return PlanResult(
        plan=result.plan, dropped=result.dropped, repaired=repaired, raw=raw_stats, agent_run_ids=run_ids
    )
