# Daelys — project context

AI operating system for teams: Brief → Plan → Signals → Risk → Explanation → Proposal → Approval → Action + Audit.
Rules for every session: [CLAUDE.md](../CLAUDE.md). Sprint specs: [process/sprints/](process/sprints/). Gates: [process/verification/](process/verification/).

## Stack (as built)

Python 3.11 · FastAPI · Pydantic v2 + pydantic-settings · SQLAlchemy 2 async (Core + `text()`, no ORM models) ·
asyncpg · Alembic · Postgres 16 + pgvector · Redis + arq · PyJWT (HS256 for users, RS256 for the GitHub App) ·
bcrypt · httpx · anthropic SDK · pypdf · langgraph · pg_trgm. Optional `meetings` extra: Notiv's
Whisper stack (faster-whisper, ffmpeg). Web: React 18 + htm in plain JS, vendored, no build step. Pinned in `pyproject.toml`; exact trees in `requirements.lock` (runtime) and
`requirements-dev.lock` (dev/CI image).

## Layout

| Path | Role |
| --- | --- |
| `api/` | FastAPI app. `deps.py` = auth + per-request tenant transaction; `bodies.py` = size-capped request bodies; `routes/` = auth (token + web session), work-items, events, webhooks, connectors, projects (briefs, plans), health |
| `core/` | config, db (engine, `tenant_tx`, SECURITY DEFINER lookups), events (`append_events`), security (JWT, bcrypt, roles), queue, ratelimit, logging, seed |
| `connectors/` | `base.py` protocol + cursors; `github.py` (webhook verify/normalize, REST backfill); `replay.py` (scenario timelines) |
| `workers/` | `projector.py` (events → work_items/edges); `main.py` (arq WorkerSettings) |
| `migrations/` | Alembic; `0001_foundation` = all tables, RLS, app role, grants; `0002_projects_plans` = projects, plan_drafts, plan fields; `0003_risk` = policies, risk columns, signal lifecycle; `0004_actions` = project_members, proposal lifecycle, auto-approve policy, audit tier; `0005_meetings_search` = meetings, speaker_mappings, summaries, duplicate_suggestions, comments, artifacts.min_role, FTS/trigram indexes, demo reset function; `0006_create_item_proposals` = item-less `create_item` proposals |
| `scenarios/tiffin_box/` | The demo scenario: a draft brief and week 1/2 timelines (independent rewrite tracked as PB-02) |
| `agents/` | `planner.py` (brief -> validated plan), `plan_validators.py`, `plan_schema.py`, `prompt_store.py`, versioned prompts in `prompts/` |
| `core/llm.py` | the only LLM entry point: ordered model fallback, structured output, `agent_runs` logging |
| `core/briefs.py`, `core/plans.py` | brief ingestion (text/PDF -> artifact + chunks); plan drafts and accept -> events |
| `evaluation/` | DaelysBench: `plan_suite.py` (loader + metrics), `run.py`, `datasets/plan/`, `results/` |
| `web/` | SPA served by FastAPI at `/`: `index.html`, `js/` (pages, api client), `vendor/` (React, htm) |
| `rules/` | `checks.py` (5 rules), `propagation.sql` (slip CTE), `scoring.py` (formula), `engine.py` (evaluate, persist, health, time-machine as-of) |
| `agents/explainer.py`, `agents/citations.py` | on-demand risk narrative (`explain_v1`); citation checker drops uncited sentences |
| `agents/proposer.py`, `agents/tools.py` | LangGraph proposer (gather → propose → validate) with read-only tools (`propose_v1`) |
| `core/actions.py`, `core/proposals.py`, `core/mailer.py` | typed actions, tiers, previews; proposal lifecycle + audit; Mailpit email |
| `workers/execute.py` | the executor: only writer of agent actions (approve-only, exactly once, audited, undo, sweep) |
| `core/realtime.py`, `core/sandbox.py` | LISTEN/NOTIFY -> SSE fan-out; workspace copy for scenario replays |
| `connectors/notiv.py`, `connectors/ics.py` | Notiv as a library (`NOTIV_PATH`) + labelled transcripts; `.ics` upload (read-only) |
| `core/meetings.py`, `agents/meeting_extractor.py`, `agents/matcher.py` | transcript -> cited decisions / action items / blockers -> matched events or `create_item` proposals |
| `core/retrieval.py`, `core/insights.py`, `agents/writers.py` | role-filtered retrieval (vector or FTS/trigram, mode reported); weekly summaries, duplicate suggestions, Ask Daelys |
| `api/routes/collab.py` | meetings, speakers, summaries, duplicates, ask, comments, calendar, demo reset, members |
| `tests/` | integration tests against real Postgres/Redis in `<db>_test` |

