# Sprint 3 break-it checks: the risk loop

## Rule correctness

1. **Hand-computed graph.** Draw a 6-item graph on paper (one chain plus one diamond), set due dates, and make one
   upstream item 3 days late. Compute the downstream projected slip by hand. Then insert it with a test fixture and
   compare it with the CTE output. The numbers must match exactly.
2. **Cycle safety.** Insert a `depends_on` cycle directly in SQL. The propagation query must terminate, using
   a depth limit or cycle detection, and must not hang.
3. **Counterfactual test (the most useful accuracy check).** Copy `timeline_week2.json` and remove the CI-failure
   events and the "waiting on keys" event. Replay it. Health should stay **On track**. If it still flips to At
   risk, the score isn't driven by the evidence you think it is.
4. **Threshold edges.** A PR stale for exactly N days should give the result you documented (flag or no flag).
   Off-by-one errors live here.
5. **Determinism [regression].** Replay week 2 twice into fresh tenants. Every `risk_score` should be identical:
   ```bash
   $PSQL_ADMIN -c "SELECT title, risk_score FROM work_items WHERE tenant_id='<t1>' ORDER BY title;"  # compare with t2
   ```

## Explanations

6. **Faithfulness read.** Read 20 "why" sentences and open each cited event. Count the sentences where the event
   really supports the claim. Record x/20 in the sign-off. This number goes in your case study.
7. **Checker bite test.** Temporarily change the explainer prompt to "do not include citations". The checker should
   drop every sentence and the UI should show "No supported explanation". Then revert.
8. **No LLM in scoring [regression].** Run the Gate 3 grep on `rules/`. Also turn the LLM provider off and replay:
   risk scores should be unchanged and only the explanations should be missing.

## Time machine and realtime

9. "As of" a timestamp before the CI failure shows On track. "As of" after it shows At risk.
10. Open two browser windows. Replaying in one updates the health card in the other within a few seconds over SSE.
    Kill the API, and the client should reconnect without a page reload.

## Performance sanity

11. Seed 2,000 work items and 10,000 edges (a script is fine). Propagation and rules should take under about a
    second. Run `EXPLAIN ANALYZE` on the CTE and add indexes on `edges(tenant_id, from_id)` and
    `edges(tenant_id, to_id)` if it's slow.
