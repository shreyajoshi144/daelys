# Sprint 1 — Foundation and ingestion

Read `CLAUDE.md` first and follow it. This sprint makes the data spine real: tenants, roles, isolation,
the event log, and two working connectors (replay and GitHub).

## Goal

By the end, a webhook from a real GitHub repo or a replayed scenario becomes deduplicated events in Postgres,
projected into work items, and tenant B can never see tenant A's data.

## Build

1. **Project skeleton** per the layout in CLAUDE.md. `docker-compose.yml` with `pgvector/pgvector:pg16`, redis,
   api, worker. Makefile with `up`, `migrate`, `seed`, `test`. `.env.example`.
2. **Schema (Alembic):** `tenants`, `users`, `memberships(user_id, tenant_id, role)` with roles
   owner/lead/member/viewer/agent, `events`, `work_items`, `edges`, `artifacts`, `chunks(embedding vector(1024))`,
   `signals`, `proposals`, `audit_log`, `agent_runs`, `connector_cursors`. Columns as in the blueprint data model.
   `events.idempotency_key` unique per tenant. `tenant_id` on every table.
3. **Row-level security:** enable RLS on every tenant table with
   `USING (tenant_id = current_setting('app.tenant_id')::uuid)`. Create a non-superuser app role. FastAPI dependency
   opens a transaction and runs `SET LOCAL app.tenant_id`.
4. **Auth:** JWT login (email + password, bcrypt), claims `sub`, `tenant_id`, `role`. `require_role(...)` dependency.
5. **Connector protocol** in `connectors/base.py`: `backfill`, `handle_webhook`, `normalize`, cursor persistence.
6. **Replay connector:** reads `scenarios/<name>/timeline_*.json`, emits events with a simulated clock and a speed
   factor. CLI: `python -m connectors.replay tiffin_box week2 --speed 60`.
7. **GitHub connector:** GitHub App webhook endpoint `POST /webhooks/github`. Verify `X-Hub-Signature-256`.
   Handle `pull_request`, `issues`, `check_run`, `issue_comment`. Use `X-GitHub-Delivery` as idempotency key.
   REST backfill using an installation token. Out-of-order events must not corrupt projections (compare
   `occurred_at`).
8. **Projection worker:** arq job that folds new events into `work_items` and `edges` (e.g. PR linked to an issue
   via "Closes #12" creates an edge).
9. **Seed:** two tenants (Tiffin Box, Decoy Co), 9 users for Tiffin Box across Product/Engineering/Marketing,
   1 user for Decoy. Create placeholder `scenarios/tiffin_box/` with `brief.md` (a messy one-page brief with one
   date contradiction), `timeline_week1.json` and `timeline_week2.json` (~30 events each: PR opened, CI failing,
   issue comments, calendar items). Mark these as drafts for Shreya to edit.
10. **API (minimal):** `GET /work-items`, `GET /work-items/{id}` (returns 404 for other tenants' IDs),
    `GET /events?since=`, `GET /health` (checks DB and Redis).
11. **Verification tooling:** Makefile targets `lint` (ruff check, ruff format --check, mypy), `audit` (pip-audit,
    npm audit when `web/` exists, gitleaks), `cov` (pytest --cov). Use these exact names so
    `docs/process/verification/` works unchanged: compose services `postgres`, `redis`, `api`, `worker`; database `daelys`;
    app role `daelys_app` (not superuser, no BYPASSRLS; if it owns the tables, use `FORCE ROW LEVEL SECURITY`).
    Add indexes on `tenant_id` plus the main filter columns of `events`, `work_items` and `edges`.

## Acceptance (all must pass, show real output)

- `make up && make migrate && make seed` works on a clean machine.
- `tests/test_isolation.py`: as a Decoy Co user, every GET endpoint queried with Tiffin Box IDs returns 404/empty;
  a raw SQL query through the app role with tenant B set returns zero tenant A rows.
- `tests/test_github_webhook.py`: valid signature accepted; bad signature 401; same delivery twice → one event.
- `tests/test_replay.py`: replaying week 2 produces the expected count of events and work items.
- Worker restart resumes from `connector_cursors` (test it).
- CI workflow (`.github/workflows/ci.yml`) runs `make test` with a Postgres service.

## Out of scope this sprint

Frontend, LLM calls, rules, agents.

## Needs Shreya (list these in the report)

Create the GitHub org + `tiffin-box-app` repo, register the GitHub App, set secrets in `.env`, run smee.io for
local webhooks, and rewrite the draft brief and timelines in her own words.
