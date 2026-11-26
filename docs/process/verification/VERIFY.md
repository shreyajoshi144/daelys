# Verifying each sprint

A sprint is done only when all five gates pass, in this order, and you have filled in the sign-off at the bottom.
Don't trust the sprint report on its own: it was written by the same session that wrote the code.

## What "production ready" means for Daelys

Be honest about the bar. For a portfolio project it means:

- **Correct:** features do what they claim, measured on data you labelled.
- **Safe:** no cross-tenant leaks, no unapproved actions, no secrets in the repo, injection attempts fail.
- **Reproducible:** a fresh clone runs with one `.env`, and the same replay gives the same result.
- **Recoverable:** a crash or a retry never duplicates or corrupts data.
- **Observable:** you can tell from logs and `agent_runs` what happened and what it cost.

It does not mean "handles 10,000 teams". Say that plainly in the README.

---

## Gate 1: Fresh-clone check (10 min)

Run this in a new folder, not your working copy:

```bash
cd /tmp && rm -rf daelys-check && git clone <your-repo-url> daelys-check && cd daelys-check
cp ~/daelys.env .env            # keep a copy of your real .env outside the repo
make up && make migrate && make seed && make test
```

It fails if any step needs a manual fix that isn't in the README.

## Gate 2: Automated quality checks (5 min)

These targets are added in Sprint 1 (see `SPRINT_1.md`, step 11):

```bash
make lint     # ruff check . && ruff format --check . && mypy core api agents rules workers connectors
make audit    # pip-audit && (cd web && npm audit --omit=dev) && gitleaks detect --no-banner
make cov      # pytest --cov --cov-report=term-missing
```

These are the pass bars:

- lint and mypy are clean
- no high or critical vulnerabilities
- gitleaks finds nothing
- coverage is at least 80% on `rules/`, `workers/execute.py`, `agents/citations.py` and `core/` (the rest can be lower)

## Gate 3: Invariant checks (5 min)

These are quick greps for breaches of the design rules. Each one should print nothing:

```bash
# Rule 2: no LLM anywhere in risk scoring
grep -rnE "llm|langgraph|openai|mistral|anthropic" rules/

# Rule 4: agents never write to the database or call write APIs
grep -rnE "session\.(add|delete|merge|execute)\(|\.commit\(|INSERT |UPDATE |DELETE " agents/

# No SQL built with f-strings or string concatenation
grep -rnE "execute\(f\"|execute\(f'|text\(f\"|text\(f'" --include=*.py .

# Rule 6: untrusted text never placed in system prompts
grep -rn "system" agents/prompts/ | grep -iE "transcript|pr_body|brief_text"
```

A hit doesn't automatically mean a bug, but every hit needs an explanation in the sign-off.

## Gate 4: Independent review (30–60 min)

Open a **new** Claude Code session with no history and paste `docs/process/verification/REVIEW_PROMPT.md`. It reviews only;
it must not change code. Then do the following:

1. For each finding, reproduce it yourself before accepting it.
2. In a **separate** session, fix only the confirmed findings, with a test for each one.
3. Rerun Gates 1–3.

## Gate 5: Break-it checks (30–60 min)

Run `docs/process/verification/SPRINT_N_CHECKS.md` for the sprint you just finished, plus the checks from all earlier sprints
marked **[regression]**. You run these yourself, by hand. This is where you actually learn the system.

---

## Sign-off (copy into `docs/sprints/SPRINT_N_REPORT.md`)

| Gate | Result | Notes |
| --- | --- | --- |
| 1 Fresh clone | pass / fail | |
| 2 Lint, audit, coverage | pass / fail | coverage on core modules: __% |
| 3 Invariant greps | pass / explained | |
| 4 Independent review | findings: __ confirmed, __ fixed | |
| 5 Break-it checks | __ / __ passed | |
| Bench numbers | copied from `evaluation/results/latest.md` | |
| What I can explain without notes | | |

Tag the commit only after the sign-off: `git tag sprint-N && git push --tags`.
