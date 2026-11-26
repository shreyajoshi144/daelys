"""Each rule, the propagation CTE (chain + diamond), scoring, and engine idempotency, on hand-built state.

State is built the real way: events appended to the log and folded by the projector."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text

from connectors.replay import ReplayConnector
from core.db import tenant_tx
from core.events import NewEvent, append_events
from core.seed import set_policy
from rules import scoring
from rules.engine import evaluate, evaluate_tenant, health_summary, propagate, tenant_now
from tests.conftest import SeededTenant
from workers.projector import project_tenant

NOW = datetime(2026, 4, 10, 12, 0, tzinfo=UTC)
_counter = 0


def ev(kind: str, key: str, at: datetime, payload: dict[str, Any], source: str = "test") -> NewEvent:
    global _counter
    _counter += 1
    return NewEvent(
        source=source,
        type=kind,
        idempotency_key=f"t{_counter}",
        occurred_at=at,
        subject_key=key,
        payload=payload,
    )


def task(
    key: str,
    due_in_days: float | None,
    *,
    est: float = 1,
    status: str = "open",
    deps: list[str] | None = None,
    owner: str | None = None,
    at: datetime = NOW - timedelta(days=10),
) -> NewEvent:
    fields: dict[str, Any] = {"kind": "task", "title": key, "status": status, "estimate_days": est}
    if due_in_days is not None:
        fields["due_at"] = (NOW + timedelta(days=due_in_days)).isoformat()
    if owner:
        fields["owner_login"] = owner
    payload: dict[str, Any] = {"fields": fields}
    if deps:
        payload["depends_on"] = deps
    return ev("plan.item_created", key, at, payload)


def issue_with_pr(
    n: int, due_in_days: float, *, pr_idle_days: float, ci: str | None = None
) -> list[NewEvent]:
    issue, pr = f"gh:o/r#{n}", f"gh:o/r#{n + 100}"
    out = [
        ev(
            "issue.opened",
            issue,
            NOW - timedelta(days=20),
            {
                "fields": {
                    "kind": "issue",
                    "title": f"Issue {n}",
                    "status": "open",
                    "due_at": (NOW + timedelta(days=due_in_days)).isoformat(),
                }
            },
        ),
        ev(
            "pr.opened",
            pr,
            NOW - timedelta(days=pr_idle_days),
            {"fields": {"kind": "pr", "title": f"PR {n}", "status": "in_review"}, "links": [issue]},
        ),
    ]
    if ci:
        out.append(
            ev(
                "ci.completed",
                pr,
                NOW - timedelta(days=pr_idle_days),
                {"fields": {"kind": "pr", "ci_checks.test": ci}},
            )
        )
    return out


async def build(tenant: SeededTenant, events: list[NewEvent]) -> None:
    async with tenant_tx(tenant.id) as s:
        await append_events(s, tenant.id, events)
    await project_tenant(tenant.id)


async def run(tenant: SeededTenant, as_of: datetime = NOW) -> Any:
    async with tenant_tx(tenant.id) as s:
        return await evaluate(s, as_of)


async def by_key(tenant: SeededTenant) -> dict[str, Any]:
    async with tenant_tx(tenant.id) as s:
        rows = await s.execute(
            text("SELECT id, key, risk_score, risk_level, projected_slip_days FROM work_items")
        )
        return {r.key: r for r in rows}


def rules_for(result: Any, item_id: UUID) -> dict[str, Any]:
    return {s.rule: s for s in result.signals if s.work_item_id == item_id}


async def test_stale_pr(fresh_tenant: SeededTenant) -> None:
    await build(
        fresh_tenant,
        issue_with_pr(1, 1.5, pr_idle_days=4)
        + issue_with_pr(2, 1.5, pr_idle_days=1)
        + issue_with_pr(3, 30, pr_idle_days=10),
    )
    result = await run(fresh_tenant)
    items = await by_key(fresh_tenant)
    stale = rules_for(result, items["gh:o/r#1"].id)["stale_pr"]
    assert stale.severity == "high" and stale.details["idle_days"] == 4.0 and len(stale.evidence) == 2
    assert "stale_pr" not in rules_for(result, items["gh:o/r#2"].id)  # PR active recently
    assert "stale_pr" not in rules_for(result, items["gh:o/r#3"].id)  # due far away


async def test_ci_failing_near_due(fresh_tenant: SeededTenant) -> None:
    await build(
        fresh_tenant,
        issue_with_pr(1, 0.5, pr_idle_days=0.1, ci="failure")
        + issue_with_pr(2, 3, pr_idle_days=0.1, ci="failure")
        + issue_with_pr(3, 3, pr_idle_days=0.1, ci="success"),
    )
    result = await run(fresh_tenant)
    items = await by_key(fresh_tenant)
    assert rules_for(result, items["gh:o/r#1"].id)["ci_failing_near_due"].severity == "critical"
    high = rules_for(result, items["gh:o/r#2"].id)["ci_failing_near_due"]
    assert high.severity == "high"
    async with tenant_tx(fresh_tenant.id) as s:
        types = set(
            await s.scalars(text("SELECT type FROM events WHERE id = ANY(:ids)"), {"ids": high.evidence})
        )
    assert types == {"ci.completed", "issue.opened"}  # the failing run and the event that set the due date
    assert "ci_failing_near_due" not in rules_for(result, items["gh:o/r#3"].id)


async def test_overdue_upstream_and_blocked_upstream(fresh_tenant: SeededTenant) -> None:
    await build(
        fresh_tenant,
        [
            task("late", -2),
            task("needs-late", 5, deps=["late"]),
            task("stuck", 9, status="blocked"),
            task("needs-stuck", 12, deps=["stuck"]),
            task("fine", 9),
            task("needs-fine", 12, deps=["fine"]),
        ],
    )
    result = await run(fresh_tenant)
    items = await by_key(fresh_tenant)
    late = rules_for(result, items["needs-late"].id)["overdue_upstream"]
    assert (
        late.severity == "high"
        and late.details["upstream"] == "late"
        and late.details["upstream_overdue_days"] == 2.0
    )
    assert rules_for(result, items["needs-stuck"].id)["overdue_upstream"].severity == "medium"
    assert rules_for(result, items["needs-fine"].id) == {}


async def test_explicit_blocker_window(fresh_tenant: SeededTenant) -> None:
    await build(
        fresh_tenant,
        [
            task("a", 5),
            task("b", 5),
            ev(
                "blocker.mentioned",
                "a",
                NOW - timedelta(days=1),
                {"text": "Waiting on legal"},
                source="meeting",
            ),
            ev("blocker.mentioned", "b", NOW - timedelta(days=30), {"text": "Old news"}, source="meeting"),
        ],
    )
    result = await run(fresh_tenant)
    items = await by_key(fresh_tenant)
    blocker = rules_for(result, items["a"].id)["explicit_blocker"]
    assert blocker.severity == "high" and blocker.details["latest"] == "Waiting on legal"
    assert "explicit_blocker" not in rules_for(result, items["b"].id)


async def test_owner_overloaded(fresh_tenant: SeededTenant) -> None:
    await build(
        fresh_tenant,
        [task(f"busy{i}", 1 + i, owner="busy") for i in range(4)]
        + [task(f"calm{i}", 1 + i, owner="calm") for i in range(3)],
    )
    result = await run(fresh_tenant)
    items = await by_key(fresh_tenant)
    assert {s.work_item_id for s in result.signals if s.rule == "owner_overloaded"} == {
        items[f"busy{i}"].id for i in range(4)
    }
    assert all(s.severity == "low" for s in result.signals if s.rule == "owner_overloaded")


async def test_propagation_chain_absorbs_slack(fresh_tenant: SeededTenant) -> None:
    # a is 3 days late. b's latest start is 2 days after a's due date, so b slips 1 day; c has 5 days of
    # slack.
    await build(
        fresh_tenant, [task("a", -3), task("b", 1, est=2, deps=["a"]), task("c", 7, est=1, deps=["b"])]
    )
    async with tenant_tx(fresh_tenant.id) as s:
        slips = await propagate(s, NOW)
    items = await by_key(fresh_tenant)
    assert slips[items["a"].id].days == pytest.approx(3)
    assert slips[items["b"].id].days == pytest.approx(1)
    assert items["c"].id not in slips
    assert slips[items["a"].id].downstream_impact == 1 and slips[items["b"].id].roots == [items["a"].id]


async def test_propagation_diamond_keeps_worst_path(fresh_tenant: SeededTenant) -> None:
    #        a  (due -4, 4 days late)
    #       / \
    #      b   c    b: due 0, est 2 -> latest start -2 -> slack 2 -> slips 2
    #       \ /      c: due 0, est 1 -> latest start -1 -> slack 3 -> slips 1
    #        d      d: due 2, est 2 -> latest start 0 -> slack 0 after b or c -> via b 2, via c 1 -> keeps 2
    await build(
        fresh_tenant,
        [
            task("a", -4),
            task("b", 0, est=2, deps=["a"]),
            task("c", 0, est=1, deps=["a"]),
            task("d", 2, est=2, deps=["b", "c"]),
        ],
    )
    async with tenant_tx(fresh_tenant.id) as s:
        slips = await propagate(s, NOW)
    items = await by_key(fresh_tenant)
    assert slips[items["a"].id].days == pytest.approx(4)
    assert slips[items["b"].id].days == pytest.approx(2)
    assert slips[items["c"].id].days == pytest.approx(1)
    assert slips[items["d"].id].days == pytest.approx(2)
    assert slips[items["d"].id].roots == [items["a"].id]
    assert slips[items["a"].id].downstream_impact == 3  # b, c and d; d reached twice but counted once


async def test_propagation_ignores_done_items_and_cycles(fresh_tenant: SeededTenant) -> None:
    # x is 5 days late; y (latest start -1, slack 4) slips 1; z depends on y and y on z: the walk must stop.
    await build(
        fresh_tenant,
        [
            task("x", -5),
            task("y", 0, est=1, deps=["x", "z"]),
            task("z", 1, est=1, deps=["y"]),
            task("done", 1, status="done", deps=["x"]),
        ],
    )
    async with tenant_tx(fresh_tenant.id) as s:
        slips = await propagate(s, NOW)
    items = await by_key(fresh_tenant)
    assert items["done"].id not in slips
    assert slips[items["y"].id].days == pytest.approx(1)
    assert slips[items["z"].id].days == pytest.approx(1) and slips[items["z"].id].roots == [items["x"].id]


def test_scoring_formula() -> None:
    assert scoring.combine([]) == 0 and scoring.combine([60]) == 60
    assert scoring.combine([60, 35]) == 74 and scoring.combine([100, 50]) == 100
    assert [scoring.slip_points(d) for d in (0, 0.2, 1.0, 1.5, 9)] == [0, 45, 45, 55, 85]
    assert [scoring.level(s) for s in (0, 29, 30, 59, 60)] == ["ok", "ok", "watch", "watch", "at_risk"]
    assert scoring.health(["ok", "watch"]) == "Watch" and scoring.health([]) == "On track"
    assert scoring.health(["watch", "at_risk"]) == "At risk"


async def test_engine_is_idempotent_and_resolves_cleared_signals(fresh_tenant: SeededTenant) -> None:
    await build(fresh_tenant, [task("late", -1), task("next", 2, deps=["late"])])
    first = await evaluate_tenant(fresh_tenant.id, NOW)
    second = await evaluate_tenant(fresh_tenant.id, NOW)
    assert first.scores == second.scores
    async with tenant_tx(fresh_tenant.id) as s:
        assert await s.scalar(text("SELECT count(*) FROM signals")) == 1
    # The upstream item finishes: the signal resolves, the score drops, nothing is duplicated.
    await build(
        fresh_tenant, [ev("issue.closed", "late", NOW - timedelta(hours=1), {"fields": {"status": "done"}})]
    )
    await evaluate_tenant(fresh_tenant.id, NOW)
    items = await by_key(fresh_tenant)
    async with tenant_tx(fresh_tenant.id) as s:
        open_signals = await s.scalar(text("SELECT count(*) FROM signals WHERE resolved_at IS NULL"))
        resolved = await s.scalar(text("SELECT count(*) FROM signals WHERE resolved_at IS NOT NULL"))
    assert (open_signals, resolved) == (0, 1)
    assert items["next"].risk_score == 0 and items["next"].risk_level == "ok"


async def test_replaying_week2_flips_health_to_at_risk(fresh_tenant: SeededTenant) -> None:
    await set_policy(fresh_tenant.id, clock="event")
    await ReplayConnector("tiffin_box", "week1").replay(fresh_tenant.id)
    await project_tenant(fresh_tenant.id)
    week1 = await evaluate_tenant(fresh_tenant.id)
    async with tenant_tx(fresh_tenant.id) as s:
        assert (await health_summary(s))["health"] == "On track"
    await ReplayConnector("tiffin_box", "week2").replay(fresh_tenant.id)
    await project_tenant(fresh_tenant.id)
    week2 = await evaluate_tenant(fresh_tenant.id)
    async with tenant_tx(fresh_tenant.id) as s:
        summary = await health_summary(s)
        checkout = (
            await s.execute(
                text("SELECT risk_level FROM work_items WHERE key = 'gh:tiffin-box/tiffin-box-app#12'")
            )
        ).scalar_one()
    assert week2.as_of > week1.as_of
    assert summary["health"] == "At risk" and summary["counts"]["at_risk"] >= 1 and checkout == "at_risk"
    assert {s.rule for s in week2.signals} >= {"ci_failing_near_due", "explicit_blocker"}


async def test_event_clock_ignores_meeting_dates(fresh_tenant: SeededTenant) -> None:
    """A meeting typed in as "today" must not age a replayed scenario: the clock follows work events."""
    await set_policy(fresh_tenant.id, clock="event")
    work_at = datetime(2026, 3, 6, 10, 15, tzinfo=UTC)
    async with tenant_tx(fresh_tenant.id) as s:
        await append_events(s, fresh_tenant.id, [ev("pr.opened", "gh:x#1", work_at, {}, source="github")])
        await append_events(
            s,
            fresh_tenant.id,
            [ev("meeting.transcribed", "meeting:m1", work_at + timedelta(days=240), {}, "meeting")],
        )
        assert await tenant_now(s) == work_at
