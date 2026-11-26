# Sprint 3 report: the risk loop

> **Historical record**, written at the end of the sprint. Items listed here as gaps or "Needs Shreya" are tracked
> today in the [product backlog](../project-management/PRODUCT_BACKLOG.md); current status is in the
> [closure report](../project-management/CLOSURE_REPORT.md).

## What was built
- **Rules** (`rules/checks.py`): `stale_pr`, `ci_failing_near_due`, `overdue_upstream`, `explicit_blocker`,
  `owner_overloaded`, each SQL over the projection at an explicit `as_of`, writing `signals` with
  `evidence_event_ids`. Per-tenant thresholds and clock in `policies` (migration `0003_risk`).
- **Slip propagation** (`rules/propagation.sql`): one recursive CTE over `depends_on` edges with slack absorption,
  cycle guard, worst-path for diamonds, root causes and downstream impact.
- **Risk score + health** (`rules/scoring.py`, `rules/engine.py`): noisy-OR of severity and slip points into
  `work_items.risk_score`/`risk_level`; project and launch health (On track / Watch / At risk). Formula in
  `TECHNICAL_DECISIONS.md` S3-02.
- **Rules worker**: runs after every projection batch (arq `project` job), plus a 10-minute sweep because risk also
  changes with time. Idempotent: same `as_of` gives the same rows; cleared signals are resolved, not deleted.
- **Explainer + citation checker** (`agents/explainer.py`, `explain_v1`, `agents/citations.py`): numbered evidence
  block, 2–3 sentences with `[evt:ID]`; uncited sentences and foreign ids are dropped. Cached per evidence set.
- **Time machine**: `GET /time-machine/state?as_of=` (rebuild + evaluate inside a rolled-back transaction);
  `POST /time-machine/replays` (owner) copies the workspace and replays a scenario via a worker job.
- **APIs**: `/risk/health`, `/work-items/{id}/signals`, `/signals/{id}/explain`, `/events/{id}`,
  `/projects/{id}/graph`, `/stream` (SSE via Postgres LISTEN/NOTIFY).
- **Frontend** (plain-JS React): launch/project health cards, risk badges, "why" panel with evidence links and
  citation links, SVG dependency graph, event detail page, time-machine form, live refresh via SSE.
