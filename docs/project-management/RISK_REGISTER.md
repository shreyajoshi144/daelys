# Risk register

Likelihood (L) and impact (I) are rated 1 (low) to 3 (high); score = L × I. Ratings are the residual risk after the
mitigation in place at the end of the build phase (2026-11-25). The owner of every risk is the project owner, Shreya
Joshi.

| Status | Meaning |
| --- | --- |
| **Mitigated** | Controls are built and tested; the risk is watched, not active |
| **Open** | Not yet controlled; has an action in the backlog |
| **Closed** | No longer applies |

## 1. Product and AI risks

| ID | Risk | L | I | Score | Mitigation in place | Evidence | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| R-01 | The model reports a risk the data doesn't support | 1 | 3 | 3 | Risk is scored by rules only; a model may explain but never set a score | No model call in `rules/` (invariant check) | Mitigated |
| R-02 | A generated sentence states something not in the evidence | 2 | 3 | 6 | The citation checker drops any sentence without a valid citation; Ask says "Not enough evidence" | `tests/test_citations.py`; faithfulness on a live model pending (PB-01) | Open |
| R-03 | Prompt injection in a PR, brief or transcript triggers an action | 1 | 3 | 3 | Untrusted text only inside evidence blocks; typed actions only; human approval; safety suite gates CI | Safety suite 11/11; real-model case pending (PB-01) | Mitigated |
| R-04 | An action runs without valid approval, twice, or after the approver lost the role | 1 | 3 | 3 | Single executor; approval, expiry and `min(agent, approver)` checked at run time; idempotency keys | `tests/test_executor.py`; safety suite | Mitigated |
| R-05 | One workspace reads another's data | 1 | 3 | 3 | Row-level security on every table; restricted app role; unset tenant returns zero rows | `tests/test_isolation.py` | Mitigated |
| R-06 | Meeting extraction is worse for Hinglish than English | 2 | 2 | 4 | The meetings suite reports the two languages separately | Not measured yet (PB-01) | Open |
| R-07 | Draft labels make the bench look better than reality | 3 | 2 | 6 | Every dataset is marked draft and every doc says so | Independent labels pending (PB-02) | Open |
| R-08 | Model cost grows without anyone noticing | 2 | 2 | 4 | Every call logged with tokens and cost; per-workspace rate limits on model endpoints | Ops suite pending (PB-01) | Open |
| R-09 | Webhooks arrive late, twice or out of order and corrupt the board | 1 | 2 | 2 | Idempotent appends; per-field last-writer-wins projection | `tests/test_projector.py`, `tests/test_github_webhook.py` | Mitigated |

## 2. Delivery risks

| ID | Risk | L | I | Score | Mitigation | Status |
| --- | --- | --- | --- | --- | --- | --- |
| R-10 | No model API key, so model quality can't be measured | 3 | 2 | 6 | Scripted models in tests; bench reports `n/a` instead of guessing; tracked as PB-01 | Open |
| R-11 | No hosting accounts, so the product isn't deployed | 3 | 2 | 6 | Production image verified locally; step-by-step [Deployment](../DEPLOYMENT.md) guide; PB-03 | Open |
| R-12 | No GitHub App, so the real integration is unproven | 3 | 2 | 6 | Signed payloads and a local GitHub stand-in in tests; PB-05 | Open |
| R-13 | CI has never run | 2 | 2 | 4 | The same checks run locally (`make test`, `make lint`); PB-04 | Open |
| R-14 | A single builder is a single point of failure | 2 | 2 | 4 | Specs, decisions, runbook and onboarding are written down | Mitigated |
| R-15 | AI-assisted code contains mistakes nobody reviewed | 2 | 3 | 6 | Written specs, verification gates and a report per sprint; invariant checks; tests against real services; a full pre-push audit (M6) | Mitigated |
| R-16 | A third-party image without a clear licence ships in the product | — | — | — | Replaced with an AI-generated image on 2026-11-24 | Closed |
| R-17 | Port clashes with other local projects stop services from starting | 2 | 1 | 2 | Every host port is configurable in `.env`; the [runbook](../RUNBOOK.md) has the fix | Mitigated |

## 3. Risks that happened (issues)

| ID | What happened | Impact | Response | Linked risk |
| --- | --- | --- | --- | --- |
| I-01 | The container image couldn't be rebuilt in Sprint 2 (package index timeouts) | Container tests delayed one sprint | Pip cache mount and longer timeouts in Sprint 3 | R-13 |
| I-02 | A meeting dated "today" moved a March scenario's clock to November: every task showed a 239-day slip | Demo unusable | The event clock follows observed work only; regression test | R-09 |
| I-03 | The first redesign used a reference image whose licence was unclear | Couldn't publish the UI | Replaced with an AI-generated image | R-16 |
| I-04 | Another project's mail server held ports 1025 and 8025, so the email test failed on a fresh copy | One failing test | Mailpit host ports moved in `.env`; runbook updated | R-17 |

## 4. Heat map (open and mitigated risks)

| Likelihood ↓ / Impact → | 1 | 2 | 3 |
| --- | --- | --- | --- |
| **3** | | R-07, R-10, R-11, R-12 | |
| **2** | R-17 | R-06, R-08, R-13, R-14 | R-02, R-15 |
| **1** | | R-09 | R-01, R-03, R-04, R-05 |

The highest open risks are all about **evidence the product works in the real world**, not about the code: live
model quality, independent labels, deployment and the GitHub integration. Each one is a blocked backlog item with a
named dependency ([Roadmap](ROADMAP.md#next-validate-milestones-m8-and-m9)).
