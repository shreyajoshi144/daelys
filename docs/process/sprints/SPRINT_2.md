# Sprint 2 — Plan from brief, DaelysBench harness, product shell

Read `CLAUDE.md` first. Sprint 1 must be green before starting. This sprint adds the first AI capability and
the evaluation harness every later sprint plugs into, plus the frontend shell.

## Goal

A lead pastes a messy brief and gets a validated plan: work items across lanes, dependencies, each item linked to
the brief line it came from, and open questions for anything contradictory. The quality of this is measured.

## Build

1. **`core/llm.py`:** provider abstraction with ordered fallback on timeout/429/5xx, structured output via
   Pydantic, logs every call to `agent_runs` (model, prompt_version, tokens, latency_ms, cost estimate).
   Fallback is never silent: record which provider served the call.
2. **Plan extraction** (`agents/planner.py`, prompt `agents/prompts/plan_v1.md`):
   output schema `Plan{items[WorkItemDraft{title, lane, owner_role, estimate_days, depends_on[local_ids],
   source_span{start,end}}], open_questions[{text, source_spans}]}`.
3. **Validators:** reject cycles (`graphlib.TopologicalSorter`), drop items whose `source_span` doesn't map to
   real brief text, unknown dependency refs become open questions. One automatic repair retry, then return what
   is valid plus a list of what was dropped.
4. **Brief ingestion:** upload markdown/PDF → `artifacts` + chunked, embedded `chunks` (keep char offsets).
5. **API:** `POST /projects/{id}/plan` (lead+), returns a draft plan; `POST /projects/{id}/plan/accept` writes
   events (`plan.item_created`, `plan.edge_created`) so work items come from the event log, per rule 1.
6. **DaelysBench harness** in `evaluation/`: dataset loader, runner, metric functions, results writer to
   `evaluation/results/latest.md` and a JSON file per run. `make bench SUITE=plan`.
   Plan suite metrics: dependency precision/recall (match items by title similarity + lane), cycle rate,
   uncited-item rate, planted-contradiction caught. Support comparing two model configs in one run.
   Create 3 example briefs with gold plans as templates; mark the rest of the 15 as "Needs Shreya".
7. **Frontend shell (`web/`):** React in plain JavaScript with `htm`, no build step, served by FastAPI (no TypeScript, Next.js or Vite). Login, project page with three lanes (Product, Engineering,
   Marketing), brief paste/upload, plan review screen: items, dependency list, open-questions panel, and hovering an
   item highlights its source line in the brief. Plain, clean, typographic UI. No dashboards full of cards.

## Acceptance

- `make test` green, including unit tests for validators (cycle, bad span, unknown dep).
- `make bench SUITE=plan` runs on the 3 example briefs with two configs and writes a results table.
- On the Tiffin Box brief, the date contradiction appears as an open question (show output).
- Accepting a plan creates events, and `work_items` are rebuilt from them (test).
- Frontend: plan review works end to end against the local API.

## Out of scope

Rules, risk, proposals, meetings.

## Needs Shreya

LLM API keys; write the remaining 12 briefs and gold plans; review the planner prompt in her own words.