- **Risk suite** (`evaluation/risk_suite.py`, `make bench SUITE=risk`): labelled timelines ("item X at risk from
  day N because of event types Y"), evaluated at the end of every scenario day. 3 DRAFT templates: Tiffin Box
  (weeks 1–2), `risk_diamond`, `risk_quiet_pr`.
- Fixes found on the way: the replay cursor now stores a timeline digest (an edited timeline used to make a resumed
  replay skip inserted events); the Docker build uses a pip cache mount + longer timeouts (PyPI kept timing out
  inside Docker on this network); `.js` is always served as `text/javascript` (the slim image served
  `application/javascript`).

## Real output (2026-11-07)

Host: `pytest -q` → `112 passed in 28.08s`. Container (`make test` after `make up/migrate/seed`): `112 passed in 25.36s`.
New this sprint: rules + CTE + scoring + engine (11), citations + explainer (4), risk API / time machine / SSE /
graph (7), risk bench (2), plus existing suites updated.
Lint: ruff clean, ruff format clean, mypy "no issues found in 50 source files".
Coverage: total 85%; rules/checks.py 100%, rules/scoring.py 100%, rules/engine.py 92%, agents/citations.py 100%,
agents/explainer.py 97%, core/realtime.py 98%, api/routes/risk.py 73%, core/sandbox.py 62%.
Invariant greps: no LLM in `rules/`; no writes in `agents/`; only the known `tests/conftest.py` DROP/CREATE DATABASE.

`make bench SUITE=risk` (container, no API key):
```
| case          | precision | recall | lead_time_days | detection_delay_days | evidence_cites_cause | explanation_cites_cause |
| risk_diamond  | 1.00 | 1.00 | 1.75 | 0.00 | 0.75 | n/a |
| risk_quiet_pr | 1.00 | 1.00 | 1.75 | 0.00 | 1.00 | n/a |
| tiffin_box    | 1.00 | 1.00 | 4.42 | 0.00 | 1.00 | n/a |
| all           | 1.00 | 1.00 | 2.64 | 0.00 | 0.92 | n/a |
```
**Read these with care.** I wrote both the rules and these three labelled timelines, so perfect scores only show the
rules and my scenarios agree; they are not an independent accuracy number. The first run scored lower (precision
0.78): two template mistakes of mine, corrected after the run and stated here because changing labels after seeing
results needs to be visible:
- `risk_quiet_pr`: the "small fix" issues never closed, so they genuinely went overdue and were (correctly)
  flagged. I added closing events so the template tests what it was meant to (overload as a negative).
- `risk_diamond`: `launch` is genuinely at risk on day 5 (its prerequisite `web` is overdue by then); my label said
  day 6.
`evidence_cites_cause` 0.75 on the diamond: the overdue root item's risk comes from propagation alone, which has no
signal evidence. `explanation_cites_cause` needs `ANTHROPIC_API_KEY`.

Live UI check (containerized stack, port 8019): owner started a time-machine replay of weeks 1–2 at 600× into
`tiffin-box-replay-3e9a6d`; on the project page, with no reload (an in-page marker survived), health went
**On track → At risk**, the checkout item got its badge, and the why panel updated from 84 to 94/100 as the stale-PR
signal appeared. Evidence and event-detail links worked; "Explain" showed `ANTHROPIC_API_KEY is not set`; graph drew
8 nodes / 4 edges with 2 at-risk nodes. Console: only that expected 503.

## Acceptance
| Criterion | Status |
| --- | --- |
| Unit tests for each rule and the CTE incl. a diamond | ✅ `tests/test_rules.py` |
| Citation checker: uncited dropped, foreign id dropped, valid kept | ✅ `tests/test_citations.py` |
| Replaying week 2 flips health to At risk (integration test) | ✅ `test_replaying_week2_flips_health_to_at_risk` (week 1 On track → week 2 At risk) |
| `make bench SUITE=risk` gives lead time and precision/recall | ✅ table above (DRAFT labels; see caveat) |

## Known gaps
- LLM explanations not exercised against a real model (no key); covered with a scripted model in tests.
- Time machine as-of is O(events) per call and briefly row-locks the tenant's projection; fine at demo scale.
- Workspace copies keep the same passwords as the source and accumulate (no cleanup endpoint).
- GitHub items only belong to a project via scenario replay; live GitHub repos aren't mapped to projects yet.
- Policies have no API/UI (defaults or seed only).
- `core/sandbox.py` project/brief copy path and the `replay_scenario` worker job are verified live, not by unit tests.
- This dev database has leftover data from earlier manual checks (e.g. week 2 replayed into `tiffin-box` during the
  Sprint 1 crash test), so its launch health reads At risk; a fresh `make seed` workspace starts On track.

## Decisions
`TECHNICAL_DECISIONS.md` S3-01 … S3-08: SQL rules at explicit `as_of` with policy clock, the risk formula, slack-
absorbing propagation, derived risk columns, rolled-back time machine, optional explanations, NOTIFY→SSE, replay
cursor digest. No new dependencies (asyncpg was already in the stack).

## Needs Shreya
1. Write the remaining 7 labelled risk timelines yourself (independent of the rules) and review the 3 DRAFT ones.
2. Sanity-check thresholds and severities (`policies` defaults, S3-02) against the story you want to tell.
3. With `ANTHROPIC_API_KEY` set: click "Explain" on a few signals and run `make bench SUITE=risk` to get
   `explanation_cites_cause`.
4. Carry-over: Sprint 2 key-dependent checks; Sprint 1 GitHub App, git repo + CI, gitleaks, independent review.