## Data flow

Connector → `NewEvent` → `append_events` (idempotent on `(tenant_id, idempotency_key)`, per-tenant advisory lock)
→ arq `project` job → `projector.project_tenant` folds events past the `connector_cursors['projector']` cursor into
`work_items` (per-field LWW) and `edges` (`closes` from "Closes #n").

## Plan flow

Brief (paste/upload) -> `artifacts` + `chunks` -> `POST /projects/{id}/plan` (lead+) -> planner (model call,
validation, <=1 repair) -> `plan_drafts` -> review UI (hover highlights brief spans) -> `POST .../plan/accept` ->
`plan.*` events -> projector -> `work_items` (lane, owner_role, estimate, source_span) + `depends_on` edges.

## Risk loop

events -> projector -> rules + propagation at `as_of` (policy clock: wall or latest event) -> `signals` (with evidence
event ids) + derived `risk_score`/`risk_level` on `work_items` -> NOTIFY -> SSE -> UI refetch. Health = worst level
of active items, per project and tenant-wide. "Why" = signals + evidence; optional LLM narrative with checked
citations. Time machine: `GET /time-machine/state?as_of=` (rolled-back rebuild), `POST /time-machine/replays`.

## Action loop

At-risk item → `POST /work-items/{id}/proposals/generate` (owner or project lead) → proposer (read-only) → validated
drafts stored as `proposals` (pending; tier 3 never auto-approved) → inbox with exact previews → approve / edit /
reject (reason) → arq `execute_proposal` → executor re-checks state and min(agent, approver) → events + `audit_log`
→ projector + risk → SSE. Tier 2 can be undone (inverse events). Tier 3: email via Mailpit locally, PR comment via
the GitHub App (marker-based exactly once).

## Meetings and search

Transcript or audio (Notiv, no diarization) -> numbered lines stored as a `transcript` artifact -> `meeting.transcribed`
-> worker `extract_meeting`: extractor (cited lines only) -> matching (retrieval top-k + LLM confirm) -> events on
matched items, or a pending `create_item` proposal (never a silent new item). Summaries and Ask answers pass the
citation checker; Ask returns "Not enough evidence" when nothing cited survives. Duplicates are suggestions only.

## Security model

- Every table has RLS; `tenants` is scoped by `id`. Policy: `tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid` → unset fails closed.
- API, worker, seed and replay connect as `daelys_app` (NOSUPERUSER, NOBYPASSRLS, not table owner). Migrations run as the owner.
- `events` and `audit_log`: app role has no UPDATE/DELETE.
- Pre-tenant lookups (tenant by slug, tenant by GitHub installation, list tenant ids) go through three SECURITY DEFINER functions that return ids only.
- Role is read from `memberships` on every request; the JWT role claim is not trusted for authz.
- Web app: JWT in an httpOnly SameSite=Strict cookie; cookie-authenticated writes need `X-Daelys-CSRF: 1`; strict CSP.
- Untrusted text (briefs, previous plans) reaches the model only inside escaped `<evidence>` blocks in the user turn.

## Local ports

Host ports come from `.env` (`PG_HOST_PORT`, `REDIS_HOST_PORT`, `API_HOST_PORT`). This machine uses 55432 / 56379 / 8019 because other stacks hold the defaults.
