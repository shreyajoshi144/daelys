"""DaelysBench runner: `python -m evaluation.run --suite plan [--configs opus-5.5,sonnet-5.5]`.

Every number in results/ is computed here from real model output; nothing is typed by hand. Writes
results/latest.md and results/run-<timestamp>.json. Exits 2 if a model provider is not configured.
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text

from agents.explainer import EvidenceEvent, describe_signal, explain
from agents.planner import plan_from_brief
from core.config import get_settings
from core.db import get_engine, tenant_tx
from core.errors import NotConfigured
from core.llm import LLM, LLMUnavailable, ProviderError, get_llm
from core.logging import configure_logging
from core.seed import ensure_tenant
from evaluation import insight_suites, plan_suite, risk_suite, safety_suite

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
CONFIGS = ROOT / "configs.json"
RISK_METRICS = [
    "precision",
    "recall",
    "lead_time_days",
    "detection_delay_days",
    "evidence_cites_cause",
    "explanation_cites_cause",
]
METRICS = [
    "dep_precision",
    "dep_recall",
    "item_precision",
    "item_recall",
    "cycle_rate",
    "uncited_item_rate",
    "contradiction_caught_rate",
    "repair_rate",
]
log = logging.getLogger(__name__)


async def run_plan_suite(config_names: list[str]) -> dict[str, Any]:
    configs: dict[str, str] = json.loads(CONFIGS.read_text())
    unknown = set(config_names) - configs.keys()
    if unknown:
        raise SystemExit(f"unknown configs: {', '.join(sorted(unknown))}")
    cases = plan_suite.load_cases()
    for case in cases:
        if problems := plan_suite.check_gold(case):
            raise SystemExit(f"gold for {case.name} is invalid: {problems}")
    llms = {name: get_llm(configs[name]) for name in config_names}  # NotConfigured before any spend
    tenant_id = await ensure_tenant("daelys-bench", "DaelysBench")

    report: dict[str, Any] = {"suite": "plan", "started_at": datetime.now(UTC).isoformat(), "configs": {}}
    for name, llm in llms.items():
        rows: list[dict[str, Any]] = []
        for case in cases:
            try:
                result = await plan_from_brief(llm, tenant_id, case.brief)
            except (LLMUnavailable, ProviderError) as exc:
                rows.append({"case": case.name, "error": str(exc)})
                continue
            rows.append({"case": case.name, **plan_suite.score_case(result, case)})
        scored = [r for r in rows if "error" not in r]
        report["configs"][name] = {
            "routes": configs[name],
            "cases": rows,
            "summary": plan_suite.aggregate(scored),
            "errors": len(rows) - len(scored),
        }
    return report


async def _explanation_cites_cause(
    llm: LLM, tenant_id: UUID, item: dict[str, Any], causes: list[str], types: dict[int, str]
) -> bool | None:
    signals = sorted(
        item["signals"], key=lambda s: ["low", "medium", "high", "critical"].index(s["severity"])
    )
    if not signals:
        return None
    top = signals[-1]
    async with tenant_tx(tenant_id) as s:
        rows = await s.execute(
            text(
                "SELECT id, type, occurred_at, actor, subject_key, payload FROM events WHERE id = ANY(:ids)"
            ),
            {"ids": top["evidence_event_ids"]},
        )
        events = [
            EvidenceEvent(r.id, r.type, r.occurred_at.isoformat(), r.actor, r.subject_key, r.payload)
            for r in rows
        ]
    checked = await explain(
        llm, tenant_id, describe_signal(top["rule"], top["severity"], top["details"], item["title"]), events
    )
    cited = {types.get(i) for s in checked.sentences for i in s.event_ids}
    return bool(cited & set(causes))


async def run_risk_suite() -> dict[str, Any]:
    cases = risk_suite.load_cases()
    for case in cases:
        if problems := risk_suite.check_case(case):
            raise SystemExit(f"labels for {case.name} are invalid: {problems}")
    try:
        llm: LLM | None = get_llm()
    except NotConfigured:
        llm = None  # detection metrics need no model; only the explanation metric does
    rows = []
    for case in cases:
        tenant_id, states = await risk_suite.run_case(case)
        async with tenant_tx(tenant_id) as s:
            types = {r.id: r.type for r in await s.execute(text("SELECT id, type FROM events"))}
        row = risk_suite.score_case(case, states, types)
        explained: list[bool] = []
        if llm is not None:
            for label in case.spec.labels:
                day = row["first_flag_day"].get(label.item)
                if day is None:
                    continue
                try:
                    hit = await _explanation_cites_cause(
                        llm, tenant_id, states[day].items[label.item], label.causes, types
                    )
                except (LLMUnavailable, ProviderError) as exc:
                    row.setdefault("errors", []).append(str(exc))
                    continue
                if hit is not None:
                    explained.append(hit)
        row["explanation_cites_cause"] = sum(explained) / len(explained) if explained else None
        rows.append(row)
    summary = risk_suite.aggregate(rows)
    summary["explanation_cites_cause"] = (
        risk_suite.aggregate([{"explanation_cites_cause": r["explanation_cites_cause"]} for r in rows]).get(
            "explanation_cites_cause"
        )
        if llm
        else None
    )
    return {
        "suite": "risk",
        "started_at": datetime.now(UTC).isoformat(),
        "model": llm is not None,
        "cases": rows,
        "summary": summary,
    }


def risk_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# DaelysBench: risk suite",
        "",
        f"Run {report['started_at']}. Cases: {len(report['cases'])}. Generated by `make bench SUITE=risk`; "
        "do not edit by hand."
        + ("" if report["model"] else " explanation_cites_cause needs ANTHROPIC_API_KEY."),
        "",
        "| case | " + " | ".join(RISK_METRICS) + " |",
        "| --- |" + " --- |" * len(RISK_METRICS),
    ]
    for row in report["cases"]:
        lines.append(f"| {row['case']} | " + " | ".join(_fmt(row.get(m)) for m in RISK_METRICS) + " |")
    lines.append("| **all** | " + " | ".join(_fmt(report["summary"].get(m)) for m in RISK_METRICS) + " |")
    return "\n".join(lines) + "\n"


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def to_markdown(report: dict[str, Any]) -> str:
    names = list(report["configs"])
    lines = [
        f"# DaelysBench: {report['suite']} suite",
        "",
        f"Run {report['started_at']}. Cases: {len(next(iter(report['configs'].values()))['cases'])}. "
        "Generated by `make bench`; do not edit by hand.",
        "",
        "| metric | " + " | ".join(names) + " |",
        "| --- |" + " --- |" * len(names),
    ]
    for metric in METRICS:
        lines.append(
            f"| {metric} | " + " | ".join(_fmt(report["configs"][n]["summary"][metric]) for n in names) + " |"
        )
    lines.append("| errors | " + " | ".join(str(report["configs"][n]["errors"]) for n in names) + " |")
    return "\n".join(lines) + "\n"


SUITES = ["plan", "risk", "safety", "meetings", "duplicates", "faithfulness", "ops"]


def _optional_llm() -> LLM | None:
    try:
        return get_llm()
    except NotConfigured:
        return None


async def _run_suite(suite: str, configs: list[str]) -> tuple[dict[str, Any], str]:
    now = datetime.now(UTC).isoformat()
    if suite == "risk":
        report = await run_risk_suite()
        return report, risk_markdown(report)
    if suite == "safety":
        results = await safety_suite.run_all()
        report = {"suite": "safety", "started_at": now, "cases": [r.__dict__ for r in results]}
        return report, safety_markdown(report)
    if suite == "meetings":
        report = {"suite": suite, "started_at": now, **await insight_suites.run_meetings(_optional_llm())}
        return report, meetings_markdown(report)
    if suite == "duplicates":
        report = {"suite": suite, "started_at": now, **await insight_suites.run_duplicates(_optional_llm())}
        return report, duplicates_markdown(report)
    if suite == "faithfulness":
        sample = RESULTS / "faithfulness-sample.md"
        report = {
            "suite": suite,
            "started_at": now,
            **await insight_suites.run_faithfulness(_optional_llm(), sample),
        }
        return report, simple_markdown("faithfulness", report, ["cited_share", "sentences"])
    if suite == "ops":
        report = {"suite": suite, "started_at": now, **await insight_suites.run_ops()}
        keys = ["summaries", "p50_latency_ms", "p95_latency_ms", "tokens_per_summary", "cost_usd_per_summary"]
        return report, simple_markdown("ops", report, keys)
    report = await run_plan_suite(
        configs
    )  # NotConfigured propagates: the plan suite has nothing to run offline
    return report, to_markdown(report)


async def _main(args: argparse.Namespace) -> int:
    configs = args.configs.split(",") if args.configs else list(json.loads(CONFIGS.read_text()))
    suites = SUITES if args.suite == "all" else [args.suite]
    RESULTS.mkdir(exist_ok=True)
    failed_safety = False
    try:
        for suite in suites:
            try:
                report, markdown = await _run_suite(suite, configs)
            except NotConfigured as exc:
                log.error("bench.not_configured", extra={"suite": suite, "detail": str(exc)})
                if args.suite != "all":
                    print(f"DaelysBench not run: {exc}", file=sys.stderr)
                    return 2
                report = {"suite": suite, "not_run": str(exc)}
                markdown = f"# DaelysBench: {suite} suite\n\nNot run: {exc}\n"
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            (RESULTS / f"run-{suite}-{stamp}.json").write_text(json.dumps(report, indent=2, default=str))
            (RESULTS / f"latest-{suite}.md").write_text(markdown)
            print(markdown)
            if suite == "safety" and any(c["passed"] is False for c in report["cases"]):
                failed_safety = True
    finally:
        await get_engine().dispose()
    _write_latest()
    return 1 if failed_safety else 0


def _fmt_any(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}" if isinstance(value, float) else str(value)


def simple_markdown(name: str, report: dict[str, Any], keys: list[str]) -> str:
    lines = [
        f"# DaelysBench: {name} suite",
        "",
        f"Run {report['started_at']}. Generated; do not edit by hand.",
        "",
    ]
    if "not_run" in report:
        lines += [f"Not run: {report['not_run']}.", ""]
    lines += ["| metric | value |", "| --- | --- |"] + [f"| {k} | {_fmt_any(report.get(k))} |" for k in keys]
    return "\n".join(lines) + "\n"


def meetings_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# DaelysBench: meetings suite",
        "",
        f"Run {report['started_at']}. English and Hinglish are reported "
        "separately. Generated; do not edit by hand.",
        "",
        "| language | action-item F1 | owner accuracy | due-date accuracy |",
        "| --- | --- | --- | --- |",
    ]
    for language, m in report["by_language"].items():
        lines.append(
            f"| {language} | {_fmt_any(m['f1'])} | {_fmt_any(m['owner_accuracy'])} | "
            f"{_fmt_any(m['due_accuracy'])} |"
        )
    if any("not_run" in c for c in report["cases"]):
        lines += ["", "Not run: needs ANTHROPIC_API_KEY."]
    return "\n".join(lines) + "\n"


def duplicates_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# DaelysBench: duplicates suite",
        "",
        f"Run {report['started_at']}. Threshold: trigram similarity >= "
        f"{report['threshold']}. Generated; do not edit by hand.",
        "",
        "| metric | value |",
        "| --- | --- |",
        f"| candidate precision (lexical stage) | {_fmt_any(report['candidate_precision'])} |",
        f"| candidate recall (lexical stage) | {_fmt_any(report['candidate_recall'])} |",
        f"| suggestion precision (after LLM judge) | {_fmt_any(report['suggestion_precision'])} |",
    ]
    return "\n".join(lines) + "\n"


def safety_markdown(report: dict[str, Any]) -> str:
    cases = report["cases"]
    counted = [c for c in cases if c["passed"] is not None]
    passed = sum(1 for c in counted if c["passed"])
    skipped = len(cases) - len(counted)
    lines = [
        "# DaelysBench: safety suite",
        "",
        f"Run {report['started_at']}. **{passed}/{len(counted)} passed**"
        + (f", {skipped} not run" if skipped else "")
        + ". Generated by `make bench SUITE=safety`; "
        "any failure fails CI.",
        "",
        "| case | result | detail |",
        "| --- | --- | --- |",
    ]
    label = {True: "pass", False: "**FAIL**", None: "not run"}
    lines += [f"| {c['name']} | {label[c['passed']]} | {c['detail']} |" for c in cases]
    return "\n".join(lines) + "\n"


README = ROOT.parent / "README.md"
BENCH_START, BENCH_END = "<!-- bench:start -->", "<!-- bench:end -->"


def _write_latest() -> None:
    """latest.md = the newest table of every suite, in suite order.

    The README's bench section is replaced with it, so no README number is ever typed by hand."""
    parts = [
        (RESULTS / f"latest-{s}.md").read_text() for s in SUITES if (RESULTS / f"latest-{s}.md").exists()
    ]
    latest = "\n".join(parts)
    (RESULTS / "latest.md").write_text(latest)
    if README.exists():
        readme = README.read_text()
        if BENCH_START in readme and BENCH_END in readme:
            head, rest = readme.split(BENCH_START, 1)
            tail = rest.split(BENCH_END, 1)[1]
            body = latest.replace("# DaelysBench: ", "#### ")
            README.write_text(f"{head}{BENCH_START}\n{body}\n{BENCH_END}{tail}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m evaluation.run")
    parser.add_argument("--suite", choices=[*SUITES, "all"], default="all")
    parser.add_argument("--configs", help="comma-separated names from evaluation/configs.json (default: all)")
    configure_logging(get_settings().log_level)
    sys.exit(asyncio.run(_main(parser.parse_args())))


if __name__ == "__main__":
    main()
