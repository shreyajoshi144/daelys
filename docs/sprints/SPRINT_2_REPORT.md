# Sprint 2 report: plan from brief, DaelysBench, product shell

> **Historical record**, written at the end of the sprint. Items listed here as gaps or "Needs Shreya" are tracked
> today in the [product backlog](../project-management/PRODUCT_BACKLOG.md); current status is in the
> [closure report](../project-management/CLOSURE_REPORT.md).

## What was built
- **LLM layer** (`core/llm.py`): official Anthropic SDK with Pydantic structured output; ordered `provider:model`
  fallback (default Opus 5.5, then Sonnet 5.5) on timeout/429/5xx/connection error/refusal. One `agent_runs` row per
  attempt (provider, model, prompt_version, tokens, latency, cost estimate, status `ok`/`fallback`/`error`).
- **Planner** (`agents/planner.py`, prompts `plan_v1`, `plan_repair_v1`): brief only inside an escaped
  `<evidence>` block in the user turn; one automatic repair call when validation finds problems.
- **Validators** (`agents/plan_validators.py`): quotes become char spans (else dropped), cycles broken via
  `graphlib`, unknown deps become open questions, duplicate ids, self-deps and bad estimates dropped.
- **Brief ingestion** (`core/briefs.py`): paste or upload markdown/text/PDF → `artifacts` + offset-exact chunks;
  Voyage embeddings when `VOYAGE_API_KEY` is set.
- **API** (`api/routes/projects.py`): projects; brief paste/upload/list/get; `POST /projects/{id}/plan` (lead+,
  rate-limited); drafts list/get; `POST /projects/{id}/plan/accept` → `plan.item_created`/`plan.edge_created`
  events → projector → `work_items` + `depends_on` edges. Web session login (`/auth/session`, `/auth/me`).
- **Migration 0002**: `projects`, `plan_drafts` (RLS), plan fields on `work_items`, `project_id` on `artifacts`.
- **DaelysBench** (`evaluation/`): loader, metrics (dep P/R, item P/R, cycle rate, uncited-item rate,
  contradiction caught, repair rate), runner comparing configs, `make bench SUITE=plan`, 3 DRAFT cases with gold.
- **Frontend** (`web/`): React 18 in plain JavaScript with `htm`, no build step, vendored (no CDN), served by
  FastAPI. Login, projects, project page with Product/Engineering/Marketing lanes, brief paste/upload, plan review
  (items by lane, dependency list, open questions, dropped list; hover highlights the source text in the brief).
- Stack docs changed per Shreya: no TypeScript, no Next.js/Vite/Node toolchain (`CLAUDE.md`, `docs/process/sprints/SPRINT_2.md`).

## Real output

`make test` (host, real Postgres + Redis), 2026-11-07:
```
88 passed in ~21s
```
New this sprint: validators (7), LLM layer (12), planner (3), projects API (7), bench (6).
`make lint`: ruff check clean, ruff format clean, mypy "no issues found in 41 source files".
`make cov`: 87% total (before the last API test). agents/ 88–100%, core/llm.py 96%, evaluation/plan_suite.py 100%,
core/briefs.py 77% (Voyage path untested), api/routes/projects.py 71%.
pip-audit on the new `requirements.lock`: No known vulnerabilities found.
Invariant greps: only `tests/conftest.py` `DROP/CREATE DATABASE` (identifier from `DATABASE_URL`, as in Sprint 1).

UI, checked by hand in the browser against the local API and DB (host uvicorn, port 8029):
- signed in through the cookie session; projects list; project page with lanes and the seeded Tiffin brief
- "Generate plan" shows `This needs server configuration first: ANTHROPIC_API_KEY is not set`
- plan review on a **hand-written fixture draft** (not model output; saved through the real validator by a
  scratch script): hovering an item highlighted its brief line; hovering the open question highlighted both
  `Friday 20 March` and `Public launch: **27 March**`
- Accept → lanes showed the 4 items from the projector; draft marked accepted
- console: only the expected 401 (before login) and 503 (no key); no script or CSP errors

**`make bench SUITE=plan`: not run with a model.** No `ANTHROPIC_API_KEY` on this machine; the runner exits 2 before
any call (tested). There are no bench numbers and no Tiffin plan from a real model yet.

## Acceptance status
| Criterion | Status |
| --- | --- |
| `make test` green incl. validator tests | ✅ 88 passed (host) |
| bench on 3 briefs, two configs, results table | ⚠️ harness, metrics and table writer built and tested; real run needs a key |
| Tiffin contradiction appears as open question | ⚠️ needs a key; the fixture shows the UI path, not model behaviour |
| Accepting a plan creates events and rebuilds work_items | ✅ tested, including drop-projection-and-replay equality |
| Frontend plan review end to end vs local API | ✅ except the model call itself (fixture draft) |

## Known gaps
- The container image could not be rebuilt during Sprint 2 (PyPI read timeouts inside Docker). Fixed in Sprint 3
  (pip cache mount + longer timeouts); the container suite passes there.
- Only the Anthropic provider is implemented; the `Provider` protocol is the place for a second vendor.
- LangGraph not used yet (planner is a single call + one repair); planned with the Sprint 4 proposer.
- Plan drafts cannot be edited before accepting; dependency removal after accept is not modelled.
- Bench title matching (difflib ratio ≥ 0.5, same lane) is crude; tune against real gold labels.
- Embeddings path (Voyage) not exercised against the real API.

## Decisions
See `TECHNICAL_DECISIONS.md` S2-01 … S2-07 (no-build React, cookie + CSRF header, SDK fallback chain, computed
citations, validation + single repair, accept writes events only, optional embeddings). New dependencies:
anthropic, pypdf; vendored react, react-dom, htm (MIT).

## Needs Shreya
1. Put `ANTHROPIC_API_KEY` in `.env`, then run `make bench SUITE=plan` and generate a plan for the Tiffin brief;
   check the date contradiction shows up as an open question.
2. Optional: `VOYAGE_API_KEY` for chunk embeddings.
3. Write the remaining 12 briefs with gold plans (`evaluation/datasets/plan/`); review the 3 DRAFT ones.
4. Review `agents/prompts/plan_v1.md` and `plan_repair_v1.md` in your own words.
5. Carry-over from Sprint 1: GitHub App + secrets, git repo + CI, gitleaks, independent review.
