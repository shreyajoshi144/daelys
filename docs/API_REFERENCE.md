# API reference

The API is served by FastAPI from the same origin as the web app. Interactive docs are at `/docs` when the server is
running. This page lists every route with who may call it.

## Conventions

| Topic | Rule |
| --- | --- |
| **Authentication** | The web app uses an httpOnly `daelys_session` cookie from `POST /auth/session`. Scripts can use `Authorization: Bearer <token>` from `POST /auth/login` |
| **CSRF** | Any write authenticated by the cookie must send `X-Daelys-CSRF: 1` |
| **Roles** | `owner` > `lead` > `member` > `viewer`. The role is read from `memberships` on every request; the token's claim isn't trusted. "Lead" below means owner or lead |
| **Project leads** | Deciding proposals and generating them also requires being an owner or a lead **of the item's project** |
| **Tenancy** | Every request runs in a transaction with `app.tenant_id` set. Another workspace's IDs return 404 |
| **Not configured** | A feature whose service isn't configured (model, embeddings, GitHub, SMTP, Notiv) answers `503 {"error": "not_configured", "detail": …}` |
| **Rate limits** | Login: 10 per minute per client IP and per account (429). Model endpoints (marked ⚡): 20 per minute per workspace |
| **Body caps** | Briefs and `.ics` 2 MB; webhooks 5 MB; audio 50 MB. Enforced while streaming |
| **Request IDs** | Every response carries `x-request-id` |

## Routes

### Health and auth

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| GET | `/health` | public | Database and Redis status |
| POST | `/auth/login` | public | Email + password + workspace → bearer token |
| POST | `/auth/session` | public | The same, as a session cookie for the web app |
| DELETE | `/auth/session` | public | Sign out (clears the cookie) |
| GET | `/auth/me` | any member | Name, role, workspace, whether it's a demo, the workspace clock |
| GET | `/members` | any member | People in the workspace |

### Events and work items

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| GET | `/events?since=` | any member | The event log, optionally from a time |
| GET | `/events/{event_id}` | any member | One event (evidence) |
| GET | `/work-items` | any member | Tasks with risk |
| GET | `/work-items/{item_id}` | any member | One task |
| GET | `/work-items/{item_id}/comments` | any member | Comments on a task |
| POST | `/work-items/{item_id}/comments` | member+ | Add a comment |

### Integrations

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| POST | `/webhooks/github` | GitHub signature | GitHub App webhooks (`pull_request`, `issues`, `issue_comment`, `check_run`). 503 while no secret is set |
| POST | `/connectors/github/backfill` | lead+ | Pull issues and PRs from the linked installation, resuming from a cursor |
| POST | `/calendar/ics` | lead+ | Upload a read-only `.ics` calendar |

### Projects, briefs and plans

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| GET | `/projects` | any member | Projects |
| POST | `/projects` | lead+ | Create a project |
| GET | `/projects/{project_id}` | any member | One project |
| POST | `/projects/{project_id}/leads` | owner | Make someone a project lead |
| GET | `/projects/{project_id}/briefs` | any member | Briefs |
| POST | `/projects/{project_id}/briefs` | lead+ | Paste a brief |
| POST | `/projects/{project_id}/briefs/upload` | lead+ | Upload a brief (text or PDF) |
| GET | `/projects/{project_id}/briefs/{artifact_id}` | any member | One brief |
| POST | `/projects/{project_id}/plan` ⚡ | lead+ | Generate a plan draft from a brief (`artifact_id`) |
| GET | `/projects/{project_id}/plan/drafts` | any member | Plan drafts |
| GET | `/projects/{project_id}/plan/drafts/{draft_id}` | any member | One draft with source spans |
| POST | `/projects/{project_id}/plan/accept` | lead+ | Accept a draft: writes `plan.*` events |
| GET | `/projects/{project_id}/graph` | any member | Dependency graph |

### Risk and time machine

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| GET | `/risk/health` | any member | Workspace and per-project health |
| GET | `/work-items/{item_id}/signals` | any member | A task's signals with evidence |
| POST | `/signals/{signal_id}/explain` ⚡ | any member | Plain-language explanation with checked citations (cached) |
| GET | `/time-machine/state?as_of=` | any member | The board as it was at a moment; never persisted |
| POST | `/time-machine/replays` | owner | Replay a scenario into a fresh copy of the workspace |
| GET | `/stream` | any member | Server-sent events for live updates |

### Proposals and audit

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| POST | `/work-items/{item_id}/proposals/generate` ⚡ | lead+ and project lead | Ask the agent for fixes |
| GET | `/proposals?status=&work_item_id=` | any member | The inbox (up to 100 at a time) |
| GET | `/proposals/{proposal_id}` | any member | One proposal with its preview |
| POST | `/proposals/{proposal_id}/approve` | lead+ and project lead | Approve, optionally with edited `args`; queues execution |
| POST | `/proposals/{proposal_id}/reject` | lead+ and project lead | Reject with a reason |
| POST | `/proposals/{proposal_id}/undo` | lead+ and project lead | Undo an executed tier-2 action |
| GET | `/audit` | lead+ | The audit log |

### Meetings

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| POST | `/meetings/transcript` | member+ | Upload a labelled transcript |
| POST | `/meetings/audio` | member+ | Upload audio for Notiv transcription (503 without `NOTIV_PATH`) |
| GET | `/meetings` | any member | Meetings |
| GET | `/meetings/{meeting_id}` | any member | One meeting; the transcript itself needs member+ |
| POST | `/meetings/{meeting_id}/extract` | lead+ | Retry extraction |
| GET | `/speakers` | any member | Speakers and their mapping |
| PUT | `/speakers` | lead+ | Map a speaker to a member |

### Summaries, search and duplicates

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| POST | `/summaries` ⚡ | lead+ | Write a weekly summary for a lane |
| GET | `/summaries` | any member | Summaries |
| POST | `/ask` ⚡ | any member | Ask Daelys; answers only from documents your role may see |
| GET | `/duplicates` | any member | Duplicate suggestions |
| POST | `/duplicates/{suggestion_id}/{decision}` | lead+ | `confirm` or `dismiss` |

### Demo

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| POST | `/admin/reset-scenario` | owner, demo workspaces only | Reset to the end of week 1 (403 in a real workspace) |

## Typed actions

Proposals carry exactly one of these. Anything else is rejected before it's stored.

| Action | Tier | Effect |
| --- | --- | --- |
| `reassign` | 2 | Change a task's owner |
| `move_due` | 2 | Change a task's due date |
| `split` | 2 | Split a task into 2–5 parts with their own estimates |
| `create_item` | 2 | Create a task heard in a meeting |
| `notify` | 3 | Email up to 10 members (subject and body) |
| `comment_on_pr` | 3 | Comment on a GitHub PR through the App |
