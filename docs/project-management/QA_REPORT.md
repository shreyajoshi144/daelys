# Testing and QA report

| | |
| --- | --- |
| **Scope** | Build phase, milestones M1 to M7 |
| **Report date** | 2026-11-25 |
| **Test run** | `make test` and `make cov` in Docker against real Postgres 16, Redis and Mailpit |
| **Recommendation** | Ready for a measured, deployed validation phase (M8, M9). **Not yet ready for a pilot:** model quality is unmeasured and the product isn't deployed (section 7) |

## 1. Summary

| Check | Result | Date |
| --- | --- | --- |
| Automated tests | **159 tests: 158 passed, 1 skipped.** The skip needs macOS speech tools and the Notiv repository | 2026-11-25 |
| Line coverage | **83%** | 2026-11-25 |
| Lint and types | `ruff check`, `ruff format --check` and `mypy` (92 source files) clean | 2026-11-25 |
| Dependency audit | `pip-audit --strict`: no known vulnerabilities | 2026-11-19 |
| Risk suite (3 draft scenarios) | Precision **1.00**, recall **1.00**, **2.64 days** before the due date, detection delay **0**, evidence cites cause **0.92** | 2026-11-19 |
| Safety suite | **11/11 pass**; the 12th case needs a live model | 2026-11-19 |
| Duplicates (lexical stage) | Precision **0.57**, recall **1.00** | 2026-11-19 |
| Plan, meetings, faithfulness, ops suites | **Not run:** need a model API key | — |
| Production image | 482 MB; boots; `/health` ok | 2026-11-09 |
| Defects open | **0 known functional defects.** 7 technical-debt items and 2 accessibility gaps are tracked in the backlog | 2026-11-25 |

## 2. Test strategy

| Level | What it covers | How |
| --- | --- | --- |
| Unit | Rules, scoring, validators, citation checker, bench scoring maths | Pure functions, no I/O |
| Integration | API, RLS, projector, executor, connectors, meetings, retrieval | Real Postgres (`<db>_test`), Redis and Mailpit in Docker. Never SQLite, never a mocked database |
| External boundaries | Models, GitHub, Notiv | Scripted models and a local GitHub stand-in at the network boundary, labelled as such in the tests |
| Adversarial | Prompt injection, unapproved, expired, cross-tenant and concurrent execution | Safety suite (`make bench SUITE=safety`); gates CI; a test proves it can fail |
| Quality evaluation | Risk, plans, meetings, duplicates, faithfulness, cost | DaelysBench, seven suites ([Evaluation](../EVALUATION.md)) |
| Invariants | No model call in `rules/`, no writes from `agents/`, only the executor writes actions | Checked in every sprint's verification gate |
| Manual | Full journeys in a real browser; break-it checks per sprint | [`docs/process/verification/`](../process/verification/) |

**Entry criteria for a sprint's sign-off:** the sprint's code complete; services up; migrations applied.
**Exit criteria:** every acceptance criterion passes or is listed as a gap with its blocker; tests, lint and types
clean; the safety suite passes; the sprint report records the real output.

## 3. Requirements coverage

