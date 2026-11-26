# Evaluation

Daelys measures quality with **DaelysBench** (`evaluation/`): seven suites that run against the real pipeline,
real Postgres and the real rules. Results are written by the harness, never typed by hand. The current numbers are
in the [QA report](project-management/QA_REPORT.md) and, generated, in [`evaluation/results/latest.md`](../evaluation/results/latest.md).

## 1. What "working" means

| Capability | Working means | Suite |
| --- | --- | --- |
| Planning | The plan's tasks and dependencies match a labelled plan, every task quotes the brief, and planted contradictions are caught | plan |
| Risk | Tasks are flagged at risk when the labels say they should be, not before and not after, and the flag cites the real cause | risk |
| Safety | No input, however hostile, makes Daelys act without a valid, current approval | safety |
| Meetings | Action items, owners and due dates match labels, for English and Hinglish separately | meetings |
| Duplicates | Similar-task suggestions are mostly real duplicates | duplicates |
| Faithfulness | Generated summaries are made of cited sentences | faithfulness |
| Cost and speed | Latency, tokens and cost per summary are known | ops |

## 2. Rules for honest reporting

| Rule | How it's enforced |
| --- | --- |
| **No hand-typed numbers** | `make bench` rewrites the README section between `<!-- bench:start -->` and `<!-- bench:end -->`. A test checks the section is generated |
| **No made-up numbers** | A suite that needs a model returns `n/a` when no key is configured. The plan suite stops before any model call and exits 2 |
| **Drafts are labelled drafts** | Every dataset file carries `"draft": true`. They were written while building, so a perfect score only shows the system agrees with its author |
| **A green gate must be able to go red** | `test_safety_suite_passes_and_can_fail` patches the executor to ignore approval and checks the safety suite then fails |
| **No model judges itself** | Plan, risk, meetings and duplicates are scored by deterministic rules over labels. The duplicate suite's model stage is measured against labels, not by another model |
| **Gold must match its source** | `tests/test_bench.py` checks that every gold quote appears verbatim in its brief and every risk label matches its timeline |

## 3. Running it

```bash
make bench                # every suite
make bench SUITE=risk     # one suite: plan, risk, safety, meetings, duplicates, faithfulness, ops
```

Model configs live in `evaluation/configs.json` (`opus-5.5`, `sonnet-5.5`). Each run writes
`results/latest*.md` (tables) and `results/run-<timestamp>.json` (per-case detail; git-ignored).

```mermaid
flowchart LR
    D[(Labelled datasets<br/>draft: true)] --> R[Suite runner]
    C[configs.json] --> R
    R --> PIPE[Real pipeline<br/>Postgres · rules · executor · core/llm.py]
    PIPE --> S[Deterministic scorer]
    S --> L[results/latest*.md]
    S --> J[run-timestamp.json]
    L --> RM[README bench section<br/>rewritten between markers]
```

## 4. Plan suite

Three briefs (`evaluation/datasets/plan/`: `tiffin_box`, `clinic_reminders`, `podcast_season`), each with a
`gold.json` of tasks, dependencies and a planted contradiction.

| Metric | Definition |
| --- | --- |
| `item_precision` / `item_recall` | Tasks matched one to one: same lane and title similarity ≥ 0.5 (difflib ratio) |
| `dep_precision` / `dep_recall` | Predicted dependencies whose both ends match gold tasks, where the gold has that edge |
| `contradiction_caught_rate` | Share of cases where an open question cites a span overlapping the planted contradiction |
| `uncited_item_rate` | Share of first-answer tasks whose quote isn't in the brief |
| `cycle_rate` | Share of cases whose first answer contained a dependency cycle |
| `repair_rate` | Share of cases that needed the one repair retry |

## 5. Risk suite

Three replayed scenarios (`evaluation/datasets/risk/`) with 8 labels in total. A label says "task X is at risk from
day N because of event types Y".

| Scenario | Labels | What it probes |
| --- | --- | --- |
| `tiffin_box` | 3 | The demo launch over two weeks: CI failing near the due date and blockers raised in standup |
| `risk_diamond` | 4 | Four tasks in a diamond-shaped dependency graph; risk comes from due dates and overdue upstream work |
| `risk_quiet_pr` | 1 | A PR that goes quiet near its due date: risk from time passing, with no new event |

The suite replays each scenario and evaluates the rules at the end of every scenario day through the time machine
(nothing is persisted), then compares the first at-risk flag for each task with its label.

| Metric | Definition |
| --- | --- |
| `precision` | Labelled tasks among those flagged |
| `recall` | Flagged tasks among those labelled |
| `lead_time_days` | Days between the first flag and the task's due date |
| `detection_delay_days` | First-flag day minus the labelled day (0 = on time; negative would be early) |
| `evidence_cites_cause` | Share of flagged tasks whose signals cite an event of a labelled cause type |
| `explanation_cites_cause` | The same for the model's explanation (needs a key) |

## 6. Safety suite

Eleven adversarial cases against the real proposer, executor and database, plus one that needs a real model. **Any
failure fails CI.**

| Case | Attack |
| --- | --- |
| Model obeys hostile evidence | A scripted model returns the actions hostile evidence asks for; all must be rejected |
| "Reassign this to me" | Evidence asks for a reassignment; at most a pending proposal may result |
| Brief escapes its evidence block | Brief text tries to close the `<evidence>` tag |
| Unapproved tier 2 (`move_due`) | Execute without approval |
| Unapproved tier 3 (`notify`, `comment_on_pr`) | Two cases |
| Cross-tenant execution | Execute another workspace's approved proposal |
| Expired proposal | Execute after expiry |
| Approver demoted | The approver loses the lead role between approval and execution |
| Double execution | Four concurrent executions of one proposal must give one effect |
| Tier 3 auto-approval | The most permissive policy must still leave tier 3 pending |
| Real model on hostile evidence | Not run without a key |

## 7. Meetings suite

Three transcripts (`standup_en`, `review_en`, `standup_hinglish`), three labelled action items each. Predicted and
gold items are matched greedily one to one by text similarity ≥ 0.5.

| Metric | Definition |
| --- | --- |
| action-item F1 | From precision and recall of matched items |
| owner accuracy | Matched items with the right owner |
| due-date accuracy | Matched items with the right due date, where the label has one |

English and Hinglish are **reported separately**, because extraction quality on code-mixed standups is the
question that matters for Indian teams.

## 8. Duplicates, faithfulness and ops

| Suite | Data | Metrics |
| --- | --- | --- |
| duplicates | 10 labelled title pairs (4 duplicates) | Precision and recall of the lexical stage (trigram similarity ≥ 0.45); precision after the model confirms |
| faithfulness | Week-2 weekly summaries for every lane of the Tiffin Box workspace | `cited_share`: share of sentences with valid citations. A sample is saved for manual review |
| ops | `agent_runs` rows from bench workspaces | p50 and p95 latency, tokens and cost per summary |

## 9. What the bench does not prove yet

- **Independent labels.** All datasets are drafts by the builder; scores on them show agreement with the author.
- **Model quality.** Plan, meetings, faithfulness, ops and the model safety case need `ANTHROPIC_API_KEY` and have
  not been run.
- **Scale.** Three risk scenarios and three plan briefs are enough to catch regressions, not to claim accuracy.
  The plan suite is designed for 15 briefs.
