"""Meetings: transcript in -> events out.

Events: meeting.transcribed, decision.made, action.proposed, blocker.mentioned.

Matching an action item or blocker to an existing work item = retrieval top-k + an LLM confirmation. A matched
item only gains an event (never a silent change); an unmatched action item becomes a pending `create_item`
proposal through the normal approval flow.
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agents.matcher import confirm
from agents.meeting_extractor import PROMPT, Extracted, extract
from connectors.notiv import Line, render
from core.briefs import chunk_text, store_brief
from core.db import tenant_tx
from core.events import NewEvent, append_events
from core.llm import LLM
from core.proposals import store_create_item
from core.retrieval import similar_items

log = logging.getLogger(__name__)

MATCH_MIN = {"lexical": 0.3, "vector": 0.55}
_RENDERED = re.compile(r"^\[L(?P<n>\d+)\] (?P<speaker>[^:]*): (?P<text>.*)$")


class MeetingNotFound(Exception):
    pass


class AlreadyExtracted(Exception):
    pass


@dataclass(frozen=True)
class ExtractionSummary:
    decisions: int
    action_items: int
    matched: int
    proposals: int
    blockers: int
    dropped: int


def parse_rendered(content: str) -> list[Line]:
    out = []
    for raw in content.splitlines():
        m = _RENDERED.match(raw)
        if m:
            speaker = m.group("speaker")
            out.append(Line(int(m.group("n")), None if speaker == "Unknown" else speaker, m.group("text")))
    return out


async def resolve_speakers(session: AsyncSession, labels: set[str]) -> dict[str, UUID | None]:
    """Explicit per-tenant mapping first; else a unique first-name match against members; else unmapped."""
    mapped = {
        r.label: r.user_id
        for r in await session.execute(
            text("SELECT label, user_id FROM speaker_mappings WHERE label = ANY(:l)"), {"l": list(labels)}
        )
    }
    members = (
        await session.execute(
            text(
                "SELECT u.id, u.display_name FROM users u JOIN memberships m ON m.user_id = u.id "
                "WHERE m.role <> 'agent'"
            )
        )
    ).all()
    out: dict[str, UUID | None] = {}
    for label in labels:
        if label in mapped:
            out[label] = mapped[label]
            continue
        first = label.split()[0].lower()
        hits = [m.id for m in members if m.display_name.split()[0].lower() == first]
        out[label] = hits[0] if len(hits) == 1 else None
    return out


async def create_meeting(
    tenant_id: UUID,
    *,
    title: str,
    occurred_at: datetime,
    language: str,
    source: str,
    lines: list[Line],
    project_id: UUID | None,
    created_by: UUID,
) -> UUID:
    if not lines:
        raise ValueError("transcript has no lines")
    meeting_id = uuid4()
    content = render(lines)
    async with tenant_tx(tenant_id) as s:
        if project_id and not await s.scalar(text("SELECT 1 FROM projects WHERE id = :p"), {"p": project_id}):
            raise MeetingNotFound("project not found")
        stored = await store_brief(
            s,
            tenant_id,
            project_id,
            f"Transcript: {title}",
            content,
            "text/plain",
            created_by,
            None,
            chunk_text(content),
        )
        await s.execute(
            text("UPDATE artifacts SET kind = 'transcript', min_role = 'member' WHERE id = :a"),
            {"a": stored.artifact_id},
        )
        await s.execute(
            text("""
                INSERT INTO meetings (id, tenant_id, project_id, title, occurred_at, language, source,
                    artifact_id, created_by)
                VALUES (:id, :t, :p, :title, :at, :lang, :src, :a, :by)
            """),
            {
                "id": meeting_id,
                "t": tenant_id,
                "p": project_id,
                "title": title,
                "at": occurred_at,
                "lang": language,
                "src": source,
                "a": stored.artifact_id,
                "by": created_by,
            },
        )
        speakers = sorted({line.speaker for line in lines if line.speaker})
        await append_events(
            s,
            tenant_id,
            [
                NewEvent(
                    source="meeting",
                    type="meeting.transcribed",
                    idempotency_key=f"meeting:{meeting_id}:transcribed",
                    occurred_at=occurred_at,
                    subject_key=f"meeting:{meeting_id}",
                    actor=None,
                    payload={
                        "title": title,
                        "lines": len(lines),
                        "speakers": speakers,
                        "language": language,
                        "source": source,
                    },
                )
            ],
        )
    return meeting_id


def _quotes(lines: dict[int, Line], ids: list[int]) -> list[str]:
    return [f"{lines[i].speaker or 'Unknown'}: {lines[i].text}"[:400] for i in ids if i in lines]


async def _match(llm: LLM, tenant_id: UUID, item_text: str) -> tuple[dict[str, Any] | None, str]:
    async with tenant_tx(tenant_id) as s:
        hits = await similar_items(s, item_text, k=5)
    candidates = [h for h in hits.rows if h["score"] >= MATCH_MIN[hits.mode]]
    if not candidates:
        return None, f"no candidate above {MATCH_MIN[hits.mode]} ({hits.mode})"
    verdict = await confirm(llm, tenant_id, item_text, candidates)
    match = next((c for c in candidates if c["key"] == verdict.match_key), None)
    return match, verdict.reason


async def extract_meeting(llm: LLM, tenant_id: UUID, meeting_id: UUID) -> ExtractionSummary:
    async with tenant_tx(tenant_id) as s:
        meeting = (
            await s.execute(
                text("""
                    SELECT m.*, a.content FROM meetings m JOIN artifacts a ON a.id = m.artifact_id
                    WHERE m.id = :id FOR UPDATE OF m
                """),
                {"id": meeting_id},
            )
        ).first()
        if meeting is None:
            raise MeetingNotFound
        if meeting.status == "extracted":
            raise AlreadyExtracted
    lines = parse_rendered(meeting.content)
    by_n = {line.n: line for line in lines}
    extracted: Extracted = await extract(llm, tenant_id, lines, meeting.occurred_at.date(), meeting.title)
    key = f"meeting:{meeting_id}"
    events: list[NewEvent] = []
    matched = proposals = 0

    def event(kind: str, n: int, subject: str, payload: dict[str, Any]) -> NewEvent:
        return NewEvent(
            source="meeting",
            type=kind,
            idempotency_key=f"{key}:{kind}:{n}",
            occurred_at=meeting.occurred_at,
            subject_key=subject,
            actor=None,
            payload={**payload, "meeting_id": str(meeting_id)},
        )

    for n, d in enumerate(extracted.decisions):
        events.append(
            event(
                "decision.made", n, key, {"text": d.text, "lines": d.lines, "quotes": _quotes(by_n, d.lines)}
            )
        )
    for n, b in enumerate(extracted.blockers):
        hit, reason = await _match(llm, tenant_id, b.text)
        events.append(
            event(
                "blocker.mentioned",
                n,
                hit["key"] if hit else key,
                {
                    "text": b.text,
                    "lines": b.lines,
                    "quotes": _quotes(by_n, b.lines),
                    "matched": bool(hit),
                    "match_reason": reason,
                },
            )
        )
    async with tenant_tx(tenant_id) as s:
        owners = await resolve_speakers(
            s, {a.owner_speaker for a in extracted.action_items if a.owner_speaker}
        )
    pending: list[tuple[int, Any, UUID | None]] = []
    for n, a in enumerate(extracted.action_items):
        owner = owners.get(a.owner_speaker) if a.owner_speaker else None
        hit, reason = await _match(llm, tenant_id, a.text)
        payload = {
            "text": a.text,
            "owner_speaker": a.owner_speaker,
            "owner_id": str(owner) if owner else None,
            "due_date": a.due_date,
            "lines": a.lines,
            "quotes": _quotes(by_n, a.lines),
            "matched": bool(hit),
            "match_reason": reason,
        }
        events.append(event("action.proposed", n, hit["key"] if hit else key, payload))
        if hit:
            matched += 1
        else:
            pending.append((n, a, owner))

    async with tenant_tx(tenant_id) as s:
        await append_events(s, tenant_id, events)
        ids = {
            r.idempotency_key: r.id
            for r in await s.execute(
                text("SELECT id, idempotency_key FROM events WHERE idempotency_key = ANY(:k)"),
                {"k": [ev.idempotency_key for ev in events]},
            )
        }
        for n, a, owner in pending:
            evidence = [ids[k] for k in (f"{key}:action.proposed:{n}",) if k in ids]
            args: dict[str, Any] = {"title": a.text[:200]}
            if owner:
                args["owner_user_id"] = str(owner)
            if a.due_date:
                args["due_date"] = a.due_date
            if meeting.project_id:
                args["project_id"] = str(meeting.project_id)
            await store_create_item(
                s,
                tenant_id,
                meeting_id=meeting_id,
                args=args,
                evidence_event_ids=evidence,
                expected_effect="Track a new task agreed in the meeting.",
                agent_run_id=extracted.agent_run_id,
                model=extracted.model,
                prompt_version=PROMPT,
            )
            proposals += 1
        summary = ExtractionSummary(
            len(extracted.decisions),
            len(extracted.action_items),
            matched,
            proposals,
            len(extracted.blockers),
            len(extracted.dropped),
        )
        await s.execute(
            text("UPDATE meetings SET status = 'extracted', extraction = CAST(:e AS jsonb) WHERE id = :id"),
            {"e": json.dumps({**summary.__dict__, "dropped_reasons": extracted.dropped}), "id": meeting_id},
        )
    return summary
