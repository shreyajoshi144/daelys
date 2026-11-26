"""Weekly lane summaries, duplicate suggestions and Ask Daelys.

Every LLM sentence passes the citation checker."""

import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text

from agents.writers import NO_EVIDENCE, answer, judge_duplicate, weekly_summary
from core.db import tenant_tx
from core.llm import LLM
from core.retrieval import search_documents, search_events, similar_items
from core.security import Role

SUMMARY_EVENT_LIMIT = 60
DUPLICATE_MIN = {"lexical": 0.45, "vector": 0.8}
DUPLICATE_BATCH = 20


async def generate_summary(
    llm: LLM, tenant_id: UUID, project_id: UUID, lane: str, week_start: date, user_id: UUID | None
) -> dict[str, Any]:
    week_start -= timedelta(days=week_start.weekday())  # weeks are Monday-to-Monday, in UTC
    start = datetime.combine(week_start, time.min, tzinfo=UTC)
    end = start + timedelta(days=7)
    async with tenant_tx(tenant_id) as s:
        name = await s.scalar(text("SELECT name FROM projects WHERE id = :p"), {"p": project_id})
        if name is None:
            raise LookupError("project not found")
        events = [
            dict(r)
            for r in (
                await s.execute(
                    text("""
            WITH lane_keys AS (
                SELECT w.key FROM work_items w WHERE w.project_id = :p AND w.lane = :lane
                UNION
                SELECT pr.key FROM work_items w JOIN edges e ON e.dst_id = w.id AND e.kind = 'closes'
                JOIN work_items pr ON pr.id = e.src_id WHERE w.project_id = :p AND w.lane = :lane
            )
            SELECT id, type, occurred_at, subject_key, payload FROM events
            WHERE subject_key IN (SELECT key FROM lane_keys) AND occurred_at >= :start AND occurred_at < :end
            ORDER BY occurred_at DESC LIMIT :n
        """),
                    {"p": project_id, "lane": lane, "start": start, "end": end, "n": SUMMARY_EVENT_LIMIT},
                )
            ).mappings()
        ]
        flags = [
            dict(r)
            for r in (
                await s.execute(
                    text("""
            SELECT w.title, w.risk_score, w.risk_level, s.rule, s.severity FROM work_items w
            JOIN signals s ON s.work_item_id = w.id AND s.resolved_at IS NULL
            WHERE w.project_id = :p AND w.lane = :lane ORDER BY w.risk_score DESC LIMIT 20
        """),
                    {"p": project_id, "lane": lane},
                )
            ).mappings()
        ]
    scope = f"project: {name}; lane: {lane}; week starting {week_start.isoformat()}"
    written = await weekly_summary(llm, tenant_id, scope, flags, events)
    sentences = [{"text": c.text, "event_ids": c.event_ids} for c in written.checked.sentences]
    async with tenant_tx(tenant_id) as s:
        version = await s.scalar(
            text("""
            SELECT coalesce(max(version), 0) + 1 FROM summaries
            WHERE project_id = :p AND lane = :lane AND week_start = :w
        """),
            {"p": project_id, "lane": lane, "w": week_start},
        )
        row = (
            await s.execute(
                text("""
            INSERT INTO summaries (tenant_id, project_id, lane, week_start, version, sentences, dropped,
                agent_run_id, created_by)
            VALUES (:t, :p, :lane, :w, :v, CAST(:s AS jsonb), :d, :run, :u)
            RETURNING id, version, created_at
        """),
                {
                    "t": tenant_id,
                    "p": project_id,
                    "lane": lane,
                    "w": week_start,
                    "v": version,
                    "s": json.dumps(sentences),
                    "d": len(written.checked.dropped),
                    "run": written.agent_run_id,
                    "u": user_id,
                },
            )
        ).one()
    return {
        "id": row.id,
        "version": row.version,
        "created_at": row.created_at,
        "lane": lane,
        "week_start": week_start,
        "sentences": sentences,
        "dropped": len(written.checked.dropped),
    }


async def detect_duplicates(llm: LLM, tenant_id: UUID) -> int:
    """Suggest duplicates for newly created items. Suggestions only: nothing is merged or changed."""
    async with tenant_tx(tenant_id) as s:
        fresh = [
            dict(r)
            for r in (
                await s.execute(
                    text("""
            SELECT id, key, title, kind, lane, status FROM work_items
            WHERE duplicates_checked_at IS NULL AND title IS NOT NULL AND status NOT IN ('done', 'cancelled')
            ORDER BY created_at LIMIT :n
        """),
                    {"n": DUPLICATE_BATCH},
                )
            ).mappings()
        ]
    suggested = 0
    for item in fresh:
        async with tenant_tx(tenant_id) as s:
            hits = await similar_items(s, item["title"], k=3, exclude=item["id"])
        for cand in (h for h in hits.rows if h["score"] >= DUPLICATE_MIN[hits.mode]):
            verdict, run_id = await judge_duplicate(llm, tenant_id, item, cand)
            if verdict.duplicate:
                async with tenant_tx(tenant_id) as s:
                    await s.execute(
                        text("""
                        INSERT INTO duplicate_suggestions
                            (tenant_id, item_id, duplicate_of, similarity, reason, agent_run_id)
                        VALUES (:t, :a, :b, :sim, :r, :run) ON CONFLICT DO NOTHING
                    """),
                        {
                            "t": tenant_id,
                            "a": item["id"],
                            "b": cand["id"],
                            "sim": float(cand["score"]),
                            "r": verdict.reason[:500],
                            "run": run_id,
                        },
                    )
                suggested += 1
        async with tenant_tx(tenant_id) as s:
            await s.execute(
                text("UPDATE work_items SET duplicates_checked_at = now() WHERE id = :id"), {"id": item["id"]}
            )
    return suggested


async def ask(llm: LLM, tenant_id: UUID, role: Role, question: str) -> dict[str, Any]:
    async with tenant_tx(tenant_id) as s:
        docs = await search_documents(s, question, role)
        events = await search_events(s, question)
    if not docs.rows and not events.rows:
        return {"answer": [], "text": NO_EVIDENCE, "sources": [], "mode": docs.mode, "enough_evidence": False}
    written = await answer(llm, tenant_id, question, docs.rows, events.rows)
    sentences = written.checked.sentences
    if not sentences or any(NO_EVIDENCE.rstrip(".") in c.text for c in sentences):
        return {"answer": [], "text": NO_EVIDENCE, "sources": [], "mode": docs.mode, "enough_evidence": False}
    doc_by_id = {str(d["id"]): d for d in docs.rows}
    ev_by_id = {e["id"]: e for e in events.rows}
    cited_docs = {d for c in sentences for d in c.doc_ids}
    cited_events = {e for c in sentences for e in c.event_ids}
    sources = [
        {
            "kind": "doc",
            "id": d,
            "title": doc_by_id[d]["artifact_title"],
            "artifact_id": doc_by_id[d]["artifact_id"],
            "excerpt": doc_by_id[d]["content"][:300],
        }
        for d in cited_docs
    ] + [
        {"kind": "event", "id": e, "type": ev_by_id[e]["type"], "subject_key": ev_by_id[e]["subject_key"]}
        for e in cited_events
    ]
    return {
        "answer": [{"text": c.text, "event_ids": c.event_ids, "doc_ids": c.doc_ids} for c in sentences],
        "text": " ".join(c.text for c in sentences),
        "sources": sources,
        "mode": docs.mode,
        "enough_evidence": True,
        "dropped": len(written.checked.dropped),
    }
