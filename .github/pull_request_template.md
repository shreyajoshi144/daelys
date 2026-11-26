## What changed and why

## How it was tested
<!-- Paste the real summary lines: `make test`, `make lint`, and `make bench SUITE=...` if risk, actions or prompts changed. -->

## Design rules (CLAUDE.md)
- [ ] Events stay append-only; `work_items` only changes through events
- [ ] Risk scores come from `rules/` only (no model sets a score)
- [ ] Every generated sentence still passes the citation checker
- [ ] Agent code stays read-only; only `workers/execute.py` acts, and only on approved proposals
- [ ] Every new table has `tenant_id` and an RLS policy
- [ ] New prompts are versioned files in `agents/prompts/`
- [ ] `.env.example` lists any new setting
