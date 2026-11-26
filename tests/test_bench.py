import argparse
from pathlib import Path
from typing import Any

import pytest

from agents.plan_schema import Plan, PlanResult, RawStats
from agents.plan_validators import locate
from evaluation import run
from evaluation.plan_suite import Case, aggregate, check_gold, load_cases, match_items, score_case


def test_every_gold_file_is_consistent_with_its_brief() -> None:
    cases = load_cases()
    assert len(cases) >= 3
    for case in cases:
        assert check_gold(case) == [], case.name


def plan_from_gold(
    case_name: str, *, drop_deps: bool = False, question: bool = True
) -> tuple[PlanResult, Case]:
    case = next(c for c in load_cases() if c.name == case_name)
    ids = {g.title: f"i{n}" for n, g in enumerate(case.gold.items)}
    deps: dict[str, list[str]] = {ids[a]: [] for a in ids}
    if not drop_deps:
        for a, b in case.gold.dependencies:
            deps[ids[a]].append(ids[b])
    span = {"start": 0, "end": 5}
    assert case.gold.contradiction is not None
    q_spans = [locate(q, case.brief) for q in case.gold.contradiction.quotes]
    plan = Plan.model_validate(
        {
            "items": [
                {
                    "local_id": ids[g.title],
                    "title": g.title,
                    "lane": g.lane,
                    "owner_role": "x",
                    "estimate_days": 1,
                    "depends_on": deps[ids[g.title]],
                    "source_span": span,
                }
                for g in case.gold.items
            ],
            "open_questions": [{"text": "Which?", "source_spans": [s.model_dump() for s in q_spans if s]}]
            if question
            else [],
        }
    )
    return PlanResult(
        plan=plan,
        dropped=[],
        repaired=False,
        raw=RawStats(items=10, uncited_items=1, had_cycle=True),
        agent_run_ids=[],
    ), case


def test_perfect_plan_scores_one() -> None:
    result, case = plan_from_gold("clinic_reminders")
    row = score_case(result, case)
    assert (row["item_precision"], row["item_recall"], row["dep_precision"], row["dep_recall"]) == (
        1,
        1,
        1,
        1,
    )
    assert row["contradiction_caught"] is True


def test_missing_dependencies_and_question_are_penalised() -> None:
    result, case = plan_from_gold("podcast_season", drop_deps=True, question=False)
    row = score_case(result, case)
    assert row["dep_recall"] == 0 and row["dep_precision"] is None and row["contradiction_caught"] is False
    summary = aggregate([row, score_case(*plan_from_gold("clinic_reminders"))])
    assert summary["dep_recall"] == 0.5 and summary["contradiction_caught_rate"] == 0.5
    assert summary["cycle_rate"] == 1.0 and summary["uncited_item_rate"] == pytest.approx(0.1)


def test_matching_requires_same_lane_and_similar_title() -> None:
    result, case = plan_from_gold("tiffin_box")
    wrong_lane = result.plan.model_copy(deep=True)
    for item in wrong_lane.items:
        item.lane = "Product" if item.lane != "Product" else "Marketing"
        item.title = item.title.upper()
    assert match_items(wrong_lane, case.gold) == {}


def test_markdown_table_comes_from_report() -> None:
    summary = {m: 0.5 for m in run.METRICS}
    report = {
        "suite": "plan",
        "started_at": "t",
        "configs": {"a": {"cases": [{}], "summary": summary, "errors": 0}},
    }
    table = run.to_markdown(report)
    assert "| dep_recall | 0.50 |" in table and "| errors | 0 |" in table


async def test_bench_without_key_exits_2_before_any_call(database: None) -> None:
    assert await run._main(argparse.Namespace(suite="plan", configs=None)) == 2


def test_risk_labels_match_their_timelines() -> None:
    from evaluation import risk_suite

    cases = risk_suite.load_cases()
    assert len(cases) >= 3
    for case in cases:
        assert risk_suite.check_case(case) == [], case.name


def test_risk_scoring_math() -> None:
    from datetime import UTC, datetime

    from evaluation import risk_suite

    case = risk_suite.Case(
        "x",
        risk_suite.RiskCase.model_validate(
            {
                "scenario": "s",
                "timelines": ["t"],
                "days": 3,
                "labels": [
                    {"item": "a", "day": 1, "causes": ["ci.completed"]},
                    {"item": "b", "day": 0, "causes": ["x"]},
                ],
            }
        ),
    )
    day = datetime(2026, 1, 1, tzinfo=UTC)
    item = {
        "risk_level": "at_risk",
        "due_at": datetime(2026, 1, 4, tzinfo=UTC),
        "signals": [{"evidence_event_ids": [5]}],
    }
    states = [
        risk_suite.DayState(0, day, {"a": {**item, "risk_level": "ok"}}),
        risk_suite.DayState(2, datetime(2026, 1, 3, tzinfo=UTC), {"a": item, "c": item}),
    ]
    row = risk_suite.score_case(case, states, {5: "ci.completed"})
    assert (row["precision"], row["recall"]) == (0.5, 0.5)
    assert (row["lead_time_days"], row["detection_delay_days"], row["evidence_cites_cause"]) == (
        1.0,
        1.0,
        1.0,
    )


async def test_safety_suite_passes_and_can_fail(database: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from evaluation import safety_suite
    from workers.execute import Outcome

    results = await safety_suite.run_all()
    assert [r.name for r in results if r.passed is False] == []
    assert sum(1 for r in results if r.passed) >= 11

    async def reckless(
        tenant_id: object, proposal_id: object
    ) -> Outcome:  # an executor that ignores approval
        return Outcome(proposal_id, "executed")  # type: ignore[arg-type]

    world = await safety_suite.build_world()
    monkeypatch.setattr(safety_suite, "execute", reckless)
    with pytest.raises(AssertionError, match="unapproved move_due ran"):
        await safety_suite.case_unapproved(world, "move_due", {"new_due_date": "2026-03-20"})


def test_meeting_scoring_math() -> None:
    from evaluation.insight_suites import score_meeting

    gold = [
        {"text": "Get sandbox keys from finance", "owner": "Arjun", "due_date": "2026-03-13"},
        {"text": "Write the FAQ page", "owner": "Neha", "due_date": "2026-03-12"},
    ]
    predicted: list[dict[str, Any]] = [
        {"text": "Get the sandbox keys from finance", "owner": "arjun", "due_date": "2026-03-13"},
        {"text": "Order lunch", "owner": None, "due_date": None},
    ]
    row = score_meeting(predicted, gold)
    assert (row["tp"], row["precision"], row["recall"]) == (1, 0.5, 0.5)
    assert row["f1"] == 0.5 and row["owner_accuracy"] == 1.0 and row["due_accuracy"] == 1.0


def test_readme_bench_section_is_generated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from evaluation import run

    results, readme = tmp_path / "results", tmp_path / "README.md"
    results.mkdir()
    (results / "latest-risk.md").write_text("# DaelysBench: risk suite\n\n| a | b |\n")
    readme.write_text("intro\n<!-- bench:start -->\nold numbers\n<!-- bench:end -->\nfooter\n")
    monkeypatch.setattr(run, "RESULTS", results)
    monkeypatch.setattr(run, "README", readme)
    run._write_latest()
    text_ = readme.read_text()
    assert "old numbers" not in text_ and "#### risk suite" in text_ and text_.endswith("footer\n")
