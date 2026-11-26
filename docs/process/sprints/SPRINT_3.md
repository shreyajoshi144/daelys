# Sprint 3 — The risk loop

Read `CLAUDE.md` first. Sprints 1–2 must be green. This sprint builds the heart of the demo: Daelys notices a launch
slipping and explains why with evidence.

## Goal

Replaying week 2 of Tiffin Box flips launch health to At risk, and clicking "why" shows a short explanation where
every sentence links to the PR, CI run or event that caused it.

## Build

1. **Rules** (`rules/`, pure functions over the DB, no LLM), each writing to `signals` with `evidence_event_ids`:
   - `stale_pr`: linked PR has no activity for N days while the item is due within M days
   - `ci_failing_near_due`: failing checks on a linked PR within M days of due
   - `overdue_upstream`: a `depends_on` predecessor is past due or blocked
   - `explicit_blocker`: a `blocker.mentioned` event (meetings will emit these in sprint 5; replay can emit them now)
   - `owner_overloaded`: owner has more than K active items due this week
   Thresholds per tenant in a `policies` table with sane defaults.
2. **Slip propagation:** one recursive CTE over `edges` (`kind='depends_on'`) that computes downstream impact and
   a projected slip in days. Keep it in `rules/propagation.sql` with a comment explaining it.
3. **Risk score:** deterministic combination of signal severities + propagation, stored on `work_items.risk_score`
   and rolled up to project health (On track / Watch / At risk). Document the formula in `TECHNICAL_DECISIONS.md`.
4. **Rules worker:** runs after each projection batch; idempotent.
5. **Explainer** (`agents/explainer.py`, prompt `explain_v1.md`): input = one signal + its evidence events as a
   numbered evidence block; output = 2–3 sentences, each with `[evt:ID]` markers.
   **Citation checker** (`agents/citations.py`): drops sentences without markers or with IDs not in the evidence set.
6. **Replay "time machine":** API to replay a scenario into a fresh copy of the demo tenant and to view state
   "as of" a timestamp.
7. **Frontend:** launch health card, item risk badges, dependency graph view (simple SVG or React Flow), and the
   "why" panel that renders citations as links to the evidence (PR, CI run, event detail). Live refresh via SSE.
8. **Risk suite:** `make bench SUITE=risk`. Format for labelled timelines: item X at risk on day N because of event
   types Y. Metrics: detection lead time (days), precision/recall of at-risk flags, explanation cites the planted
   cause. Build 3 labelled timelines as templates.

## Acceptance

- Unit tests for each rule and for the CTE on a hand-built graph (including a diamond dependency).
- Citation checker tests: uncited sentence dropped, foreign ID dropped, valid kept.
- Replaying week 2 flips health to At risk (integration test asserts it).
- `make bench SUITE=risk` produces lead time and precision/recall on the templates.

## Out of scope

Proposals and actions (next sprint). No LLM in scoring, ever.

## Needs Shreya

Write the remaining 7 labelled timelines; sanity-check thresholds against the story she wants to tell.
