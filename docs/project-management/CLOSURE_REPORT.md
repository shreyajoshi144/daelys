# Project closure report: build phase

| | |
| --- | --- |
| **Project** | Daelys |
| **Phase closed** | Build (milestones M1 to M7), 2026-11-04 to 2026-11-25 |
| **Prepared by** | Shreya Joshi, owner and project manager |
| **Outcome** | The full product loop is built and tested. Validation (live model, deployment, pilot) is open, blocked on external accounts and people |

## 1. Executive summary

Daelys set out to answer a launch lead's three daily questions (what is slipping, why, and what to do) without the
two failures that make AI tools untrustworthy: made-up claims and unreviewed actions. In three weeks and five
spec-driven sprints, the build phase delivered the whole loop:

> Brief → Plan → Signals → Risk → Explanation → Proposal → **Approval** → Action + Audit

Every functional requirement that doesn't depend on an external account is met. The test suite has 159 tests
(158 passed, 1 skipped), with 83% coverage. The risk suite flags every labelled slip, on average 2.64 days before
the due date. The safety suite passes 11 of 11 adversarial cases and gates CI.

What is **not** done is the evidence that it works in the real world:
- quality with a live model
- independent labels
- a deployed URL
- a real GitHub integration
- a pilot

Each is a blocked backlog item with a named dependency. This report closes the build phase and hands those items to
a validation phase.

## 2. Objectives versus outcomes

