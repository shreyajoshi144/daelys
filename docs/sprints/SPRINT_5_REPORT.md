# Sprint 5 report: meetings, summaries, search, ship

> **Historical record**, written at the end of the sprint. Items listed here as gaps or "Needs Shreya" are tracked
> today in the [product backlog](../project-management/PRODUCT_BACKLOG.md); current status is in the
> [closure report](../project-management/CLOSURE_REPORT.md).

## What was built
- **Meetings input** (`connectors/notiv.py`). Notiv (`NOTIV_PATH`, here `~/Downloads/notiv-step1`) is used as a library:
  its `core/lecture` is imported as the package `notiv_lecture`, and nothing is copied or stubbed. Audio goes through
  ffmpeg to WAV and then Notiv's local faster-whisper. Notiv has no diarization, so owners come from labelled
  transcripts (`[00:12] Name: text`). Speakers map to members through an explicit mapping (`PUT /speakers`), or else
  through a unique first-name match.
- **Extraction** (`core/meetings.py`, `agents/meeting_extractor.py`, prompt `meeting_v1`). It produces decisions,
  action items and blockers, and each one must cite transcript line numbers. Invented lines, non-speaker owners and
  implausible dates are dropped or cleared.
  - Matching = retrieval top-k + LLM confirm (`agents/matcher.py`, `match_v1`).
  - A matched item gets `action.proposed` / `blocker.mentioned` events.
  - An unmatched action item becomes a pending **`create_item` proposal** (migration `0006`). It is never a silent
    new work item, and it goes through the normal approve → executor → undo path.
- **Retrieval** (`core/retrieval.py`). The role filter (`artifacts.min_role`) and the tenant filter (RLS) run in SQL
  before ranking. Ranking uses pgvector when an embedding key exists, else Postgres FTS / pg_trgm. The mode is
  returned with every result.
- **Weekly summaries** (`summary_v1`). They are versioned (regenerating keeps both), and every sentence passes the
  citation checker.
- **Duplicate detection** (`duplicate_v1`): suggestions only (dismiss or confirm), never merged.
- **Ask Daelys** (`ask_v1`). It answers with `[evt:]`/`[doc:]` citations, or says "Not enough evidence in this
  workspace to answer that."
- **Other features:**
  - Comments on work items, live via SSE.
  - `.ics` upload (read-only, no URL fetch, to avoid SSRF).
  - Demo reset (owner, demo tenant only, via a SECURITY DEFINER function).
  - Members endpoint.
- **Web:** Meetings list and detail (transcript with line highlight, speaker mapping, run extraction), Ask page,
  project summaries, duplicates panel, comments, Reset demo.
- **Bench:**
  - New suites: `meetings` (F1, owner and due-date accuracy, **en and Hinglish reported separately**), `duplicates`,
    `faithfulness` (with a sample file for manual review) and `ops` (latency, tokens, cost per summary).
  - `make bench` runs every suite. The README bench section is rewritten from `latest.md` between markers, so no
    number in it is typed by hand (tested).
- **Ship:**
  - `render.yaml` (api + worker + env group), README rewrite, `docs/ARCHITECTURE.md`, `docs/ONBOARDING.md`
    (Render/Neon, GitHub App permissions, speaker mapping, tiers, point-in-time restore, isolation checks).
  - Docker `INSTALL_MEETINGS` build arg (ffmpeg + `requirements-meetings.lock`) and a read-only `/notiv` mount.
- **Fixed during the live check:**
  - The summary "Week of" default picked a **Sunday** in IST: `toISOString()` shifted local Monday back to UTC.
  - The server read the week in server-local time, so host and container windows differed.
  - Weeks are now Monday-to-Monday UTC, and the server normalises any date to its Monday. A regression assertion
    was added.

