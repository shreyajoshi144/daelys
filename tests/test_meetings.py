"""Meetings: parsing, real Notiv transcription, extraction validation, matching, no silent merges."""

import asyncio
import os
import shutil
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from agents.matcher import MatchVerdict
from agents.meeting_extractor import LLMMeeting, validate
from connectors.notiv import Line, parse_transcript, render, transcribe_audio
from core.db import tenant_tx
from core.llm import LLM, Completion, Route
from core.meetings import create_meeting, extract_meeting
from core.security import Role
from tests.actions_helpers import CHECKOUT, build_scenario, propose
from tests.conftest import auth_headers
from workers.execute import execute, undo

STANDUP = """[00:01] Meera: Quick standup for the subscriptions launch.
[00:15] Arjun: Checkout PR is still red, Razorpay sandbox keys abhi tak nahi aaye.
[00:40] Meera: Arjun, please chase finance for the keys by Friday.
[01:02] Priya: Main FAQ page likh dungi, launch FAQ by Thursday.
[01:20] Meera: Decided: hum launch 27 March ko hi karenge.
"""
MEETING_DAY = datetime(2026, 3, 11, 10, 0, tzinfo=UTC)


class Scripted:
    """Plays the extractor, then the matcher, from fixed answers."""

    name = "scripted"

    def __init__(self, meeting: dict[str, Any], matches: dict[str, str | None]) -> None:
        self.meeting, self.matches = meeting, matches

    async def complete(
        self, model: str, system: str, user: str, output_type: Any, max_tokens: int
    ) -> Completion[Any]:
        if output_type is LLMMeeting:
            return Completion(parsed=LLMMeeting.model_validate(self.meeting), input_tokens=1, output_tokens=1)
        key = next((v for k, v in self.matches.items() if k in user), None)
        return Completion(
            parsed=MatchVerdict(match_key=key, reason="scripted"), input_tokens=1, output_tokens=1
        )


def llm(model: Scripted) -> LLM:
    return LLM([Route("scripted", "m")], {"scripted": model})


def test_parse_labelled_transcript() -> None:
    lines = parse_transcript(STANDUP + "\nunlabelled line\nhttps://example.com: not a speaker\n")
    assert [(line.n, line.speaker) for line in lines[:2]] == [(1, "Meera"), (2, "Arjun")]
    assert lines[1].start == 15.0 and lines[1].text.startswith("Checkout PR")
    assert lines[5].speaker is None and lines[6].speaker is None
    assert render(lines[:1]) == "[L1] Meera: Quick standup for the subscriptions launch."


def test_extraction_validation_drops_uncited_and_clears_bad_owner_and_date() -> None:
    lines = parse_transcript(STANDUP)
    raw = LLMMeeting.model_validate(
        {
            "decisions": [
                {"text": "Launch stays on 27 March", "lines": [5]},
                {"text": "Invented", "lines": [99]},
            ],
            "action_items": [
                {
                    "text": "Chase finance for keys",
                    "owner_speaker": "Arjun",
                    "due_date": "2026-03-13",
                    "lines": [3],
                },
                {
                    "text": "Write launch FAQ",
                    "owner_speaker": "Rohit",
                    "due_date": "2031-01-01",
                    "lines": [4],
                },
            ],
            "blockers": [{"text": "Checkout blocked on sandbox keys", "lines": []}],
        }
    )
    out = validate(raw, lines, date(2026, 3, 11))
    assert [d.text for d in out.decisions] == ["Launch stays on 27 March"] and out.blockers == []
    assert out.action_items[0].due_date == "2026-03-13"
    assert (out.action_items[1].owner_speaker, out.action_items[1].due_date) == (None, None)
    assert len(out.dropped) == 4


async def test_extract_matches_known_items_and_proposes_unknown(
    client: httpx.AsyncClient, database: None
) -> None:
    sc = await build_scenario()
    lines = parse_transcript(STANDUP)
    meeting_id = await create_meeting(
        sc.tenant.id,
        title="Standup",
        occurred_at=MEETING_DAY,
        language="auto",
        source="transcript",
        lines=lines,
        project_id=sc.project_id,
        created_by=sc.user_ids[Role.owner],
    )
    async with tenant_tx(sc.tenant.id) as s:
        items_before = await s.scalar(text("SELECT count(*) FROM work_items"))
    model = Scripted(
        {
            "decisions": [{"text": "Launch stays on 27 March", "lines": [5]}],
            "action_items": [
                {
                    "text": "Finish subscription checkout plans and Razorpay",
                    "owner_speaker": "Arjun",
                    "due_date": "2026-03-13",
                    "lines": [3],
                },
                {
                    "text": "Write release notes for subscriptions",
                    "owner_speaker": "Priya",
                    "due_date": None,
                    "lines": [4],
                },
            ],
            "blockers": [{"text": "Subscription checkout blocked on Razorpay sandbox keys", "lines": [2]}],
        },
        {"Subscription checkout blocked": CHECKOUT, "Finish subscription checkout": CHECKOUT},
    )
    summary = await extract_meeting(llm(model), sc.tenant.id, meeting_id)
    assert (
        summary.decisions,
        summary.action_items,
        summary.matched,
        summary.proposals,
        summary.blockers,
    ) == (1, 2, 1, 1, 1)
    async with tenant_tx(sc.tenant.id) as s:
        assert await s.scalar(text("SELECT count(*) FROM work_items")) == items_before  # no silent new item
        blocker = (
            await s.execute(
                text("""
            SELECT subject_key, payload FROM events
            WHERE type = 'blocker.mentioned' AND payload->>'meeting_id' = :m
        """),
                {"m": str(meeting_id)},
            )
        ).one()
        proposal = (await s.execute(text("SELECT * FROM proposals WHERE action_type = 'create_item'"))).one()
    assert blocker.subject_key == CHECKOUT and blocker.payload["quotes"][0].startswith("Arjun:")
    assert (
        proposal.status == "pending" and proposal.work_item_id is None and proposal.meeting_id == meeting_id
    )
    assert proposal.args["title"] == "Write release notes for subscriptions"
    assert proposal.args["project_id"] == str(sc.project_id)

    # Normal approval flow; then the executor creates the item, and undo cancels it.
    lead = await auth_headers(client, sc.tenant, Role.lead)
    listed = (await client.get(f"/proposals/{proposal.id}", headers=lead)).json()
    assert listed["preview"]["new_item"] == listed["item_title"] == "Write release notes for subscriptions"
    assert (await client.post(f"/proposals/{proposal.id}/approve", headers=lead)).status_code == 202
    assert (await execute(sc.tenant.id, proposal.id)).status == "executed"
    async with tenant_tx(sc.tenant.id) as s:
        created = (
            await s.execute(
                text("SELECT title, status, project_id FROM work_items WHERE key = :k"),
                {"k": f"meeting-item:{proposal.id}"},
            )
        ).one()
    assert (created.title, created.status, created.project_id) == (
        "Write release notes for subscriptions",
        "open",
        sc.project_id,
    )
    from core.security import Principal

    await undo(sc.tenant.id, proposal.id, Principal(sc.user_ids[Role.owner], sc.tenant.id, Role.owner))
    async with tenant_tx(sc.tenant.id) as s:
        assert (
            await s.scalar(
                text("SELECT status FROM work_items WHERE key = :k"), {"k": f"meeting-item:{proposal.id}"}
            )
            == "cancelled"
        )


