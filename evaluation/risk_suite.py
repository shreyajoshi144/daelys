"""Risk suite: replay labelled timelines, evaluate the rules at the end of every scenario day (time machine,
nothing persisted), and compare flags with labels.

Labels say "item X is at risk from day N because of event types Y"."""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from connectors.replay import ReplayConnector
from core.seed import ensure_tenant, set_policy
from rules.engine import evaluate_as_of
from workers.projector import project_tenant

DATASET_DIR = Path(__file__).resolve().parent / "datasets" / "risk"


class Label(BaseModel):
    item: str
    day: int = Field(ge=0)
    causes: list[str] = Field(min_length=1)


class RiskCase(BaseModel):
    draft: bool = False
    scenario: str
    timelines: list[str]
    days: int = Field(ge=1, le=90)
    labels: list[Label]


@dataclass(frozen=True)
class Case:
    name: str
    spec: RiskCase


def load_cases(directory: Path = DATASET_DIR) -> list[Case]:
    return [
        Case(p.stem, RiskCase.model_validate(json.loads(p.read_text())))
        for p in sorted(directory.glob("*.json"))
    ]


def check_case(case: Case) -> list[str]:
    """Labels must name items and event types that actually occur in the timelines."""
    subjects, types = set(), set()
    for timeline in case.spec.timelines:
        for ev in ReplayConnector(case.spec.scenario, timeline).load():
            subjects.add(ev.subject_key)
            types.add(ev.type)
    problems = [
        f"label item not in timelines: {lb.item}" for lb in case.spec.labels if lb.item not in subjects
    ]
    problems += [
        f"cause type not in timelines: {c}" for lb in case.spec.labels for c in lb.causes if c not in types
    ]
    return problems


def scenario_start(case: Case) -> datetime:
    return ReplayConnector(case.spec.scenario, case.spec.timelines[0]).week_start


@dataclass
class DayState:
    day: int
    as_of: datetime
    items: dict[str, dict[str, Any]]


async def run_case(case: Case) -> tuple[UUID, list[DayState]]:
    tenant_id = await ensure_tenant(
        f"bench-risk-{case.name.replace('_', '-')}", f"DaelysBench risk {case.name}"
    )
    await set_policy(tenant_id, clock="event")
    for timeline in case.spec.timelines:
        await ReplayConnector(case.spec.scenario, timeline).replay(tenant_id)  # idempotent across runs
    await project_tenant(tenant_id)
    start = scenario_start(case)
    states = []
    for day in range(case.spec.days):
        as_of = start + timedelta(days=day + 1) - timedelta(seconds=1)
        state = await evaluate_as_of(tenant_id, as_of)
        states.append(DayState(day, as_of, {i["key"]: i for i in state["items"]}))
    return tenant_id, states


def score_case(case: Case, states: list[DayState], event_types: dict[int, str]) -> dict[str, Any]:
    first_flag: dict[str, DayState] = {}
    for state in states:
        for key, item in state.items.items():
            if item["risk_level"] == "at_risk" and key not in first_flag:
                first_flag[key] = state
    gold = {lb.item: lb for lb in case.spec.labels}
    hits = [k for k in gold if k in first_flag]
    lead_times, delays, cause_hits = [], [], []
    for key in hits:
        state = first_flag[key]
        item = state.items[key]
        if item["due_at"] is not None:
            lead_times.append((item["due_at"] - state.as_of).total_seconds() / 86400)
        delays.append(state.day - gold[key].day)
        cited = {event_types.get(e) for s in item["signals"] for e in s["evidence_event_ids"]}
        cause_hits.append(bool(cited & set(gold[key].causes)))
    return {
        "case": case.name,
        "flagged": sorted(first_flag),
        "first_flag_day": {k: s.day for k, s in first_flag.items()},
        "precision": len(hits) / len(first_flag) if first_flag else None,
        "recall": len(hits) / len(gold) if gold else None,
        "lead_time_days": mean(lead_times) if lead_times else None,
        "detection_delay_days": mean(delays) if delays else None,
        "evidence_cites_cause": mean(cause_hits) if cause_hits else None,
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    def avg(key: str) -> float | None:
        values = [float(r[key]) for r in rows if r.get(key) is not None]
        return mean(values) if values else None

    return {
        k: avg(k)
        for k in ("precision", "recall", "lead_time_days", "detection_delay_days", "evidence_cites_cause")
    }
