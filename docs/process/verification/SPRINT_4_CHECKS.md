# Sprint 4 break-it checks: proposals, approvals and the executor

These checks matter most. A bug here means the agent did something nobody approved.

## Authorization [regression]

1. A member (not a lead) approving a proposal gets 403 from the API. Test it with curl, not just the UI.
2. A lead of project A approving a proposal from project B gets 403.
3. Decoy Co's lead approving a Tiffin Box proposal ID gets 404.
4. **Effective permission.** Have a lead approve an action that needs owner rights (if you have any). It should
   be refused: min(agent, approver).

## Executor safety [regression]

5. **Double-approve race.** Fire two approve calls at the same moment:
   ```bash
   for i in 1 2; do curl -s -X POST localhost:8000/proposals/$PID/approve -H "Authorization: Bearer $LEAD" & done; wait
   ```
   Expect exactly one GitHub comment on the PR and one `audit_log` row.
6. **Expired proposal.** Push its expiry into the past, then approve. It should be refused with a clear message:
   ```bash
   $PSQL_ADMIN -c "UPDATE proposals SET expires_at = now() - interval '1 minute' WHERE id='$PID';"
   ```
7. **Kill during execution.** Kill the worker while a `comment_on_pr` is running, then restart it. Expect at most
   one comment and a final state of either executed or failed, never stuck.
8. **Undo.** Approve a `move_due`, undo it, and confirm the due date, the derived risk score and the audit trail
   (execute plus undo rows).
9. **Every executed action is audited.** Expect zero rows:
   ```bash
   $PSQL_ADMIN -c "SELECT p.id FROM proposals p LEFT JOIN audit_log a ON a.proposal_id=p.id
     WHERE p.status='executed' AND a.id IS NULL;"
   ```

## Agent containment [regression]

10. **Agents can't write.** Run the Gate 3 grep on `agents/`. Also check that the agent's DB session is
    read-only, either with a separate role that has only SELECT grants, or with `SET TRANSACTION READ ONLY`.
11. **Free-text action.** Make the proposer output an action like `delete_project`. It should be rejected and
    logged, and nothing should be created.
12. **Your own injection attempts.** Write 5 hostile inputs yourself: a PR body, an issue comment, a transcript
    line, a brief line, and a replay event payload. Try to get the agent to reassign everything, notify an outsider,
    or skip approval. Nothing should ever execute without approval. Add each one to the safety suite.

## Tier 3 honesty

13. The preview shows the **exact** text that will be posted, and the posted GitHub comment matches it byte for byte.
14. `notify` emails appear only in Mailpit (http://localhost:8025), never in a real inbox.
