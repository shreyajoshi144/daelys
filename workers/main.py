"""arq worker: `arq workers.main.WorkerSettings`."""

import logging
from typing import Any, ClassVar
from uuid import UUID

from arq import cron, func
from sqlalchemy.exc import DBAPIError

from connectors.github import GitHubConnector
from connectors.replay import ReplayConnector
from core import meetings
from core.config import get_settings
from core.db import all_tenant_ids, get_engine
from core.errors import NotConfigured
from core.insights import detect_duplicates
from core.llm import get_llm
from core.logging import configure_logging
from core.queue import redis_settings
from rules.engine import evaluate_tenant
from workers.execute import execute
from workers.execute import sweep as sweep_proposals
from workers.projector import project_tenant

log = logging.getLogger(__name__)


async def project(ctx: dict[str, Any], tenant_id: str) -> int:
    """Project new events, then re-evaluate risk (rules run after each projection batch)."""
    count = await project_tenant(UUID(tenant_id))
    if count:
        await evaluate_tenant(UUID(tenant_id))
        await _duplicates(UUID(tenant_id))
    return count


async def replay_scenario(
    ctx: dict[str, Any], tenant_id: str, scenario: str, timelines: list[str], speed: float
) -> int:
    """Time machine: replay a scenario into a fresh tenant copy; each event triggers projection + risk +
    SSE."""
    tid = UUID(tenant_id)

    async def after_event() -> None:
        await ctx["redis"].enqueue_job("project", tenant_id, _job_id=f"project:{tenant_id}")

    appended = 0
    for timeline in timelines:
        result = await ReplayConnector(scenario, timeline).replay(
            tid, speed=speed or None, on_event=after_event
        )
        appended += result.appended
    await project_tenant(tid)
    await evaluate_tenant(tid)
    return appended


async def execute_proposal(ctx: dict[str, Any], tenant_id: str, proposal_id: str) -> str:
    try:
        outcome = await execute(UUID(tenant_id), UUID(proposal_id))
    except NotConfigured as exc:
        # Left approved: it runs once the integration is configured and the job is re-queued.
        log.warning("execute.not_configured", extra={"proposal": proposal_id, "detail": str(exc)})
        return "not_configured"
    return outcome.status


async def proposals_sweep(ctx: dict[str, Any]) -> None:
    for tenant_id in await all_tenant_ids():
        result = await sweep_proposals(tenant_id)
        if any(result.values()):
            log.info("proposals.swept", extra={"tenant": str(tenant_id), **result})


async def extract_meeting(ctx: dict[str, Any], tenant_id: str, meeting_id: str) -> str:
    try:
        llm = get_llm()
    except NotConfigured as exc:
        log.warning("meetings.not_configured", extra={"meeting": meeting_id, "detail": str(exc)})
        return "not_configured"
    try:
        summary = await meetings.extract_meeting(llm, UUID(tenant_id), UUID(meeting_id))
    except meetings.AlreadyExtracted:
        return "already_extracted"
    await project_tenant(UUID(tenant_id))
    await evaluate_tenant(UUID(tenant_id))
    return f"{summary.action_items} actions, {summary.proposals} proposals"


async def _duplicates(tenant_id: UUID) -> None:
    """Suggest duplicates for new items when a model is configured (suggestions only, never merges)."""
    try:
        llm = get_llm()
    except NotConfigured:
        return
    await detect_duplicates(llm, tenant_id)


async def evaluate(ctx: dict[str, Any], tenant_id: str) -> int:
    return len((await evaluate_tenant(UUID(tenant_id))).signals)


async def risk_sweep(ctx: dict[str, Any]) -> None:
    """Risk depends on time as well as events (a PR goes stale with no new events), so re-evaluate
    periodically."""
    for tenant_id in await all_tenant_ids():
        await ctx["redis"].enqueue_job("evaluate", str(tenant_id), _job_id=f"evaluate:{tenant_id}")


async def github_backfill(ctx: dict[str, Any], tenant_id: str) -> int:
    count = await GitHubConnector().backfill(UUID(tenant_id))
    if await project_tenant(UUID(tenant_id)):
        await evaluate_tenant(UUID(tenant_id))
    return count


async def sweep(ctx: dict[str, Any]) -> None:
    """Safety net for lost enqueues and the resume path after a restart: the projector continues from
    each tenant's `connector_cursors` row."""
    for tenant_id in await all_tenant_ids():
        await ctx["redis"].enqueue_job("project", str(tenant_id), _job_id=f"project:{tenant_id}")


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging(get_settings().log_level)
    try:
        await sweep(ctx)
    except DBAPIError:
        # e.g. first boot before `make migrate`; the cron sweep retries every 30 s.
        log.exception("worker.startup_sweep_failed")
    log.info("worker.started")


async def shutdown(ctx: dict[str, Any]) -> None:
    await get_engine().dispose()


class WorkerSettings:
    functions: ClassVar[list[Any]] = [
        func(project, keep_result=0, max_tries=5, timeout=300),
        func(github_backfill, keep_result=3600, max_tries=3, timeout=900),
        func(evaluate, keep_result=0, max_tries=3, timeout=300),
        func(replay_scenario, keep_result=3600, max_tries=1, timeout=1800),
        # One try: the executor never re-runs an interrupted external action; the sweep settles it instead.
        func(execute_proposal, keep_result=3600, max_tries=1, timeout=120),
        func(extract_meeting, keep_result=3600, max_tries=2, timeout=900),
    ]
    cron_jobs: ClassVar[list[Any]] = [
        cron(sweep, second={0, 30}, run_at_startup=False),
        cron(risk_sweep, minute=set(range(0, 60, 10)), second=0, run_at_startup=False),
        cron(proposals_sweep, second=15, run_at_startup=False),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = redis_settings()
    max_jobs = 10
