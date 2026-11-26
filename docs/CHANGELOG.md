# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Daelys has not had a tagged release;
sections are dated by milestone.

## [Unreleased] — 2026-11-24

### Added
- Project-management documentation in `docs/project-management/`: charter, PRD, roadmap, milestones, product
  backlog, risk register, QA report and closure report, all sharing one set of requirement, milestone, backlog, risk
  and defect IDs.
- Runbook entry for host-port clashes with other local projects.
- Documentation set: product overview, one-page case study, evaluation, results and testing, development history,
  API reference, configuration reference, runbook, design system, this changelog and a docs index.
- A `--watch` colour token used by health dots, board stripes and graph nodes.

### Changed
- Redesigned the journey from landing page to workspace around one AI-generated collage
  (`web/img/collage.webp`) and one cobalt blue. Warm stone paper replaced the sage palette; "for review" is lilac,
  "approved" is leaf green.
- The case study is now a one-page product case study. Its engineering detail moved to Architecture and Results and
  testing.
- `docs/SPRINT_STATUS.md` and the development history were folded into Milestones; results and testing became the
  QA report.

### Fixed
- `.webp` files are served as `image/webp`; with `nosniff`, browsers could refuse them before.
- The hero photo card no longer shows content from the source image beyond its crop.

### Removed
- Dark mode. Every screen now uses the light stone paper of the landing page (`color-scheme: only light`).
- An earlier third-party reference image whose licence was unclear, and every crop of it.
- The image slivers beside the principles band.

## Pre-push audit — 2026-11-19

### Security
- One streaming reader caps every upload while reading, so a chunked body can't exhaust memory.
- `Referrer-Policy` on every response; HSTS when `COOKIE_SECURE`; a client's `X-Request-ID` is kept only if it is a
  plain token of up to 64 characters.
- The running app no longer requires the owner database URL.

### Fixed
- Losing the `LISTEN` connection now ends open SSE streams after one refetch, so browsers reconnect and listening
  resumes.

### Changed
- Compose restarts infrastructure services; the Notiv mount defaults to an ignored `./.notiv`.
- Process documents moved under `docs/process/`; bench run logs are git-ignored.

## Sprint 5: meetings, summaries, search, ship — 2026-11-08/09

### Added
- Meetings from labelled transcripts and Notiv audio, with cited extraction, matching and `create_item` proposals.
- Weekly summaries, Ask Daelys, duplicate suggestions, comments, `.ics` upload, demo reset.
- Meetings, duplicates, faithfulness and ops bench suites.
- Production image, `render.yaml`, deployment and onboarding guides; the first full web UI.

### Fixed
- The event clock follows observed work only (a meeting dated "today" had produced a 239-day slip).
- Effects no longer return values React treats as cleanups (blank screen after sign-in navigation).
- One shared `EventSource` per tab (two streams per page hit the browser's connection limit).
- Weekly summary dates use local formatting; the server normalises to Monday UTC.

## Sprint 4: proposals, approvals, execution, audit — 2026-11-07

### Added
- LangGraph proposer with read-only tools and five typed actions.
- Approval tiers, project leads, expiry and `min(agent, approver)` permission.
- The executor: exactly once, audited, undo for tier 2, crash recovery for tier 3.
- Safety suite (11 cases) as a CI gate.

## Sprint 3: the risk loop — 2026-11-07

### Added
- Five SQL risk rules, slip propagation, the noisy-OR score and health roll-up.
- The why panel, explanations behind the citation checker, the time machine, live updates over SSE.
- Risk bench suite.

## Sprint 2: plan from brief — 2026-11-07

### Added
- LLM gateway with ordered fallback and `agent_runs` logging.
- Planner, validators and one repair retry; plan acceptance writes events.
- DaelysBench harness and plan suite; the web shell with cookie sessions, CSRF and a strict CSP.

## Sprint 1: foundation — 2026-11-04/05

### Added
- Schema with row-level security on every table and the restricted `daelys_app` role.
- Idempotent, ordered event appends and the order-independent projector.
- Scenario replay and GitHub connectors; the Tiffin Box scenario.
