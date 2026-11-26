# Technical decisions

## S1-01 Projection is order-independent (per-field last-writer-wins)
Each projected field stores a version `(occurred_at UTC, idempotency_key)` in `work_items.field_versions`; an event only
overwrites older versions. Replaying any permutation gives the same state (tested forward vs reverse), re-applying is a
no-op, and partial events (a CI result, a reassignment) compose. CI is per-check (`ci_checks.<name>`) rolled up to
`ci_status` (any failure → failure, else any pending → pending). Cost: link removal from PR bodies is not modelled
(edges are add-only).

## S1-02 Projector cursor is safe because appends are serialised per tenant
`append_events` takes `pg_advisory_xact_lock(hash(tenant))`, so within a tenant event ids commit in id order and
`id > cursor` never skips a late-committing row. Throughput per tenant is serialised; fine at this scale, revisit if a
single tenant needs parallel ingestion.

## S1-03 Poison events are skipped and logged, never wedge the projector
Each event applies in a savepoint. Data/validation errors (bad timestamp, check violation) roll back that event only,
log `projector.event_skipped` with the event id, and the cursor moves on. Connection errors are *not* caught: the batch
rolls back and arq retries.

## S1-04 RLS policy uses NULLIF(..., '')
The spec's `current_setting('app.tenant_id')::uuid` errors when unset and casts `''` (what a pooled connection reverts
to after `SET LOCAL`) to an error. `NULLIF(current_setting('app.tenant_id', true), '')::uuid` makes both cases return
zero rows. Tenant is set with `set_config('app.tenant_id', :id, true)`, the parameterised form of `SET LOCAL`.

## S1-05 No FORCE ROW LEVEL SECURITY; app role never owns tables
Tables are owned by the migration role. Forcing RLS would also block the three SECURITY DEFINER lookup functions,
which run as the owner. Verification check 3 holds because `daelys_app` owns nothing.

## S1-06 SQL via SQLAlchemy Core `text()` with bound params, no ORM models
The schema lives once, in the migration. Avoids model/migration drift and keeps RLS-relevant SQL visible. No query
string is built from runtime input (migration DDL uses module constants only).

## S1-07 Login is tenant-scoped
`POST /auth/login {tenant, email, password}`. Users belong to a tenant (`users.tenant_id`) with one membership row.
Unknown tenant/email still runs bcrypt (timing). Rate limit: Redis fixed window per IP and per account; fails open
(logged) if Redis is down so an outage does not lock everyone out.

## S1-08 Replay timelines use the normalized event schema
Timelines carry `{day, time, type, subject, payload.fields/links}` rather than raw GitHub payloads, so Shreya can edit
them by hand. Simulated clock: real sleep = gap / speed, capped by `--max-sleep` (default 2 s) so overnight gaps don't
stall demos. Occurred-at times are fixed (reproducible), not shifted to "now".

## S1-09 Write endpoints own their transaction
Read endpoints use the `get_context` dependency transaction. The webhook commits explicitly before returning 202, so
GitHub never gets a success for an event that failed to commit.

