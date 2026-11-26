"""Plan suite: dataset loading and metric functions. Pure functions, no LLM, no DB."""

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from statistics import mean

from pydantic import BaseModel

from agents.plan_schema import Lane, Plan, PlanResult, SourceSpan
from agents.plan_validators import locate

DATASET_DIR = Path(__file__).resolve().parent / "datasets" / "plan"
TITLE_MATCH_THRESHOLD = 0.5


class GoldItem(BaseModel):
    title: str
    lane: Lane


class GoldContradiction(BaseModel):
    quotes: list[str]


class Gold(BaseModel):
    draft: bool = False
    items: list[GoldItem]
    dependencies: list[tuple[str, str]]  # (item, prerequisite) by gold title
    contradiction: GoldContradiction | None = None


@dataclass(frozen=True)
class Case:
    name: str
    brief: str
    gold: Gold


def load_cases(directory: Path = DATASET_DIR) -> list[Case]:
    cases = []
    for case_dir in sorted(p for p in directory.iterdir() if p.is_dir()):
        gold = Gold.model_validate(json.loads((case_dir / "gold.json").read_text()))
        cases.append(Case(case_dir.name, (case_dir / "brief.md").read_text(), gold))
    return cases


def check_gold(case: Case) -> list[str]:
    """Problems that make a gold file unusable: unknown titles in dependencies, quotes not in the brief."""
    titles = {i.title for i in case.gold.items}
    problems = [
        f"dependency names unknown item {t!r}"
        for dep in case.gold.dependencies
        for t in dep
        if t not in titles
    ]
    if case.gold.contradiction:
        problems += [
            f"contradiction quote not in brief: {q!r}"
            for q in case.gold.contradiction.quotes
            if locate(q, case.brief) is None
        ]
    return problems


def _norm(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", title.lower()).strip()


def title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def match_items(plan: Plan, gold: Gold, threshold: float = TITLE_MATCH_THRESHOLD) -> dict[str, int]:
    """Greedy one-to-one match of predicted local_id -> gold item index: same lane, best title similarity."""
    candidates = sorted(
        (
            (title_similarity(p.title, g.title), p.local_id, gi)
            for p in plan.items
            for gi, g in enumerate(gold.items)
            if p.lane == g.lane
        ),
        reverse=True,
    )
    matched: dict[str, int] = {}
    used: set[int] = set()
    for score, local_id, gi in candidates:
        if score < threshold:
            break
        if local_id not in matched and gi not in used:
            matched[local_id] = gi
            used.add(gi)
    return matched


def _ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def _overlaps(a: SourceSpan, b: SourceSpan) -> bool:
    return a.start < b.end and b.start < a.end


def score_case(result: PlanResult, case: Case) -> dict[str, float | int | bool | None]:
    plan, gold = result.plan, case.gold
    matched = match_items(plan, gold)
    title_index = {g.title: i for i, g in enumerate(gold.items)}
    gold_deps = {(title_index[a], title_index[b]) for a, b in gold.dependencies}
    pred_deps = [(i.local_id, d) for i in plan.items for d in i.depends_on]
    correct = sum(
        1 for a, b in pred_deps if a in matched and b in matched and (matched[a], matched[b]) in gold_deps
    )
    caught: bool | None = None
    if gold.contradiction:
        gold_spans = [s for s in (locate(q, case.brief) for q in gold.contradiction.quotes) if s is not None]
        caught = any(
            _overlaps(qs, gs) for q in plan.open_questions for qs in q.source_spans for gs in gold_spans
        )
    return {
        "items": len(plan.items),
        "item_precision": _ratio(len(matched), len(plan.items)),
        "item_recall": _ratio(len(matched), len(gold.items)),
        "dep_precision": _ratio(correct, len(pred_deps)),
        "dep_recall": _ratio(correct, len(gold_deps)),
        "raw_items": result.raw.items,
        "raw_uncited": result.raw.uncited_items,
        "raw_cycle": result.raw.had_cycle,
        "contradiction_caught": caught,
        "repaired": result.repaired,
        "dropped": len(result.dropped),
    }


def aggregate(rows: list[dict[str, float | int | bool | None]]) -> dict[str, float | None]:
    def avg(key: str) -> float | None:
        values = [float(v) for r in rows if (v := r.get(key)) is not None]
        return mean(values) if values else None

    raw_items = sum(int(r["raw_items"] or 0) for r in rows)
    return {
        "dep_precision": avg("dep_precision"),
        "dep_recall": avg("dep_recall"),
        "item_precision": avg("item_precision"),
        "item_recall": avg("item_recall"),
        "cycle_rate": avg("raw_cycle"),
        "uncited_item_rate": (sum(int(r["raw_uncited"] or 0) for r in rows) / raw_items)
        if raw_items
        else None,
        "contradiction_caught_rate": avg("contradiction_caught"),
        "repair_rate": avg("repaired"),
    }
