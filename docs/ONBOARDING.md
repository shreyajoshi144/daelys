# Onboarding a new team

## 1. Deploy (Render + Neon)

1. **Neon:** create a project (Postgres 16). Enable extensions are installed by the migration (`vector`, `pg_trgm`);
   the owner role must be allowed to create them (Neon's default owner can).
2. Create the app role password: pick a strong value for `DAELYS_APP_PASSWORD`. The first migration creates
   `daelys_app` (NOSUPERUSER, NOBYPASSRLS) and sets that password.
3. Connection strings (both with `?ssl=require`):
   `DATABASE_URL=postgresql+asyncpg://<owner>:<pw>@<host>/<db>` (migrations only) and
   `APP_DATABASE_URL=postgresql+asyncpg://daelys_app:<DAELYS_APP_PASSWORD>@<host>/<db>` (everything else).
4. **Render:** New → Blueprint → this repo (`render.yaml`). Fill every `sync: false` value, and copy the same values
   into the `daelys-shared` env group for the worker. The api's pre-deploy command runs `alembic upgrade head`.
5. Seed or create the first owner (`python -m core.seed` for the demo; for a real team, insert the owner via a
   one-off job, then use the app).
6. Check `/health` → `{"status":"ok"}`.
7. **Point-in-time restore:** Neon console → your project → Branches → *Restore* (choose a timestamp). Restore into
   a new branch first, point a staging deploy at it, verify, then switch.

## 2. GitHub App: permissions and why

| Permission | Why |
| --- | --- |
| Issues: read & write | read issues for work items; write only to post approved comments |
| Pull requests: read & write | read PRs, links and status; post approved `comment_on_pr` |
| Checks: read | CI status for the `ci_failing_near_due` rule |
| Metadata: read | required by GitHub |

Webhook events: `pull_request`, `issues`, `issue_comment`, `check_run`. Webhook URL:
`https://<api>/webhooks/github`, secret = `GITHUB_WEBHOOK_SECRET`. Install the app on the team's repos and link the
installation id to the workspace (integrations table, or `SEED_GITHUB_INSTALLATION_ID` for the demo).

## 3. First backfill

As a lead: `POST /connectors/github/backfill`. It pulls issues and PRs (resuming from a cursor), the worker projects
them and evaluates risk. Then accept a plan from the team's brief so tasks have lanes, owners and due dates.

## 4. Speaker mapping (meetings)

Upload transcripts with `Name: text` lines. Daelys maps a speaker to a member by an explicit mapping first, else by a
unique first-name match. Unmapped speakers appear on the meeting page; a lead maps them once (`PUT /speakers`).
Audio recordings are transcribed by Notiv without speakers; prefer labelled transcripts when owners matter.

## 5. Approval tiers

| Tier | Actions | Approval |
| --- | --- | --- |
| 2 workspace | reassign, move_due, split, create_item | owner or lead of the item's project; can be undone |
| 3 external | notify (email), comment_on_pr | owner or project lead; never auto-approved; not undoable |

Owners make leads project leads (`POST /projects/{id}/leads`). `policies.auto_approve_max_tier` can allow tier-2
auto-approval (default 0 = off); tier 3 can never be auto-approved. Proposals expire after 48 h.

## 6. Verify isolation before go-live

Run `docs/process/verification/SPRINT_1_CHECKS.md` checks 1–6 against the deployed database and API: app role is not superuser
and has no BYPASSRLS; every table has RLS; another workspace's ids return 404; no tenant set returns zero rows. Then
`make bench SUITE=safety` against staging.