async def test_transcript_api_and_speakers(client: httpx.AsyncClient, database: None) -> None:
    sc = await build_scenario()
    member = await auth_headers(client, sc.tenant, Role.member)
    viewer = await auth_headers(client, sc.tenant, Role.viewer)
    body = {
        "title": "Standup",
        "occurred_at": MEETING_DAY.isoformat(),
        "transcript": "Zed: hello there",
        "project_id": str(sc.project_id),
    }
    assert (await client.post("/meetings/transcript", json=body, headers=viewer)).status_code == 403
    created = await client.post("/meetings/transcript", json=body, headers=member)
    assert created.status_code == 201
    meeting = (await client.get(f"/meetings/{created.json()['meeting_id']}", headers=member)).json()
    assert meeting["speakers"] == {"Zed": None} and meeting["lines"][0]["text"] == "hello there"
    assert meeting["events"][0]["type"] == "meeting.transcribed"
    assert (await client.get(f"/meetings/{created.json()['meeting_id']}", headers=viewer)).status_code == 403
    lead = await auth_headers(client, sc.tenant, Role.lead)
    resp = await client.put(
        "/speakers", json={"label": "Zed", "user_id": str(sc.user_ids[Role.member])}, headers=lead
    )
    assert resp.status_code == 204
    meeting = (await client.get(f"/meetings/{created.json()['meeting_id']}", headers=member)).json()
    assert meeting["speakers"] == {"Zed": str(sc.user_ids[Role.member])}


@pytest.mark.skipif(
    not os.environ.get("NOTIV_PATH") or shutil.which("say") is None or shutil.which("ffmpeg") is None,
    reason="needs NOTIV_PATH, ffmpeg and macOS `say` to synthesise speech",
)
async def test_real_notiv_transcription(tmp_path: Path) -> None:
    audio = tmp_path / "standup.aiff"
    say = shutil.which("say")
    assert say is not None
    proc = await asyncio.create_subprocess_exec(
        say, "-o", str(audio), "Checkout is blocked on the sandbox keys. Arjun will chase finance."
    )
    assert await proc.wait() == 0
    lines = await transcribe_audio(audio.read_bytes(), ".aiff", "en")
    words = " ".join(line.text for line in lines).lower()
    assert "blocked" in words and "finance" in words and all(line.speaker is None for line in lines)


async def test_unmatched_owner_free_proposal_needs_owner_or_project_lead(
    client: httpx.AsyncClient, database: None
) -> None:
    sc = await build_scenario(project_lead=False)
    lines = [Line(1, "Meera", "Someone should write release notes")]
    meeting_id = await create_meeting(
        sc.tenant.id,
        title="Sync",
        occurred_at=MEETING_DAY,
        language="en",
        source="transcript",
        lines=lines,
        project_id=sc.project_id,
        created_by=sc.user_ids[Role.owner],
    )
    model = Scripted(
        {
            "decisions": [],
            "blockers": [],
            "action_items": [
                {"text": "Write release notes", "owner_speaker": None, "due_date": None, "lines": [1]}
            ],
        },
        {},
    )
    await extract_meeting(llm(model), sc.tenant.id, meeting_id)
    async with tenant_tx(sc.tenant.id) as s:
        pid = await s.scalar(text("SELECT id FROM proposals WHERE action_type = 'create_item'"))
    lead = await auth_headers(client, sc.tenant, Role.lead)  # not a lead of this project
    assert (await client.post(f"/proposals/{pid}/approve", headers=lead)).status_code == 403
    owner = await auth_headers(client, sc.tenant, Role.owner)
    assert (await client.post(f"/proposals/{pid}/approve", headers=owner)).status_code == 202
    assert await propose(sc, "move_due", {"new_due_date": "2026-03-20"})  # ordinary proposals still work
