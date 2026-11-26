# Independent review prompt

Paste this into a fresh Claude Code session. Replace N with the sprint number.

---

You are reviewing Sprint N of Daelys. You did not write this code. Your job is to find real defects, not to praise
or to rewrite. **Do not modify any files.**

1. Read `CLAUDE.md` and `docs/process/sprints/SPRINT_N.md`. Treat the design rules and acceptance criteria as the spec.
2. Run `make test`, `make lint` and the sprint's `make bench` suite. Report the real output. If something can't run,
   say why.
3. Review the code changed in this sprint (`git diff sprint-<N-1>..HEAD`, or the whole repo for Sprint 1) for:
   - **Spec gaps:** every acceptance criterion. Is it actually implemented and actually tested, or only claimed?
   - **Design-rule breaches:** rules 1–8 in CLAUDE.md, especially tenant isolation, the agent never writing state,
     and risk never being LLM-set.
   - **Security:** RLS on every tenant table (including new ones), app role not superuser and not BYPASSRLS,
     missing `SET LOCAL app.tenant_id` on any path (workers too), authz on every endpoint, webhook signature checks,
     SQL injection, secrets in code or logs, CORS, rate limits on login and LLM endpoints.
   - **Reliability:** idempotency on retries, crash mid-job, out-of-order events, timeouts on external calls,
     unbounded queries or file reads, missing DB indexes on hot filters.
   - **Tests that can't fail:** tests that mock the thing under test, assert nothing meaningful, or skip silently.
   - **Evaluation integrity:** gold labels or test briefs leaking into prompts, few-shot examples or code;
     metrics computed incorrectly; results typed by hand instead of generated.
4. Output a table: severity (P0 = data leak, unsafe action or wrong result; P1 = likely bug; P2 = quality),
   file:line, what's wrong, how to reproduce it, and suggested fix. Then list what you checked and found fine.
   If you're unsure about a finding, mark it "unconfirmed" rather than leaving it out.
