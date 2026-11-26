# Sprint 5 — Meetings, summaries, search, ship

Read `CLAUDE.md` first. Sprints 1–4 must be green. This sprint adds the remaining AI capabilities, then makes Daelys
deployable, demoable and explainable.

## Goal

A Hinglish standup recording becomes matched action items and blocker events; leads get a cited weekly summary;
anyone can ask Daelys a question within their permissions; and a stranger can open the live demo and run the scenario
without help.

## Build

1. **Notiv connector** (`connectors/notiv.py`). Shreya's Notiv repo sits at `../notiv` (confirm the path; if it is
   missing or its pipeline entry point is unclear, stop and ask instead of reimplementing). Call its
   transcription + extraction pipeline as a library or a local service. Normalize into events:
   `meeting.transcribed`, `decision.made`, `action.proposed`, `blocker.mentioned`. Speaker-to-user mapping table
   per tenant.
2. **Action-item matching:** pgvector top-k against existing work items, then an LLM confirm. Below threshold →
   proposal to create a new item (through the normal approval flow). Never silent merges.
3. **Weekly health summary** per lane using the explainer + citation checker pipeline. Regenerate keeps both
   versions in `agent_runs`.
4. **Duplicate detection:** on item creation, top-k similar items + pairwise LLM judge → suggestion only.
5. **Ask Daelys:** RAG over chunks + events. RBAC and tenant filter in SQL before ranking. Answers cite sources or
   say "not enough evidence".
6. **Light comments:** comments on work items, live via SSE. No presence avatars.
7. **Bench suites:** `meetings` (action-item F1, owner accuracy, due-date accuracy, reported separately for English
   and Hinglish), `duplicates` (precision at threshold), `faithfulness` (share of cited sentences + a sample file for
   manual review), `ops` (p50/p95 latency, tokens and cost per summary). `make bench` runs all suites and writes one
   combined `evaluation/results/latest.md`.
8. **Deploy:** `render.yaml` (api + worker), Neon Postgres with pgvector, managed Redis, env docs.
   Demo tenant with a "Reset scenario" button (admin-only endpoint that reseeds and replays).
9. **Docs:** `README.md` (one-line pitch, GIF placeholder, run in one command, DaelysBench table pulled from
   `latest.md`, cut list, known limitations, honest notes on what is sandboxed), `docs/ARCHITECTURE.md`,
   `docs/ONBOARDING.md` (deploying Daelys for a new team: permissions and why, first backfill, speaker mapping,
   approval tiers, verifying isolation), `TECHNICAL_DECISIONS.md` (Postgres over Neo4j, computed risk, typed actions,
   tiered approval, .ics fallback).
10. **.ics calendar fallback** connector (read-only). Google Calendar OAuth only if time remains.

## Acceptance

- `make test` and `make bench` green; all numbers in the README come from `latest.md`, none typed by hand.
- Meetings suite reports English and Hinglish separately.
- Deployed URL works: log in to the demo tenant, reset, replay, see the risk flip, approve a proposal.
- Fresh-clone check: `git clone && make up && make migrate && make seed` works with only `.env` filled.

## Out of scope

Billing, signup funnel, mobile, presence, Gantt, anything on the cut list.

## Needs Shreya

Record 3 meetings with friends (one Hinglish) and label gold action items; fill in all 10 meeting transcripts;
set Render/Neon accounts and secrets; record the 3-minute demo video; run the two-week pilot with a real team and
write the case study from real results.
