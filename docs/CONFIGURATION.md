# Configuration reference

Settings are read from the environment (and `.env` locally) by `core/config.py`. [`.env.example`](../.env.example)
lists every variable with a one-line comment. Anything optional that is missing turns its feature off with a clear
`503 not_configured`; nothing falls back to a fake.

## Required

| Variable | Used by | Meaning |
| --- | --- | --- |
| `APP_DATABASE_URL` | api, worker, seed, replay | Connection as `daelys_app`. **Never** a superuser or the table owner |
| `REDIS_URL` | api, worker | Jobs and rate limits. Default `redis://localhost:6379/0` |
| `JWT_SECRET` | api | HS256 signing secret, at least 32 characters |

## Database setup (migrations only)

| Variable | Meaning |
| --- | --- |
| `DATABASE_URL` | Owner connection for `alembic upgrade head`. The running app doesn't need it |
| `DAELYS_APP_PASSWORD` | Password the first migration gives the `daelys_app` role |
| `POSTGRES_PASSWORD` | Superuser password for the local Postgres container (local only) |

## Auth and web

| Variable | Default | Meaning |
| --- | --- | --- |
| `JWT_TTL_MINUTES` | 60 | Token and session lifetime (1–1440) |
| `LOGIN_RATE_LIMIT_PER_MINUTE` | 10 | Login attempts per client IP and per account |
| `COOKIE_SECURE` | false | Mark the session cookie `Secure` and send HSTS. **Set true** anywhere served over HTTPS |
| `FORWARDED_ALLOW_IPS` | — | Read by uvicorn. Set to the load balancer so per-IP limits see real clients (Render: `*`) |

## Models

| Variable | Default | Meaning |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | — | Enables planning, explanations, proposals, meeting extraction, summaries, Ask and the model bench suites |
| `ANTHROPIC_API_BASE` | `https://api.anthropic.com` | Override only for a proxy |
| `LLM_ROUTES` | `anthropic:claude-opus-5-5,anthropic:claude-sonnet-5-5` | Ordered fallback chain; later entries serve on timeout, 429, 5xx or refusal |
| `LLM_TIMEOUT_SECONDS` | 180 | Per call |
| `LLM_RATE_LIMIT_PER_MINUTE` | 20 | Per workspace, per model endpoint |
| `VOYAGE_API_KEY` | — | 1024-dim embeddings for vector search. Without it, search uses full-text and trigram matching and says so |
| `EMBEDDING_MODEL` | `voyage-3.5` | Must output 1024 dimensions |

## GitHub App

| Variable | Default | Meaning |
| --- | --- | --- |
| `GITHUB_WEBHOOK_SECRET` | — | Webhooks are refused (503) while empty |
| `GITHUB_APP_ID` | — | For REST backfill and PR comments |
| `GITHUB_APP_PRIVATE_KEY_PATH` | — | Path to the App's PEM. Keep it outside the repo (Render: a secret file) |
| `GITHUB_API_URL` | `https://api.github.com` | Override only for GitHub Enterprise |

## Meetings

| Variable | Default | Meaning |
| --- | --- | --- |
| `NOTIV_PATH` | — | Path to the Notiv repo; its Whisper transcription is used as a library. Empty: audio upload is 503, labelled transcripts still work |
| `NOTIV_HOST_PATH` | `./.notiv` | Host folder mounted read-only into the containers at `/notiv` (Compose) |
| `NOTIV_WHISPER_MODEL` | `small` | Read by Notiv: `tiny`, `base`, `small`, `medium` … |

## Email (`notify` action)

| Variable | Default | Meaning |
| --- | --- | --- |
| `SMTP_HOST` | — | Empty: `notify` is 503. Locally this is Mailpit. The mailer has no TLS or auth yet |
| `SMTP_PORT` | 1025 | |
| `SMTP_FROM` | `daelys@localhost` | Sender address |

## Seed and demo

| Variable | Meaning |
| --- | --- |
| `SEED_USER_PASSWORD` | Password for every seeded demo user. Local and demo only |
| `SEED_GITHUB_INSTALLATION_ID` | Links a GitHub App installation to the Tiffin Box workspace |

## Local ports (Compose)

| Variable | Default | Meaning |
| --- | --- | --- |
| `PG_HOST_PORT` | 5432 | Host port for Postgres |
| `REDIS_HOST_PORT` | 6379 | Host port for Redis |
| `API_HOST_PORT` | 8000 | Host port for the API and web app |
| `MAILPIT_SMTP_HOST_PORT` | 1025 | Mailpit SMTP |
| `MAILPIT_UI_HOST_PORT` | 8025 | Mailpit inbox in the browser |

## Logging

| Variable | Default | Meaning |
| --- | --- | --- |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. Logs never include tokens, passwords or transcripts |

## Per-workspace policy (database, not environment)

Stored in the `policies` table, one row per workspace.

| Column | Default | Meaning |
| --- | --- | --- |
| `stale_pr_days` | 3 | Days without activity before a PR is stale (1–60) |
| `due_window_days` | 5 | How close a due date must be for stale-PR and CI rules (1–60) |
| `blocker_window_days` | 7 | How long a reported blocker counts (1–60) |
| `overload_limit` | 3 | Open tasks due within 7 days before an owner is overloaded (1–50) |
| `clock` | `wall` | `wall` uses real time; `event` uses the latest observed work event (replayed scenarios) |
| `auto_approve_max_tier` | 0 | Highest tier that may be auto-approved (0–2); 0 = never. Tier 3 can't be auto-approved |
| `proposal_ttl_hours` | 48 | How long a proposal stays open before it expires |