| ID | Objective ([Charter](PROJECT_CHARTER.md#2-objectives)) | Outcome | Met? |
| --- | --- | --- | --- |
| O1 | Detect at-risk work before it slips | Precision 1.00, recall 1.00, 2.64 days of warning on 3 draft scenarios | Yes, on draft data |
| O2 | Never show an unsupported claim | The citation checker drops uncited sentences by construction | Yes by design; live faithfulness unmeasured |
| O3 | Never act without a valid, current approval | Safety suite 11/11, gating CI | Yes |
| O4 | Keep every workspace's data isolated | RLS on every table; 8 isolation tests | Yes |
| O5 | Be ready for a pilot team | Not deployed, not measured live, labels are drafts | **No** |

## 3. Scope

| | Count | Detail |
| --- | --- | --- |
| Functional requirements met | 35 of 37 | Of these, 11 are tested with scripted models or local stand-ins and await live credentials ([PRD](PRD.md#5-functional-requirements)) |
| Not met | 2 | FR-35 deployed URL; FR-36 fresh-clone check |
| Non-functional requirements met | 7 of 8 | NFR-07 accessibility partly met (two text colours below AA) |
| Cut on purpose | 10 features | Gantt charts, custom fields, sprint velocity, WIP limits, chat, presence avatars, mobile, billing, signup funnel, Google Calendar OAuth |

## 4. Schedule

| Milestone | Completed | Exit criteria |
| --- | --- | --- |
| M1 Foundation and ingestion | 2026-11-05 | 4 of 6; the rest need a repository |
| M2 Plans, DaelysBench, web shell | 2026-11-07 | 3 of 5; the rest need a model key |
| M3 Risk loop | 2026-11-07 | 4 of 4 |
| M4 Proposals, approvals, execution, audit | 2026-11-07 | 2 of 3; the rest needs a GitHub App |
| M5 Meetings, summaries, search, ship-ready | 2026-11-09 | 2 of 4; the rest need accounts |
| M6 Pre-push audit | 2026-11-19 | All findings fixed |
| M7 Redesign and documentation | 2026-11-25 | Done |

Detail: [Milestones](MILESTONES.md).

## 5. Scope changes

| ID | Change | Reason | Impact |
| --- | --- | --- | --- |
| CR-01 | Added a sixth proposal type, `create_item`, for tasks heard in meetings | The Sprint 5 spec needed unmatched action items to become tasks through approval, never silently | Same approval, audit and undo as other tier-2 actions (S5-04) |
| CR-02 | Calendar via `.ics` upload instead of Google Calendar OAuth or a URL | No OAuth scope review; an upload avoids the server fetching user-typed addresses | Read-only calendar; OAuth stays cut (S5-06) |
| CR-03 | Added a pre-push audit milestone (M6) | Publishing the code needed a security and reliability review first | Five fixes, including two high-severity defects |
| CR-04 | Replaced the first redesign's reference image with an AI-generated one | The original's licence was unclear | No licensed third-party imagery in the product |
| CR-05 | Removed dark mode | The workspace should look like the landing page and sign-in | Light only; one set of colours to maintain |
| CR-06 | Added project-management documentation | To make scope, delivery and quality reviewable | This folder |

## 6. Quality summary

Full detail: [QA report](QA_REPORT.md).

| Measure | Result |
| --- | --- |
| Tests | 159: 158 passed, 1 skipped (needs macOS speech tools) |
| Coverage | 83% |
| Lint, types, dependency audit | Clean |
| Defects found outside the test suite | 12, all fixed with a regression test or guard |
| Open functional defects | 0 |
| Model-dependent bench suites | Not run (no key) |

## 7. Cost

No paid service was used in the build phase. Everything ran locally in Docker: Postgres, Redis and Mailpit. Model
calls were scripted in tests, because no API key was available. Model cost per summary and per replay is therefore
**unknown**; the ops suite will measure it once a key is set (PB-01).

## 8. Risks at closure

Of 17 registered risks: 8 mitigated, 8 open, 1 closed. All 8 open risks are about real-world evidence or delivery
dependencies, not code. Detail: [Risk register](RISK_REGISTER.md).

## 9. Lessons learned

| What happened | Lesson | What I'd repeat or change |
| --- | --- | --- |
| The cut list was written before any code, and held all the way through | Most of the important decisions were about what not to build | Repeat: write the cut list first |
| Rules for the model (no scores, citations only, no direct actions) were enforced in code, not prompts | Guarantees you can test beat instructions you hope are followed | Repeat |
| The bench was built in Sprint 2, before most features | Every later feature arrived with a way to measure it | Repeat |
| Reporting `n/a` without a key | Every published number stays defensible | Repeat |
| The worst bugs (a 239-day slip, a blank screen, silent loss of live updates) were found by using the app, not by the tests | Green tests aren't a working product | Change: walk through the app in a browser at the end of every sprint, not only the last |
| Mixing real time and scenario time caused the biggest demo bug | Write down which clock every component uses | Change: decide clocks in the design, not after a bug |
| External dependencies (key, App, hosting) were never obtained | They blocked the last exit criterion of four milestones | Change: secure accounts in week one, before they are on the critical path |
| Datasets were drafted by the builder | Scores on them can't prove much | Change: recruit a labeller at the start |

## 10. Handover

| Topic | Where |
| --- | --- |
| Run it locally | [README](../../README.md); `make up`, `make migrate`, `make seed` |
| Deploy it | [Deployment](../DEPLOYMENT.md), [Configuration](../CONFIGURATION.md) |
| Operate it | [Runbook](../RUNBOOK.md) |
| Set up a team | [Onboarding](../ONBOARDING.md) |
| How it works | [Architecture](../ARCHITECTURE.md), [Technical decisions](../TECHNICAL_DECISIONS.md) |
| What's left | [Product backlog](PRODUCT_BACKLOG.md#4-open-backlog), [Roadmap](ROADMAP.md) |

## 11. Open items for the validation phase

| Item | Backlog | Blocked on |
| --- | --- | --- |
| Live-model bench run | PB-01 | An Anthropic API key |
| Independent labels, full datasets | PB-02 | A second labeller; recorded meetings |
| Deployment and stranger test | PB-03 | Render and Neon accounts |
| Repository, CI, fresh-clone check | PB-04 | A GitHub repository |
| GitHub App end to end | PB-05 | A GitHub App |
| Two-week pilot | PB-06 | PB-03 |

## 12. Sign-off

The build phase is closed: everything that could be built and verified without external accounts is built and
verified, and everything that couldn't is recorded with its blocker. The validation phase starts when PB-01 to PB-05
are unblocked.

— Shreya Joshi, 2026-11-25
