"""Sprint 5 suites.

meetings: action-item F1, owner and due-date accuracy, per language. duplicates: precision at the
threshold. faithfulness: share of cited summary sentences + a sample for manual review. ops:
latency, tokens and cost per summary. Anything that needs a model returns None when none is
configured: never a made-up number.
"""

import asyncio
import json
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from statistics import mean, quantiles
from typing import Any
from uuid import UUID

from sqlalchemy import text

from agents.meeting_extractor import extract
from connectors.notiv import parse_transcript
from core.db import global_tx, tenant_tx
from core.insights import DUPLICATE_MIN
from core.llm import LLM
from core.seed import ensure_tenant

ROOT = Path(__file__).resolve().parent / "datasets"
TEXT_MATCH = 0.5


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def score_meeting(predicted: list[dict[str, Any]], gold: list[dict[str, Any]]) -> dict[str, Any]:
    """Greedy one-to-one text match; then owner and due-date accuracy over the matched pairs."""
    pairs = sorted(
        ((_sim(p["text"], g["text"]), i, j) for i, p in enumerate(predicted) for j, g in enumerate(gold)),
        reverse=True,
    )
    used_p: set[int] = set()
    used_g: set[int] = set()
    matched: list[tuple[int, int]] = []
    for score, i, j in pairs:
        if score < TEXT_MATCH:
            break
        if i not in used_p and j not in used_g:
            used_p.add(i)
            used_g.add(j)
            matched.append((i, j))
    tp = len(matched)
    precision = tp / len(predicted) if predicted else None
    recall = tp / len(gold) if gold else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else 0.0
    owners = [
        (predicted[i].get("owner") or "").lower() == (gold[j].get("owner") or "").lower() for i, j in matched
    ]
    dues = [
        predicted[i].get("due_date") == gold[j]["due_date"] for i, j in matched if gold[j].get("due_date")
    ]
    return {
        "tp": tp,
        "predicted": len(predicted),
        "gold": len(gold),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "owner_accuracy": mean(owners) if owners else None,
        "due_accuracy": mean(dues) if dues else None,
    }


async def run_meetings(llm: LLM | None) -> dict[str, Any]:
    tenant_id = await ensure_tenant("bench-meetings", "DaelysBench meetings")
    rows = []
    for case in sorted(p for p in (ROOT / "meetings").iterdir() if p.is_dir()):
        gold = json.loads((case / "gold.json").read_text())
        if llm is None:
            rows.append(
                {"case": case.name, "language": gold["language"], "not_run": "needs ANTHROPIC_API_KEY"}
            )
            continue
        lines = parse_transcript((case / "transcript.txt").read_text())
        out = await extract(llm, tenant_id, lines, date.fromisoformat(gold["meeting_date"]), case.name)
        predicted = [
            {"text": a.text, "owner": a.owner_speaker, "due_date": a.due_date} for a in out.action_items
        ]
        rows.append(
            {
                "case": case.name,
                "language": gold["language"],
                **score_meeting(predicted, gold["action_items"]),
            }
        )
    by_language: dict[str, dict[str, float | None]] = {}
    for language in sorted({r["language"] for r in rows}):
        scored = [r for r in rows if r["language"] == language and "not_run" not in r]
        by_language[language] = (
            {
                k: (
                    mean(v for r in scored if (v := r[k]) is not None)
                    if any(r[k] is not None for r in scored)
                    else None
                )
                for k in ("f1", "owner_accuracy", "due_accuracy")
            }
            if scored
            else {"f1": None, "owner_accuracy": None, "due_accuracy": None}
        )
    return {"cases": rows, "by_language": by_language}


