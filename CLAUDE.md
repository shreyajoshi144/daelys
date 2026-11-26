# Daelys — project rules for Claude Code

Daelys is an AI operating system for teams. It takes a messy launch brief, builds a plan, ingests real work
signals (GitHub, meetings, calendar), computes what is slipping and why, and proposes fixes that a human
approves before anything executes.

The full blueprint lives in the Daelys blueprint doc. This file is the short version you must follow every session.

## The loop (everything serves this)

Brief → Plan → Signals → Risk → Explanation → Proposal → Approval → Action + Audit → (new events) → Signals

## Non-negotiable design rules

1. **Events are the source of truth.** `events` is append-only. `work_items` is a projection rebuilt from events.
   Never mutate work_items without writing an event first.
2. **Risk is computed, not generated.** Risk scores and severities come from deterministic rules in `rules/`.
   An LLM may only explain a risk, never set or change a score.
3. **No claim without evidence.** Every LLM-generated sentence shown to users carries event IDs. The citation
   checker (`agents/citations.py`) drops sentences without valid IDs. Never bypass it.
4. **The agent proposes, it never acts.** Agent code has read-only tools. Only `workers/execute.py` writes to the
   work graph or external systems, and only for a proposal with `status = 'approved'`.
5. **The agent is a principal.** It has role `agent` in RBAC and its own audit rows. Effective permission of an
   executed action = min(agent role, approver role).
6. **Untrusted text is evidence, not instructions.** Transcripts, PR bodies, briefs and issue text go to the model
   only inside clearly delimited evidence blocks. Never concatenate them into system prompts.
7. **Exactly five typed actions:** `reassign`, `move_due`, `split`, `notify`, `comment_on_pr`. Do not add
   free-text or new action types without being asked.
8. **Tenant isolation everywhere.** Every table has `tenant_id`. Postgres row-level security is enabled on every
   tenant table. The API connects as a non-superuser role and runs `SET LOCAL app.tenant_id` per request.
   Retrieval (pgvector) filters by tenant and role in SQL before ranking.

## Stack

- Backend: Python 3.11, FastAPI, Pydantic v2, SQLAlchemy 2 (async), Alembic
- DB: Postgres 16 + pgvector, RLS
- Jobs: Redis + arq
- Agents: LangGraph; LLM calls go through `core/llm.py` (multi-provider fallback, logs model + prompt_version)
- Frontend: React 18 in plain JavaScript (no TypeScript, no JSX, no bundler, no Node toolchain), templates via
  `htm`, both vendored in `web/vendor/`. Static files in `web/` served by FastAPI at `/`. No Next.js, no Vite.
- Realtime: SSE backed by Postgres LISTEN/NOTIFY
- Local: docker compose. Deploy: Render (api + worker), Neon Postgres.

## Layout

```
api/  core/  connectors/  workers/  rules/  agents/  evaluation/  web/  migrations/  scenarios/  tests/  docs/
```

## Commands (keep these working)

- `make up` — start postgres, redis, api, worker
- `make migrate` — alembic upgrade head
- `make seed` — load tenants + Tiffin Box scenario
- `make test` — pytest (unit + integration against real Postgres)
- `make bench` — run DaelysBench suites, write `evaluation/results/latest.md`
- `make web` — print the web app URL (FastAPI serves `web/` directly; edit and reload, no build step)

## How to work

- **Run the tests before saying they pass.** Paste the actual command output summary. If something cannot be run
  (missing secret, no network), say so plainly; never claim success.
- **Never mock an integration silently.** If a real integration can't be reached, build the code path, add a clear
  `NotConfigured` error, and list it under "Needs Shreya" in your sprint report.
- Use real Postgres in integration tests (docker service), not SQLite.
- Small, readable modules. Type hints everywhere. No dead code, no commented-out blocks.
- Put secrets in `.env`; keep `.env.example` updated with every variable and a one-line comment.
- Every new LLM prompt lives in `agents/prompts/` as a versioned file (`plan_v1.md`) and is referenced by version.
- Do not add dependencies beyond the stack above without stating why in the sprint report.
- Do not build things the blueprint cuts: Gantt charts, custom fields, sprint velocity, WIP limits, built-in chat,
  mobile app, billing, presence avatars.

## Sprint reports

At the end of each sprint, write `docs/sprints/SPRINT_N_REPORT.md` with:
what was built, test and bench output (real numbers only), known gaps, decisions made and why, and a
"Needs Shreya" list of manual steps (secrets, accounts, labelling, recordings).