## Dependencies added beyond CLAUDE.md stack
pydantic-settings (env config), PyJWT[crypto] (JWT + GitHub App RS256), bcrypt, httpx (GitHub REST + tests),
redis (arq's client, used directly for rate limits), pip-audit/ruff/mypy/pytest-* (dev only).

## S2-01 Plain-JS React frontend, no build step
React 18 + `htm` (tagged-template JSX alternative), vendored UMD files in `web/vendor/` with sha256s, ES modules,
hash routing, one CSS file. FastAPI serves `web/` at `/`. No TypeScript, Node toolchain, bundler or CDN (chosen by
Shreya). Tailwind was dropped because it needs a build step. Strict CSP (`script-src 'self'`) on everything except
`/docs`.

## S2-02 Web auth via httpOnly cookie + CSRF header
`POST /auth/session` sets an httpOnly, SameSite=Strict cookie (`Secure` via `COOKIE_SECURE`); the page never sees
the JWT. Cookie-authenticated writes must send `X-Daelys-CSRF: 1`, which a cross-site form or fetch cannot add
without a CORS preflight the API never grants. Bearer tokens (`/auth/login`) remain for API clients.

## S2-03 LLM layer: official Anthropic SDK, ordered model fallback
`core/llm.py` uses `AsyncAnthropic.messages.parse` with Pydantic output (forced tool use is rejected by current
models). Routes are an ordered `provider:model` list (default Opus 5.5 then Sonnet 5.5); fallback only on
timeout/429/5xx/connection error/refusal, never on other 4xx. One `agent_runs` row per attempt; a call served by a
later route is stored as `fallback`. Only the Anthropic provider exists today: the `Provider` protocol is where a
second vendor plugs in. LangGraph is not used yet (the planner is one call plus one repair); it arrives with the
Sprint 4 proposer.

## S2-04 Citations are computed, not trusted
The model returns verbatim `source_quote`s; we locate them in the brief (whitespace-tolerant) and store char
offsets. An item or question whose quote isn't found is dropped. Model-produced offsets were rejected because models
miscount characters.

## S2-05 Plan validation and repair
Duplicate ids, self-dependencies, bad estimates and uncited items are dropped; unknown dependency refs become open
questions; cycles are broken one edge at a time (`graphlib`). Any problem triggers exactly one repair call with the
problem list; whatever is still invalid after that is dropped and listed.

## S2-06 Accepting a plan writes events only
`plan.item_created` / `plan.edge_created` (idempotent per draft) are appended; the projector builds `work_items`
(kind `task`, new columns `project_id`, `owner_role`, `estimate_days`, `source_span`) and `depends_on` edges
(src depends on dst). The accept endpoint runs the projector inline after commit so the lanes update immediately;
the worker sweep is the fallback.

## S2-07 Embeddings optional
Chunks keep char offsets. With `VOYAGE_API_KEY` they get 1024-dim `voyage-3.5` embeddings; without it they are
stored unembedded and the API says `embedded: false`. Planning doesn't depend on embeddings.

## Dependencies added in Sprint 2
anthropic (official SDK; brings httpx2), pypdf (PDF text extraction). Vendored, not installed: react@18.3.1,
react-dom@18.3.1, htm@3.1.1 (MIT).

## S3-01 Rules are SQL over the projection, evaluated at an explicit `as_of`
Five rules in `rules/checks.py`, each a query over `work_items`/`edges`/`events` at `as_of`, returning signal drafts
with evidence event ids. Evidence for "when was this due date set" comes from `field_versions` (it stores the
idempotency key of the event that last set each field). Thresholds live in `policies` (defaults: stale PR 3 days, due
window 5 days, overload > 3 items due in 7 days, blocker window 7 days). `policies.clock`: `wall` = now, `event` =
latest event time (replayed scenarios run on their own clock; the seed sets `event` for Tiffin Box).

## S3-02 Risk formula
- Severity points: low 15, medium 35, high 60, critical 85.
- Rule severities: stale PR medium (high if due within 2 days); CI failing near due high (critical within 1 day or
  overdue); upstream overdue high, upstream blocked medium; explicit blocker high; owner overloaded low (medium if
  more than limit + 2).
- Slip points from propagation: 0 if on time, else min(85, 35 + 10 × ceil(projected slip days)).
- Item score = 100 × (1 − Π(1 − pᵢ/100)) over its signal points and slip points (noisy-OR: reasons compound, one
  reason scores itself, never above 100).
- Item level: at_risk ≥ 60, watch ≥ 30, else ok. Project / launch health = worst level among active items
  (At risk / Watch / On track).

## S3-03 Slip propagation
`rules/propagation.sql`: recursive CTE from every overdue active item down `depends_on` edges. Each hop subtracts the
downstream item's slack (its latest start = due − estimate, minus the upstream due date); delay ≤ 0 stops; done items
are never delayed; `path[]` blocks cycles; diamonds keep the worst path. Edge direction: src depends on dst.

## S3-04 Derived risk columns on work_items
`risk_score`, `risk_level`, `projected_slip_days`, `risk_evaluated_at` are written by the engine, not by events. They
are a pure function of (events, rules, as_of) and are recomputed on every run, so they are derived data like the
projection, not facts that need an event (rule 1 covers facts). The engine never touches projected fields.

## S3-05 Time machine as a rolled-back rebuild
`GET /time-machine/state?as_of=` deletes the tenant's projection inside a transaction, folds events up to `as_of`,
runs the same rules, reads the result and rolls back. Same code as live evaluation, nothing persists, other
sessions never see it (MVCC). Cost O(events) per call; a concurrent projector for that tenant waits on row locks
until the rollback. `POST /time-machine/replays` (owner) copies the workspace (people, projects, briefs; no events)
into a new tenant and replays a scenario there via a worker job, so live updates show the flip.

## S3-06 Explanations are optional; facts are not
The "why" panel always shows the deterministic signal, its details and linked evidence events. The LLM narrative
(`explain_v1`) is on demand, cached per evidence set, and passes through the citation checker (sentences without
markers or with foreign ids are dropped). Without a key the panel still works; only the narrative is missing.

## S3-07 Live updates via Postgres NOTIFY
The engine NOTIFYs `daelys_updates` with only the tenant id (delivered on commit). Each API process holds one LISTEN
connection and fans out to per-tenant SSE queues; clients refetch through the RLS-scoped API, so no tenant data
crosses the channel.

## S3-08 Replay cursor stores a timeline digest
A replay cursor is an index into one exact timeline file; if the file changes, the replay restarts from 0
(idempotency keys make re-appends no-ops) instead of silently skipping events inserted before the old index.

## S4-01 Proposer: LangGraph orchestration, deterministic tool use, read-only by construction
`agents/proposer.py` is a LangGraph graph: gather → propose → validate. The four read-only tools (`get_item`,
`get_dependents`, `get_owner_load`, `search_events`, plus prior rejections) are called by the graph, not chosen by
the model, inside `readonly_tenant_tx` (`SET TRANSACTION READ ONLY` + RLS), and only through `fetch_all/fetch_one`.
The model's single structured call goes through `core/llm.py`. The model returns a flat schema with `action_type` as
free text, so an off-list action (e.g. `delete_project`) is visible, rejected and audited instead of being silently
coerced. Tier and reversibility are set by us, not by the model. LangGraph pulls in `langsmith`; it sends nothing
unless `LANGSMITH_TRACING` is enabled (it is not).

## S4-02 Typed actions, tiers, permission
`core/actions.py`: Pydantic args per action (extra fields forbidden), tiers (reassign/move_due/split = 2,
notify/comment_on_pr = 3), required rank per action. Ranks: viewer 0, member 1, lead 2, agent 2, owner 3.
Effective rank = min(agent, approver), checked again at execution with the approver's *current* membership.
Auto-approval (opt-in policy, default off) is capped at tier 2 by a DB CHECK and in code, and grants only the agent's
rank. Comments may not @mention anyone; recipients and assignees must be workspace members.

## S4-03 Project-level deciders
`project_members(role='lead')`: owners decide anything; a lead decides only for items in projects they lead. The
creating lead becomes a project lead; owners add more via `POST /projects/{id}/leads`. The seed makes all Tiffin Box
leads leads of "Subscriptions launch".

## S4-04 Exactly once
Approve: row lock + `pending → approved` (a concurrent second approve gets 409), and the execution job has a fixed id.
Tier 2: claim, events (idempotency `exec:<proposal>:<n>`), audit and `executed` in one transaction. Tier 3: claim
(`approved → executing`) and commit, then the external call, then events + audit + `executed`. A PR comment carries
`<!-- daelys:proposal:<id> -->` (shown in the preview) and the executor looks for it before posting. A run interrupted
mid-flight is settled by the sweep: comment found → executed, otherwise failed. It is never re-sent automatically.
NotConfigured integrations leave the proposal approved, to run once configured.

## S4-05 The executor acts on the tenant's clock
Executor events (and the proposer's "today" and date validation) use `tenant_now`: wall time, or the latest event
time for replayed scenarios. Using wall time made a scenario's clock jump months ahead and every item look overdue
(caught by the move_due/undo test).

## S4-06 Undo writes events, never deletes
Tier-2 executions store their inverse. Undo appends inverse events (reassign/move_due restore fields; split cancels
the children) and audits a human `undo.*` row. The `owner_id` projected field was added so reassignment works for
people without a GitHub login.

## S4-07 Safety suite as a CI gate
`make bench SUITE=safety` runs injection (incl. a model scripted to obey the attack), unapproved tier 2/3,
cross-tenant execution, expiry, demoted approver, double execution and tier-3 auto-approval cases; any failure exits
1, and CI runs it after the tests. A real-model injection case runs only with a key and is reported "not run"
otherwise, never counted as a pass.

## Dependencies added in Sprint 4
langgraph (required by the stack for agents; brings langchain-core, langsmith). Mailpit (docker image, dev/CI only).

## S5-01 Postgres over a graph database
The work graph is small per tenant (hundreds to thousands of items) and every query is tenant-scoped. One Postgres
gives RLS isolation, transactions spanning events + projection + audit, recursive CTEs for propagation, pgvector and
full-text search. A graph database would add a second store to secure and keep consistent for no gain at this scale.

## S5-02 Computed risk, typed actions, tiered approval (summary of S3/S4)
Scores come from rules (auditable, testable, no drift); the LLM only explains. Agents choose from typed actions so
every effect can be validated, previewed exactly, permission-checked and undone (tier 2). Tier 3 (outside the
workspace) always needs a human.

## S5-03 Meetings: Notiv as a library, extraction in Daelys, labelled speakers
Shreya's choice (2026-11-08). Notiv's `core/lecture/transcribe.py` is imported from `NOTIV_PATH` as package
`notiv_lecture` (no copy, no stub; its import chain needs OpenCV). Extraction lives in Daelys (`meeting_v1`) so
items cite transcript lines and pass the same validation as everything else. Speakers come from labelled
transcripts plus a per-tenant mapping; no diarization (would add torch/pyannote and an HF token).

## S5-04 `create_item`: a sixth action, requested by the Sprint 5 spec
Design rule 7 fixes five agent actions. The spec explicitly asks that an unmatched meeting action item become a
proposal through the approval flow, so `create_item` exists for meeting ingestion only: the proposer's vocabulary
is still the five actions (`ACTIONS`), and a `create_item` proposal has no work item until a human approves it.

## S5-05 Retrieval with an honest fallback
pgvector when an embedding provider is configured; otherwise Postgres full-text (documents, events) and trigram
similarity (item titles). The mode is returned with results and shown in the UI. Role filtering is a SQL WHERE on
`artifacts.min_role` before ranking, so restricted text never becomes a candidate or reaches a model.

## S5-06 .ics upload instead of calendar URLs
Read-only `.ics` upload. Fetching user-supplied URLs server-side invites SSRF; Google Calendar OAuth was not built.

## S5-07 Demo reset is the only log deletion
`daelys_reset_demo(tenant)` (SECURITY DEFINER) clears a tenant's log and derived data only if `tenants.is_demo`,
and only from inside that tenant's own transaction. Real workspaces stay append-only.

## S5-08 Event clock follows work, not typed-in dates
In `clock = event` workspaces (replays, the demo), "now" is the latest event from observed work. Meeting events
(`source = 'meeting'`) are excluded, because their date is typed by a person. Plan acceptance is stamped at the
workspace clock, as executor actions already were. Before this, one meeting dated "today" in the March demo moved
the clock to November and showed a 239-day slip on every task. `/auth/me` returns `clock_now`, so the UI defaults
dates to the workspace clock. Weekly summaries use Monday-to-Monday UTC weeks.

## S5-09 UI: one neutral system, landing + sign-in + sidebar workspace
> **Superseded by D-01** (2026-11-24): the colours and dark mode below were replaced. The parts about weight over
> hue, system fonts, no build step and invite-only sign-in still hold.

Neutral ink surfaces, one forest-green accent and yellow for "watch". "At risk" is solid ink (weight, not hue), so
severity reads without red or orange, in light and dark mode. System font stack, inline SVG icons, no new
dependencies and no build step. The public landing page (`#/`) shows only the shipped sample scenario, with no
invented customers or metrics. Sign-in is invite-only (signup is on the cut list).

## A-01 Pre-push audit hardening (2026-11-19)
- **Uploads.** One streaming reader (`api/bodies.py`) caps every upload while reading, because chunked bodies have
  no `Content-Length` to check first.
- **Realtime.** Losing the `LISTEN` connection ends open SSE streams after one refetch, so browsers reconnect
  instead of silently going stale.
- **Least privilege.** Runtime settings no longer require the owner `DATABASE_URL`.
- **Headers.** Responses send `Referrer-Policy` everywhere, and HSTS when `COOKIE_SECURE`. A client's
  `X-Request-ID` is kept only if it is a plain token of up to 64 characters.
- **Compose.** Infrastructure services restart with Docker. The Notiv mount defaults to an ignored `./.notiv`, so a
  fresh clone no longer creates a folder outside the repo.
- **Repository layout.** Process documents moved under `docs/process/`. Bench run logs (`run-*.json`) are
  git-ignored; the published numbers live in `latest*.md`.

## D-01 Redesign: one image and one blue, landing page to workspace (2026-11-24)
- **One image.** A collage the project owner generated with an AI image tool (`web/img/collage.webp`, 1733×907)
  is the only imagery in the product. It replaced an earlier third-party reference image whose
  licence was unclear.
  - Landing hero, sign-in and onboarding sit on it: our content panel covers the image's own centre panel exactly
    (x 24.9–73.2%, y 11–87.1%), so only the picture frame shows. Full-screen views compute the
    covered image's size in CSS to keep the alignment. Under 900px the image is hidden.
  - Everywhere else uses named crops of it (`Crop` in `web/js/ui.js`): the sidebar cover, project-card bands,
    empty states and the hero's photo card.
  - Before a public release, confirm the image tool's terms allow commercial use. The image's layout (stages
    around a central panel) follows the earlier reference, but that centre is always covered by our content.
- **Tokens** (`web/styles.css`):
  - colours: warm stone canvas (`#f2efe8`) and paper from the image, ink text, and one cobalt (`#1b5bb0`) for
    primary actions, the principles band and the call to action
  - review tags take the image's chips: lilac "for review", leaf green "approved"; lilac for the risk score tile
  - "At risk" stays solid ink, not red
  - type: the system grotesk (SF Pro Display) at weight 600 for display, system sans for UI, mono for labels
  - light only: no dark mode, so the workspace matches the landing page and sign-in (`color-scheme: only light`)
- **Shared components** (`web/js/ui.js`): dot loader, image-crop empty state, a confirm dialog (replaces
  `window.confirm`) and toasts, all mounted once at the root.
- **No new backend.** Signup and password reset don't exist (signup is on the cut list), so those screens say how
  access actually works instead of faking a flow. Onboarding is three client-side steps, stored per user in
  `localStorage` and always skippable.

## Dependencies added in Sprint 5
Optional extra `meetings`: faster-whisper, av, opencv-python-headless (Notiv's transcription import chain), plus
ffmpeg in the image (`INSTALL_MEETINGS`). Postgres extension pg_trgm. On macOS the OpenCV and PyAV wheels each
bundle FFmpeg and print a duplicate-class warning at import (same as in Notiv); Linux images are unaffected.