## Real output (2026-11-08)
- **Host tests:** `pytest -q` gave `155 passed in 74.71s` before the week fix, and `155 passed in 76.75s` after it (coverage run). In between,
  `tests/test_search_and_insights.py` gave 11 passed.
  - 19 tests are new this sprint: meetings (6, incl. a **real Notiv/Whisper transcription** of speech from macOS
    `say`), search and insights (11), bench (2).
  - Coverage: 87% total (155 passed with the week fix). By module: core/meetings.py 94%, core/insights.py 98%, agents/meeting_extractor.py 95%, agents/writers.py 100%, connectors/ics.py 91%, connectors/notiv.py 88%, core/retrieval.py 78% (vector paths need an embedding key), api/routes/collab.py 63% (audio upload, duplicate confirm/dismiss and speaker listing are untested over HTTP).
- **Lint and audit:**
  - `ruff check .`: All checks passed.
  - `mypy` (source dirs): "no issues found in 68 source files". mypy over `tests/` reports 12 type-annotation
    errors, mostly from earlier sprints. They don't affect behaviour, and `make lint` doesn't check tests.
  - `node --check` on all web JS: OK.
  - pip-audit: "No known vulnerabilities found" for both `requirements.lock` and `requirements-meetings.lock`.
- **Containers:** rebuilt with `INSTALL_MEETINGS` (ffmpeg + Whisper). The first build attempt failed on a network timeout; the retry succeeded. `make up && make migrate && make seed && make test && make bench` exited 0: `155 passed, 1 skipped in 53.73s`. The skip is the macOS-`say` Notiv test, which has no `say` on Linux. Bench numbers are identical to the host run.
- **`make bench`** (all suites, no model key), as written to the README:

| Suite | Result |
| --- | --- |
| risk | precision 1.00, recall 1.00, lead time 2.64 days (3 cases) |
| safety | 11/11 passed, 1 not run (needs a key) |
| duplicates | lexical stage: precision 0.57, recall 1.00 at trigram ≥ 0.45; after the LLM judge: n/a |
| meetings (en / Hinglish) | n/a / n/a (needs `ANTHROPIC_API_KEY`) |
| plan, faithfulness, ops | not run (needs a key) |

- **Live check** (API + worker on the host, port 8029, dev database migrated to `0006` and seeded):

| Check | Result |
| --- | --- |
| Labelled transcript upload | 201; speakers Meera and Arjun auto-mapped, unknown "Rohit" left unmapped; `meeting.transcribed` event |
| Extraction, Ask, summary without a key | `503 not_configured`, shown in the UI as "needs server configuration" |
| Comment | 201, listed |
| Write without CSRF header | 403 |
| Demo reset | 202, 30 events replayed, meetings cleared, 11 items re-projected |
| Pages | Meetings, meeting detail, Ask and project (summaries, duplicates) render with no JS errors besides the expected 401 before login and the 503 above |

## UI pass and fixes (2026-11-09, on request)
- **Public landing page** (`#/`): the loop, features and principles. The product preview uses the shipped Tiffin Box
  sample and is labelled as an illustration. There are no invented customers or metrics.
- **Sign-in** (`#/login[/workspace]`): split screen, remembered workspace, show/hide password and a clear error
  message. Invite-only, because signup is on the cut list.
- **Notion-style workspace:** a sidebar with the workspace, Ask, nav with a pending-proposals count, projects with
  health dots and the user. Breadcrumb topbar, and a mobile drawer.
- **Palette:** neutral ink, a forest-green accent and yellow for "watch". "At risk" is solid ink, so there is no red,
  orange, blue, purple or beige. Light and dark modes. No new dependencies.
- **Fixes found while checking:**
  - A blank screen on landing → sign-in: `useEffect(() => window.scrollTo(...))` returned a Promise in current
    Chromium, which React then called as a cleanup.
  - Deep links now survive sign-in, and sign-out returns to the landing page.
  - The meeting date default was UTC shown as local time; it now uses the workspace clock.
  - **Event-clock bug (S5-08):** one meeting dated "today" moved the demo clock from March to November, which
    showed a 239-day slip on every task. Meetings no longer move the clock, and plan acceptance is stamped at the
    workspace clock. Regression test added.
- Checked in the browser:
  - landing (light/dark, mobile)
  - wrong-password message and sign-in
  - board and why panel
  - meetings form default
  - deep link → sign-in → back
  - sign-out → landing
  - mobile drawer
  - demo reset (clock 2026-03-06, On track)
