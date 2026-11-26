# Running Daelys's sprints with Claude Code

1. Create an empty repo `daelys`, copy `CLAUDE.md` to the root and `docs/process/sprints/` into it. Commit.
2. Open Claude Code in the repo. For each sprint, use one session and this prompt:

   > Read CLAUDE.md and sprints/SPRINT_1.md. First, give me a short plan of the files you'll create and the
   > order. Then build the sprint. Run `make test` (and `make bench` where the sprint says) and show me the real
   > output. Finish by writing docs/sprints/SPRINT_1_REPORT.md.

3. Before moving on, do the sprint's "Needs Shreya" list yourself, then run the five gates in
   `docs/process/verification/VERIFY.md`: fresh clone, lint and audit, invariant greps, an independent review in a NEW session
   (`docs/process/verification/REVIEW_PROMPT.md`), and the break-it checks in `docs/process/verification/SPRINT_N_CHECKS.md`.
   Don't start the next sprint until the sign-off table is filled in.
4. Commit at the end of each sprint with a tag (`sprint-1` ... `sprint-5`), so you can show history.

## What Claude Code can't do for you

- Accounts and secrets: GitHub App, LLM keys, Render, Neon.
- Labelled data: gold plans, risk timelines, meeting action items. This is your evaluation, so it must be yours.
- Recordings, the pilot, the demo video, the case study.
- Understanding. After each sprint, read the rules, the CTE, the executor and the RLS policies until you can
  explain them without notes. Interviewers will ask about exactly those.

## If a sprint gets too big for one session

Split it at the numbered build steps. Tell Claude Code: "Do steps 1–5 of SPRINT_3.md only, then stop and report."