Every functional requirement in the [PRD](PRD.md#5-functional-requirements) maps to tests or a manual check.

| Requirements | Verified by | Result |
| --- | --- | --- |
| FR-01 to FR-03 (access, isolation) | `test_auth.py` (10), `test_isolation.py` (8) | Pass |
| FR-04 to FR-08 (events, ingestion, projection) | `test_projector.py` (8), `test_replay.py` (6), `test_github_webhook.py` (11), `test_github_backfill.py` (3) | Pass; GitHub against a stand-in |
| FR-09 (`.ics`) | `test_search_and_insights.py` | Pass |
| FR-10 to FR-12 (planning) | `test_planner.py` (3), `test_plan_validators.py` (7), `test_projects_api.py` (7) | Pass with a scripted model |
| FR-13 to FR-19 (risk) | `test_rules.py` (12), `test_risk_api.py` (8), `test_citations.py` (4); risk suite | Pass |
| FR-20 to FR-25 (proposals and actions) | `test_proposer.py` (8), `test_executor.py` (11), `test_external_actions.py` (4); safety suite | Pass; GitHub against a stand-in |
| FR-26 to FR-31 (meetings and insights) | `test_meetings.py` (6), `test_search_and_insights.py` (10) | Pass with scripted models |
| FR-32 (bench) | `test_bench.py` (11) | Pass |
| FR-33, FR-34, FR-37 (image, demo, web journey) | Production image boot; browser checks of every screen | Pass |
| FR-35, FR-36 (deployed URL, fresh clone) | — | **Not tested:** need accounts and a repository |
| NFR-01, NFR-04, NFR-05 (security, reliability, observability) | `test_health.py` (4), `test_llm.py` (8), isolation, executor and realtime tests | Pass |
| NFR-07 (accessibility) | Measured contrast ratios | Partly: two text colours below AA |

Numbers in brackets are test functions; with parametrised cases they make the 159 tests.

## 4. Test areas in detail

| Area | What it proves |
| --- | --- |
| Isolation | The app role isn't a superuser and can't bypass RLS; every table has RLS; another workspace's IDs return 404; no tenant set returns zero rows; no cross-tenant writes; events are append-only |
| Auth | Tenant-scoped login; the agent can't log in; bad tokens are 401; viewers get 403 on lead endpoints; role comes from the database, not the token; rate limits; password hashing |
| Projection and replay | Order independence; idempotency; rebuilding from the log gives the same rows; a killed replay resumes without duplicates; reverse-order replay matches forward |
| GitHub | Signature checks; normalisation; duplicate and out-of-order delivery; resumable backfill |
| Planning | Validation and repair; quotes located in the brief; accepting writes events only; CSRF on cookie writes; strict CSP |
| Risk | Each rule; propagation including a diamond; scoring; health roll-up; explanations need a key and are cached; the time machine never persists; live updates survive a lost listener |
| Proposals and execution | Typed actions only; read-only tools; injection rejected and audited; approval, expiry, demotion; exactly once under four concurrent runs; undo; tier-3 crash recovery without re-sending; one email to Mailpit |
| Meetings and insights | Transcript parsing; uncited extractions dropped; matching or `create_item`; role filter before ranking; Ask never leaks a restricted document; summaries cited and versioned; duplicates suggested, never merged; demo reset owner-only |
| Health and limits | Failed dependencies reported; security headers; request IDs; chunked uploads refused while streaming |

## 5. Test history

| Milestone | Tests | Coverage |
| --- | --- | --- |
| M1 | 53 passed | 89% |
| M2 | 88 passed | — |
| M3 | 112 passed (host and container) | — |
| M4 | 136 passed | 86% |
| M5 | 156 passed (host); 155 + 1 skipped (container) | — |
| M6 | 159 tests | — |
| M7 | 158 passed, 1 skipped | 83% |

"—" means coverage wasn't recorded for that milestone.

## 6. Defect log

Every defect below was found **outside** the passing test suite: by using the app in a browser, by running it
against real services, by auditing the deploy path, or on a fresh copy. All are fixed, and each has a regression test
or a guard.

| ID | Severity | Defect | Found by | Root cause | Fix | Milestone |
| --- | --- | --- | --- | --- | --- | --- |
| D-01 | High | Every task showed a 239-day slip in the demo | Browser walkthrough | A meeting dated "today" moved a March scenario's clock to November | The clock follows observed work only; regression test | M5 |
| D-02 | High | Blank screen going from the landing page to sign-in | Browser walkthrough | An effect returned a Promise, which React called as a cleanup | Effects use block bodies | M5 |
| D-03 | Medium | Sidebar health stayed unknown after quick navigation | Browser walkthrough | Two event streams per page hit the browser's six-connection limit | One shared stream per tab | M5 |
| D-04 | Medium | The weekly summary defaulted to a Sunday in India | Browser walkthrough | `toISOString()` shifted local midnight into the previous UTC day | Local date formatting; the server normalises to Monday UTC | M5 |
| D-05 | Medium | Executor actions made a scenario's clock jump, so everything looked overdue | Live check | Executor events used wall time | Executor uses the workspace clock | M4 |
| D-06 | High | Live updates stopped silently after a database restart | Audit | The single listener connection was gone while streams kept their keepalives | Losing the listener ends every stream so browsers reconnect; regression test | M6 |
| D-07 | High | A chunked upload could exhaust memory | Audit | Bodies were buffered before the size check | One streaming reader with a hard cap; test proves it stops early | M6 |
| D-08 | Medium | The running app required owner database credentials | Audit | Settings demanded the migration URL | Removed from runtime settings | M6 |
| D-09 | Medium | Every user on Render would share one login rate limit | Audit | uvicorn trusted forwarded addresses only from localhost | `FORWARDED_ALLOW_IPS` set for the load balancer | M6 |
| D-10 | Low | The production image would ship test tools and a 1.5 GB audio stack | Audit | Development-first Dockerfile defaults | Production defaults; Compose opts in locally | M6 |
| D-11 | Medium | The landing image could be refused by browsers | Browser check | `.webp` served as `application/octet-stream` with `nosniff` | Content type registered | M7 |
| D-12 | Low | The email test failed on a fresh copy | Fresh-copy run | Another local project held the mail server's ports | Ports moved in `.env`; runbook entry | M7 |

## 7. Not verified

| Area | Why not | Backlog |
| --- | --- | --- |
| Model quality: plans, explanations, meeting extraction (English and Hinglish), summaries, Ask, cost | No model API key | PB-01 |
| The real-model prompt-injection case | Same | PB-01 |
| Scores on independent labels | All datasets are drafts by the builder | PB-02 |
| Deployed URL, load, recovery, stranger test | Not deployed | PB-03 |
| CI, fresh clone, secret scan | No repository pushed | PB-04 |
| Real GitHub webhooks and PR comments | No GitHub App | PB-05 |
| Email through a real relay | Mailer has no TLS or auth yet | PB-10 |

## 8. How to reproduce

```bash
make up && make migrate && make seed
make test          # 159 tests
make cov           # with coverage
make lint          # ruff, format check, mypy (needs the dev requirements installed)
make bench         # DaelysBench; model suites report n/a without a key
```
