# Sprint 1 break-it checks: foundation and ingestion

Names assumed from SPRINT_1.md: compose services `postgres`, `api`, `worker`; database `daelys`; app role `daelys_app`.

Shortcut used below:
```bash
PSQL_ADMIN="docker compose exec -T postgres psql -U postgres -d daelys"
PSQL_APP="docker compose exec -T postgres psql -U daelys_app -d daelys"
```

## Tenant isolation [regression]

1. **The app role cannot bypass RLS.** Expect `f | f`:
   ```bash
   $PSQL_ADMIN -c "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname='daelys_app';"
   ```
2. **Every tenant table has RLS on.** Expect only `alembic_version`, and `tenants` if it's deliberately global:
   ```bash
   $PSQL_ADMIN -c "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='public' AND c.relkind='r' AND NOT c.relrowsecurity;"
   ```
3. **If daelys_app owns the tables, RLS must be forced.** Expect zero rows:
   ```bash
   $PSQL_ADMIN -c "SELECT c.relname FROM pg_class c JOIN pg_roles r ON r.oid=c.relowner
     WHERE r.rolname='daelys_app' AND c.relkind='r' AND c.relrowsecurity AND NOT c.relforcerowsecurity;"
   ```
4. **Wrong tenant sees nothing.** Expect `0`:
   ```bash
   DECOY=$($PSQL_ADMIN -tAc "SELECT id FROM tenants WHERE name='Decoy Co'")
   $PSQL_APP -c "BEGIN; SET LOCAL app.tenant_id='$DECOY'; SELECT count(*) FROM events
     WHERE tenant_id <> '$DECOY'; COMMIT;"
   ```
5. **No tenant set means fail closed.** Expect an error or `0`, never all rows:
   ```bash
   $PSQL_APP -c "SELECT count(*) FROM events;"
   ```
6. **API-level isolation.** Log in as the Decoy user, take a Tiffin Box work item ID from the DB, and call
   `GET /work-items/<id>`. Expect 404, not 403: a 403 leaks that the item exists.

## Auth [regression]

7. An expired token, a token with an edited `role` claim (re-encoded without the secret), and no token
   all return 401.
8. A viewer calling a lead-only endpoint gets 403.

## GitHub webhook [regression]

9. **Bad signature returns 401:**
   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:8000/webhooks/github \
     -H "X-GitHub-Event: issues" -H "X-GitHub-Delivery: test-1" \
     -H "X-Hub-Signature-256: sha256=deadbeef" -d '{"action":"opened"}'
   ```
10. **Same delivery twice creates one event.** Save a real payload from smee as `payload.json`, then:
    ```bash
    SIG=$(openssl dgst -sha256 -hmac "$GITHUB_WEBHOOK_SECRET" < payload.json | sed 's/^.* //')
    for i in 1 2; do curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:8000/webhooks/github \
      -H "Content-Type: application/json" -H "X-GitHub-Event: pull_request" \
      -H "X-GitHub-Delivery: dup-test-1" -H "X-Hub-Signature-256: sha256=$SIG" \
      --data-binary @payload.json; done
    $PSQL_ADMIN -c "SELECT count(*) FROM events WHERE idempotency_key LIKE '%dup-test-1%';"   # expect 1
    ```
11. **Real end to end.** Open and close a PR in `tiffin-box-app`, and confirm the events and the work item status
    update within a few seconds.

## Crash and replay [regression]

12. **Kill the worker mid-replay, then restart it:**
    ```bash
    python -m connectors.replay tiffin_box week2 --speed 600 &
    sleep 3 && docker compose kill worker && docker compose up -d worker
    ```
    After it finishes, expect no duplicates and the same event count as a clean replay:
    ```bash
    $PSQL_ADMIN -c "SELECT tenant_id, idempotency_key, count(*) FROM events GROUP BY 1,2 HAVING count(*)>1;"
    ```
13. **Out-of-order events.** Replay the week 2 events in reverse order into a fresh tenant. The final work item
    statuses should match a normal replay.

## Fresh-clone and CI

14. CI is green on the pushed commit, and the CI log shows the isolation test actually ran (not skipped).
