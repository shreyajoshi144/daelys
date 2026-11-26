# Sprint 1 report — Foundation and ingestion

> **Historical record**, written at the end of the sprint. Items listed here as gaps or "Needs Shreya" are tracked
> today in the [product backlog](../project-management/PRODUCT_BACKLOG.md); current status is in the
> [closure report](../project-management/CLOSURE_REPORT.md).

## Built
- Schema (`migrations/versions/0001_foundation.py`): tenants, users, memberships (owner/lead/member/viewer/agent),
  integrations, events, work_items, edges, artifacts, chunks (`vector(1024)`), signals, agent_runs, proposals,
  audit_log, connector_cursors. `tenant_id` + RLS on every table; composite FKs stop cross-tenant references;
  indexes on tenant_id plus the hot filters of events/work_items/edges.
- App role `daelys_app`: NOSUPERUSER, NOBYPASSRLS, owns no tables, no UPDATE/DELETE on events/audit_log.
- Auth: tenant-scoped JWT login (bcrypt), `require_role(...)`, role re-read from memberships every request,
  login rate limiting.
- Connector protocol (`connectors/base.py`), replay connector + CLI, GitHub connector (HMAC-verified webhook for
  pull_request/issues/check_run/issue_comment, `X-GitHub-Delivery` idempotency, installation-token REST backfill with
  pagination, retries and cursor).
- Projector arq job: order-independent per-field LWW, `closes` edges, per-check CI rollup, poison-event skip,
  startup + 30 s sweep for resume.
- API: `/auth/login`, `/work-items`, `/work-items/{id}` (404 across tenants), `/events?since=&after_id=`,
  `/webhooks/github`, `/connectors/github/backfill` (lead+), `/health` (DB + Redis).
- Seed: Tiffin Box (9 users across Product/Engineering/Marketing + agent principal), Decoy Co (1 user), week 1 replayed.
- DRAFT scenario: `scenarios/tiffin_box/brief.md` (launch date 20 vs 27 March contradiction), 30-event week 1 and week 2.
- Tooling: docker compose (`postgres`, `redis`, `api`, `worker`), Makefile (`up migrate seed test cov lint audit replay`),
  CI workflow, Dockerfile (non-root), lockfiles.

## Real output (2026-11-04/05, this machine)

`make up && make migrate && make seed && make test` on a wiped volume, all in containers:
```
seed.done {"replay": {"appended": 30, "duplicates": 0, "resumed_from": 0, "total": 30}, "projected": 30}
53 passed in 16.37s
api Up (healthy) · /health → {"status":"ok","checks":{"db":"ok","redis":"ok"}}
```
`make lint`: ruff check — All checks passed; ruff format — 42 files already formatted; mypy — no issues in 30 files.
`make cov`: 53 passed, total 89%; core/ 83% (logging 47%, seed 63%, queue 79%, ratelimit 81%, rest 100%).
`pip-audit` on both lockfiles: No known vulnerabilities found.

Acceptance tests: `tests/test_isolation.py` (8), `tests/test_github_webhook.py` (14), `tests/test_replay.py` (8),
`tests/test_projector.py` (8, worker restart/crash resume), plus auth (10), backfill (3), health (2).

## Sign-off (fill in after gates)

| Gate | Result | Notes |
| --- | --- | --- |
| 1 Fresh clone | partial | Clean volume run passed; not from a git clone (no repo yet) |
| 2 Lint, audit, coverage | lint pass; pip-audit pass; gitleaks not run | coverage on core modules: 83% |
| 3 Invariant greps | explained | Only hits: `tests/conftest.py:63-64` `DROP/CREATE DATABASE` with the test DB name derived from `DATABASE_URL` (identifiers can't be bound) |
| 4 Independent review | not run | |
| 5 Break-it checks | 11 / 14 run, 11 passed | 1–6, 9, 10, 12, 13 by hand on the live stack; 7–8 by tests. 11 (real GitHub) and 14 (CI) need accounts |
| Bench numbers | n/a in Sprint 1 | |
| What I can explain without notes | | |

## Known gaps
- Real GitHub never contacted: webhooks tested with locally signed payloads, backfill against an httpx MockTransport
  stand-in (labelled in the test). Without App credentials `/connectors/github/backfill` returns 503 `not_configured`.
- Edges are add-only; removing "Closes #n" from a PR body does not delete the edge.
- `make audit` needs gitleaks installed and a git repo.
- One unexplained early stop of two background replays during check 13 (host appeared to sleep mid-command); not
  reproducible, and the interrupted replay resumed from its cursor to the identical final state.
- Owner resolution maps `github_login` → user only at projection time; adding a login later does not backfill owners.

## Decisions
See `TECHNICAL_DECISIONS.md` S1-01 … S1-09 (LWW projection, advisory-locked appends, poison events, NULLIF RLS policy,
no FORCE RLS, Core SQL, tenant-scoped login, normalized timelines, write endpoints own their transaction) and the
dependency list there.

## Needs Shreya
1. Create the GitHub org + `tiffin-box-app` repo and register the GitHub App (permissions: issues, pull requests,
   checks read; webhook events: pull_request, issues, check_run, issue_comment).
2. Put `GITHUB_WEBHOOK_SECRET`, `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY_PATH` (PEM outside the repo) and
   `SEED_GITHUB_INSTALLATION_ID` in `.env`, then `make seed`.
3. Run smee.io to forward webhooks to `localhost:$API_HOST_PORT/webhooks/github`; run check 11.
4. Rewrite `scenarios/tiffin_box/brief.md` and both timelines in your own words (they are marked DRAFT).
5. `git init`, push, confirm CI is green and the isolation tests ran (check 14); `brew install gitleaks`, run `make audit`.
6. Gate 4: independent review in a new session with `docs/process/verification/REVIEW_PROMPT.md`.
