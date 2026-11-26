"""Deterministic checks on a model-produced plan. No LLM here.

- every item and question must cite brief text that really exists (quote -> char span), else dropped
- duplicate ids, self-dependencies and bad estimates are dropped
- unknown dependency refs are removed and turned into open questions
- dependency cycles are broken one edge at a time, each removal recorded
"""

import re
from dataclasses import dataclass, field
from graphlib import CycleError, TopologicalSorter

from agents.plan_schema import (
    Dropped,
    LLMPlan,
    OpenQuestion,
    Plan,
    RawStats,
    SourceSpan,
    WorkItemDraft,
)

MIN_QUOTE_CHARS = 3
_WS = re.compile(r"\s+")


@dataclass
class Validation:
    plan: Plan
    dropped: list[Dropped] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    raw: RawStats | None = None


def locate(quote: str, brief: str) -> SourceSpan | None:
    """Char span of `quote` in `brief`, ignoring differences in whitespace only."""
    needle = _WS.sub(" ", quote).strip()
    if len(needle) < MIN_QUOTE_CHARS:
        return None
    exact = brief.find(needle)
    if exact >= 0:
        return SourceSpan(start=exact, end=exact + len(needle))
    # Whitespace-normalised search, mapping positions back to the original text.
    norm_chars: list[str] = []
    origin: list[int] = []
    previous_space = False
    for index, char in enumerate(brief):
        if char.isspace():
            if previous_space:
                continue
            norm_chars.append(" ")
            previous_space = True
        else:
            norm_chars.append(char)
            previous_space = False
        origin.append(index)
    pos = "".join(norm_chars).find(needle)
    if pos < 0:
        return None
    return SourceSpan(start=origin[pos], end=origin[pos + len(needle) - 1] + 1)


def _find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    try:
        TopologicalSorter(graph).prepare()
    except CycleError as exc:
        return list(exc.args[1])
    return None


def validate(raw: LLMPlan, brief: str) -> Validation:
    dropped: list[Dropped] = []
    problems: list[str] = []
    kept: dict[str, WorkItemDraft] = {}
    requested_deps: dict[str, list[str]] = {}  # from the kept item, never from a dropped duplicate
    uncited = 0

    for item in raw.items:
        ref = item.local_id or "?"
        span = locate(item.source_quote, brief)
        reason = None
        if not item.local_id.strip() or not item.title.strip():
            reason = "missing id or title"
        elif item.local_id in kept:
            reason = "duplicate local_id"
        elif span is None:
            uncited += 1
            reason = "source_quote not found in brief"
        elif not 0 < item.estimate_days <= 365:
            reason = f"estimate_days {item.estimate_days} outside (0, 365]"
        if reason or span is None:
            dropped.append(Dropped(kind="item", ref=ref, reason=reason or "invalid"))
            problems.append(f"item {ref} ({item.title!r}): {reason}")
            continue
        requested_deps[item.local_id] = item.depends_on
        kept[item.local_id] = WorkItemDraft(
            local_id=item.local_id,
            title=item.title.strip(),
            lane=item.lane,
            owner_role=item.owner_role.strip() or "unassigned",
            estimate_days=item.estimate_days,
            depends_on=[],
            source_span=span,
        )

    questions: list[OpenQuestion] = []
    for local_id, draft in kept.items():
        for dep in dict.fromkeys(requested_deps[local_id]):
            if dep == local_id:
                dropped.append(Dropped(kind="dependency", ref=f"{local_id}->{dep}", reason="self-dependency"))
                problems.append(f"item {local_id} depends on itself")
            elif dep not in kept:
                dropped.append(Dropped(kind="dependency", ref=f"{local_id}->{dep}", reason="unknown item"))
                problems.append(f"item {local_id} depends on unknown or dropped item {dep!r}")
                questions.append(
                    OpenQuestion(
                        text=f'"{draft.title}" depends on "{dep}", which is not in the plan. '
                        "What does it actually depend on?",
                        source_spans=[draft.source_span],
                    )
                )
            else:
                draft.depends_on.append(dep)

    had_cycle = False
    graph = {i: set(d.depends_on) for i, d in kept.items()}
    while (cycle := _find_cycle(graph)) is not None:
        had_cycle = True
        # graphlib reports [n0, n1, ..., n0] where each node depends on the next one or vice versa.
        a, b = cycle[0], cycle[1]
        src, dst = (a, b) if b in graph[a] else (b, a)
        graph[src].discard(dst)
        kept[src].depends_on.remove(dst)
        dropped.append(Dropped(kind="dependency", ref=f"{src}->{dst}", reason="breaks a dependency cycle"))
        problems.append(f"dependency cycle through {' -> '.join(cycle)}")

    for question in raw.open_questions:
        spans = [s for s in (locate(q, brief) for q in question.source_quotes) if s is not None]
        if not question.text.strip() or not spans:
            dropped.append(Dropped(kind="question", ref=question.text[:80], reason="no quote found in brief"))
            problems.append(f"open question {question.text[:80]!r} cites nothing found in the brief")
            continue
        questions.append(OpenQuestion(text=question.text.strip(), source_spans=spans))

    return Validation(
        plan=Plan(items=list(kept.values()), open_questions=questions),
        dropped=dropped,
        problems=problems,
        raw=RawStats(items=len(raw.items), uncited_items=uncited, had_cycle=had_cycle),
    )