async def run_duplicates(llm: LLM | None) -> dict[str, Any]:
    from agents.writers import judge_duplicate

    pairs = json.loads((ROOT / "duplicates" / "pairs.json").read_text())["pairs"]
    threshold = DUPLICATE_MIN["lexical"]
    tenant_id = await ensure_tenant("bench-duplicates", "DaelysBench duplicates")
    rows = []
    for p in pairs:
        async with global_tx() as s:
            sim = float(await s.scalar(text("SELECT similarity(:a, :b)"), {"a": p["a"], "b": p["b"]}) or 0)
        candidate = sim >= threshold
        judged = None
        if llm is not None and candidate:
            verdict, _ = await judge_duplicate(llm, tenant_id, {"title": p["a"]}, {"title": p["b"]})
            judged = verdict.duplicate
        rows.append({**p, "similarity": round(sim, 3), "candidate": candidate, "suggested": judged})

    def precision(flag: str) -> float | None:
        flagged = [r for r in rows if r[flag]]
        return sum(r["duplicate"] for r in flagged) / len(flagged) if flagged else None

    positives = sum(r["duplicate"] for r in rows)
    return {
        "threshold": threshold,
        "candidate_precision": precision("candidate"),
        "candidate_recall": sum(r["candidate"] and r["duplicate"] for r in rows) / positives
        if positives
        else None,
        "suggestion_precision": precision("suggested") if llm is not None else None,
        "pairs": rows,
    }


async def run_faithfulness(llm: LLM | None, sample_path: Path) -> dict[str, Any]:
    """Weekly summaries for the risk-bench Tiffin tenant, week 2, every lane."""
    from core.insights import generate_summary

    if llm is None:
        return {"cited_share": None, "sentences": 0, "not_run": "needs ANTHROPIC_API_KEY"}
    tenant_id = await ensure_tenant("bench-risk-tiffin-box", "DaelysBench risk tiffin_box")
    async with tenant_tx(tenant_id) as s:
        project = await s.scalar(text("SELECT id FROM projects ORDER BY created_at LIMIT 1"))
        if project is None:
            project = await s.scalar(
                text(
                    "INSERT INTO projects (tenant_id, name) VALUES (:t, 'Subscriptions launch') RETURNING id"
                ),
                {"t": tenant_id},
            )
            await s.execute(text("UPDATE work_items SET project_id = :p"), {"p": project})
    kept = dropped = 0
    sample = ["# Faithfulness sample (check each sentence against its cited events)", ""]
    for lane in ("Product", "Engineering", "Marketing"):
        result = await generate_summary(llm, tenant_id, UUID(str(project)), lane, date(2026, 3, 9), None)
        kept += len(result["sentences"])
        dropped += result["dropped"]
        sample.append(f"## {lane}")
        sample += [f"- {s['text']}" for s in result["sentences"]] + [""]
    await asyncio.to_thread(sample_path.write_text, "\n".join(sample))
    total = kept + dropped
    return {"cited_share": kept / total if total else None, "sentences": total, "sample": sample_path.name}


async def run_ops() -> dict[str, Any]:
    """From agent_runs across bench tenants: latency percentiles, tokens and cost per summary."""
    out: dict[str, Any] = {}
    for slug in ("bench-risk-tiffin-box",):
        async with global_tx() as s:
            tenant = await s.scalar(text("SELECT daelys_resolve_tenant(:s)"), {"s": slug})
        if tenant is None:
            continue
        async with tenant_tx(UUID(str(tenant))) as s:
            runs = (
                await s.execute(
                    text("""
                SELECT latency_ms, input_tokens, output_tokens, cost_usd FROM agent_runs
                WHERE agent = 'summarizer' AND status <> 'error'
            """)
                )
            ).all()
        if not runs:
            continue
        latencies = sorted(r.latency_ms for r in runs)
        out = {
            "summaries": len(runs),
            "p50_latency_ms": quantiles(latencies, n=100)[49] if len(latencies) > 1 else latencies[0],
            "p95_latency_ms": quantiles(latencies, n=100)[94] if len(latencies) > 1 else latencies[0],
            "tokens_per_summary": mean((r.input_tokens or 0) + (r.output_tokens or 0) for r in runs),
            "cost_usd_per_summary": float(mean(float(r.cost_usd or 0) for r in runs)),
        }
    return out or {"not_run": "no summaries yet (needs ANTHROPIC_API_KEY)"}