- After this pass: host `156 passed in 76.79s`, ruff clean, mypy clean. The container run above predates the UI
  pass; the containers were rebuilt afterwards and serve the new UI.

## Acceptance
| Criterion | Status |
| --- | --- |
| `make test` and `make bench` green; README numbers come from `latest.md` | Met on host; container 155 passed + 1 skipped. README section is generated (test `test_readme_bench_section_is_generated`) |
| Meetings suite reports English and Hinglish separately | Met in structure; **both n/a** until a key is set, so there is no Hinglish vs English finding yet |
| Deployed URL works (login, reset, replay, risk flip, approve) | **Not done**: there is no Render/Neon account. Verified locally only |
| Fresh clone: `make up && make migrate && make seed` with only `.env` | Not verified from a clone: there is no git repo yet. The same commands work in this checkout |

Break-it checks (`docs/process/verification/SPRINT_5_CHECKS.md`):
- Covered by tests:
  - 3 (no silent merges): `test_extract_matches_known_items_and_proposes_unknown`
  - 4 (role leak): `test_ask_never_leaks_a_restricted_doc`, `test_role_filter_happens_before_ranking`
  - 5 and 6 (tenant leak, unanswerable): `test_other_tenant_and_unanswerable_questions`
  - 7 (duplicates suggested, never merged): `test_duplicates_are_suggested_never_merged`
  - With scripted models, because there is no key.
- Partly covered: 10 (health checks DB + Redis; JSON logs exclude transcripts and tokens) and 11 (rate limits,
  same-origin, httpOnly cookie).
- Need a deployment or Shreya: 1, 2, 8, 9, 12–16.

## Known gaps
- No live LLM run, so extraction quality, the Hinglish gap, summary faithfulness (x/30), Ask quality and cost per
  summary or replay are unmeasured.
- All datasets are drafts written by the builder:
  - meetings: 3 transcripts, of 10 wanted
  - duplicates: 10 pairs
- The duplicate lexical stage has 0.57 precision. That is why the LLM judge exists; its precision is unmeasured.
- Audio has no diarization; owners need labelled transcripts or a mapping.
- On macOS, the host prints an objc "class implemented in both cv2 and av" warning when Notiv loads. It is harmless
  and doesn't appear in the Linux image.
- Not deployed, so there is no load test, cold-deploy notes, recovery test or stranger test.
- Google Calendar OAuth: not built (`.ics` upload only).

## Decisions
Recorded in `TECHNICAL_DECISIONS.md`:
- S5-01 Postgres over a graph DB.
- S5-03 Notiv as a library, extraction in Daelys, labelled speakers.
- S5-04 `create_item` as a sixth action. The spec's "below threshold → proposal" needs it, and it is tier 2 with
  undo.
- S5-05 Retrieval with a reported fallback mode.
- S5-06 `.ics` upload, not a URL (SSRF).
- S5-07 Demo reset is the only log deletion: it is owner-only and only for demo tenants.

New dependencies:
- `pg_trgm`: a Postgres extension, not a package.
- Optional extra `meetings`: Notiv's own requirements (faster-whisper etc.) plus `opencv-python-headless`, because
  Notiv's import chain needs cv2.

## Needs Shreya
1. `ANTHROPIC_API_KEY` in `.env`, then `make bench`. This fills plan, meetings (en vs Hinglish), duplicates judge,
   faithfulness and ops. Then read `evaluation/results/faithfulness-sample.md` and record x/30.
2. Record 3 meetings with friends (one Hinglish), label gold action items, and bring the transcripts to 10 in
   `evaluation/datasets/meetings/`. Replace the draft duplicate pairs with independent labels.
3. Optional `VOYAGE_API_KEY` for vector search; without it, search is lexical and says so.
4. Render + Neon accounts and secrets (`docs/ONBOARDING.md`). Then on the deployed URL:
   - cold deploy notes
   - `hey` load test (p95, errors)
   - worker restart during a replay
   - the stranger test
   - cost per summary and per replay in the README
5. GitHub App (carried over) for the real `comment_on_pr`.
6. Create the git repo and CI (carried over), then the fresh-clone check.
7. Record the 3-minute demo as `docs/demo.gif`, run the two-week pilot and write the case study.
