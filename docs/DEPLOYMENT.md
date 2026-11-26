# Deployment: where it stands and what is left

**Status (2026-11-25): code and config are ready for a first deploy, but nothing is deployed yet.** The remaining
work is mostly accounts and secrets. It is tracked as PB-03 and PB-04 in the
[product backlog](project-management/PRODUCT_BACKLOG.md#4-open-backlog). Target: Render (api + worker + Key Value) with Neon
Postgres, as described in [`render.yaml`](../render.yaml).

## Readiness at a glance

| Area | State | Evidence |
| --- | --- | --- |
| Production image | ✅ Ready | Built with production defaults: 482 MB, no test tooling, no audio stack. Booted on `PORT=10000`; `/health` returned `{"db":"ok","redis":"ok"}` and `/` returned 200 |
| Migrations | ✅ Ready | `alembic upgrade head` (0001–0006) runs as Render's `preDeployCommand` and creates the RLS policies and the `daelys_app` role |
| Tests and lint | ✅ Green locally | 159 tests: 158 passed, 1 skipped (macOS-only), 2026-11-25. ruff and mypy clean; pip-audit clean ([QA report](project-management/QA_REPORT.md)) |
| CI | ⚠️ Written, never run | `.github/workflows/ci.yml` (lint, tests, migrate, safety gate) runs on the first push |
| Security config | ✅ Ready | `COOKIE_SECURE=true`; strict CSP; same-origin only (no CORS); `FORWARDED_ALLOW_IPS` set so per-IP login limits see real clients |
| Git repository | ❌ Not pushed | No remote yet |
| Hosting accounts | ❌ Missing | Render and Neon accounts, and the Render blueprint instance |
| Secrets | ❌ Missing | Neon URLs, `DAELYS_APP_PASSWORD`, `SEED_USER_PASSWORD`; optional `ANTHROPIC_API_KEY`, GitHub App |
| Post-deploy checks | ❌ Not run | Load test, cold-deploy notes, recovery and stranger test (`docs/process/verification/SPRINT_5_CHECKS.md` 9–16) |

## What works on the first deploy, and what doesn't

| Feature | On Render without extra keys | Needs |
| --- | --- | --- |
| Sign-in, workspace, board, risk, time machine, demo reset, audit, comments | ✅ | — |
| Labelled-transcript meetings (upload, speakers) | ✅ | — |
| Planning, explanations, proposals, meeting extraction, summaries, Ask | `503 not_configured` | `ANTHROPIC_API_KEY` |
| Vector search | Lexical search instead (and says so) | `VOYAGE_API_KEY` |
| GitHub webhooks, backfill, PR comments | `not_configured` | A GitHub App |
| Email `notify` | `not_configured` | **A code change:** the mailer has no TLS/auth yet (fine for Mailpit, not for a real relay) |
| Audio meetings (Notiv/Whisper) | `not_configured` | Notiv is a separate local repo and the image leaves out the 1.5 GB audio stack. Keep audio local-only, or vendor Notiv and use a larger plan |

## Steps, in order

1. **GitHub (15 min).** Create a private repo, push, and check that the CI run is green.
2. **Neon (20 min).**
   1. Create a Postgres 16 project.
   2. Copy the owner connection string and add `?ssl=require`.
   3. Pick a strong `DAELYS_APP_PASSWORD`.
   4. Derive two URLs:
      - `DATABASE_URL=postgresql+asyncpg://<owner>:<pw>@<host>/<db>?ssl=require` (migrations only)
      - `APP_DATABASE_URL=postgresql+asyncpg://daelys_app:<DAELYS_APP_PASSWORD>@<host>/<db>?ssl=require`
   5. The first migration creates `vector`, `pg_trgm` and the `daelys_app` role. Neon's default owner can do this;
      check that the first deploy's migration log ends at `0006`.
3. **Render (30 min).**
   1. New → Blueprint → this repo.
   2. Fill every `sync: false` value on `daelys-api`.
   3. Copy the same values into the `daelys-shared` env group for the worker, **including `JWT_SECRET`**: the
      worker refuses to start without it, and Render generates the API's value, so copy it after the first deploy.
      Leave `DATABASE_URL` (owner credentials) on the API only: only its pre-deploy migration uses it, and the
      running app never needs it.
   4. Optional: put the GitHub App key in a secret file at `/etc/secrets/github-app.pem`.
4. **Seed the demo workspace (5 min).** In the API's Render Shell, run `SEED_USER_PASSWORD=<8+ chars> python -m
   core.seed`. That creates the `tiffin-box` demo workspace and the decoy workspace used by the isolation checks.
5. **Smoke test (10 min).**
   1. `/health` returns ok.
   2. Open the URL → Try the demo → sign in.
   3. Time machine → replay `week1,week2` → watch the flip to At risk.
   4. Reset the demo.
6. **Verify isolation on the real database (15 min).** Run `docs/process/verification/SPRINT_1_CHECKS.md` checks 1–6 against
   Neon, then run the safety suite against staging (`make bench SUITE=safety` with the deployed database URLs).
7. **Production checks (1–2 h).** Run `docs/process/verification/SPRINT_5_CHECKS.md` 9–16:
   - cold deploy notes
   - log search for secrets
   - 30 rapid logins (rate limit)
   - `hey -z 2m -c 20` on read endpoints: record p95 and errors in the README
   - a worker restart during a replay
   - the stranger test
   - cost per summary and per replay

## Cost and sizing (check current Render and Neon prices before committing)

- `render.yaml` asks for `standard` (2 GB) for both API and worker. The production image has no audio stack, so
  `starter` (512 MB) is probably enough for a demo. Not measured yet: watch memory during the replay test before
  downsizing.
- Render Key Value `starter` for Redis; Neon's free tier is enough for a demo workspace.
- LLM cost per summary and per replay is logged in `agent_runs`, but only measurable once a key is set.

## Fixed while preparing this

- **Dockerfile defaults.** The image used to default to dev tools and the audio stack, and `render.yaml` didn't
  override them. It now defaults to production-safe settings, and `docker-compose.yml` opts in locally.
- **Port.** The start command listens on `$PORT` (Render), defaulting to 8000.
- **Rate limiting behind a proxy.** uvicorn trusted `X-Forwarded-For` only from 127.0.0.1. Behind Render's load
  balancer, every user would have shared one login rate limit. `FORWARDED_ALLOW_IPS` is now set in
  `render.yaml`.
- **Owner credentials.** The app no longer requires the owner `DATABASE_URL` at runtime, so the worker doesn't
  get it.
- **HTTPS headers.** With `COOKIE_SECURE=true`, responses send `Strict-Transport-Security`. All responses send
  `Referrer-Policy`.
- **Uploads.** Brief, audio, `.ics` and webhook bodies are read with a hard cap, so a chunked upload can't
  exhaust memory.
- **Resilience.** Postgres, Redis and Mailpit restart with Docker. Live updates recover after a database restart.

## Rollback and recovery

- **Code:** Render → service → Deploys → roll back to the previous deploy. Migrations are additive (0001–0006),
  so the previous code still runs against the newer schema.
- **Data:** Neon → Branches → Restore to a timestamp. Restore into a new branch, point a staging deploy at it,
  verify, then switch. Details are in [ONBOARDING.md](ONBOARDING.md).
