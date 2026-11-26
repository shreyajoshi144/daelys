# Sprint 5 break-it checks: meetings, search and production

## Meetings

1. **Listen and compare.** Play one real recording and write down the action items yourself, before you look at
   Daelys's output. Then compare and note the misses and false items. This is more convincing than F1 alone.
2. **English vs Hinglish** are reported separately in `latest.md`. If Hinglish is much worse, say so in the README.
   That's honest and interesting.
3. **No silent merges.** An action item that doesn't match any existing task creates a *proposal*, not a work item.

## Search and permissions [regression]

4. **Role leak.** Upload a doc restricted to leads, then ask Ask Daelys about its contents as a viewer. The answer
   must not reveal it, and the citations must not include it.
5. **Tenant leak.** As Decoy Co, ask "what is blocking the AutoPay launch?". Expect "not enough evidence".
6. **Unanswerable question.** Ask something the workspace can't know. Expect "not enough evidence", not a guess.
7. **Duplicates.** Create an obvious duplicate and an item that only looks similar. Only the first should be
   suggested, and nothing should merge on its own.

## Summaries

8. Read the weekly summary next to the audit log for that week. Every claim should be traceable to an event.
   Record the faithfulness spot check (x/30) in the sign-off.

## Production readiness (on the deployed URL)

9. **Cold deploy from zero.** Create new Render and Neon resources from `render.yaml` and the docs alone. Note every
   step that wasn't documented, then document it.
10. **Health and logs.** `/health` checks DB and Redis. Logs are JSON, with no tokens, passwords or full transcripts.
    ```bash
    # in Render log search: look for "Bearer", "password", "sk-", "ghs_"  -> expect nothing
    ```
11. **Security basics.** CORS allows only your frontend origin. Login and LLM endpoints are rate limited (try 30
    rapid logins). HTTPS only. Cookies or tokens can't be read from another origin.
12. **Small load test.** Use [k6](https://k6.io) or `hey` with about 20 concurrent users for 2 minutes on the read
    endpoints. Record p95 latency and the error rate. Expect no 5xx errors. Put the numbers in the README.
    ```bash
    hey -z 2m -c 20 -H "Authorization: Bearer $TOKEN" https://<api>/work-items
    ```
13. **Recovery.** Restart the worker during a replay on the deployed app. The result is the same as a clean run.
    Know where Neon's point-in-time restore is, and write it in ONBOARDING.md.
14. **Reset scenario.** A stranger (ask a friend) opens the URL, logs into the demo tenant, resets, replays, sees
    the risk flip and approves a proposal, with no help from you. Watch them, and note where they got confused.
15. **Cost.** `agent_runs` shows the cost per weekly summary and per full demo replay. Put both in the README.

## Final regression

16. Run the **[regression]** checks from Sprints 1–4 against the deployed app, not just locally.
