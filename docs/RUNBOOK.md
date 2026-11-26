# Operations runbook

For setting up a team, see [Onboarding](ONBOARDING.md). For first deployment, see [Deployment](DEPLOYMENT.md).

## 1. Before every deploy

- [ ] `make test` and `make lint` are green, and CI passed on the commit.
- [ ] `make bench SUITE=safety` passes (11/11). It is a CI gate; never deploy past a failure.
- [ ] Migrations run as the **owner** (`DATABASE_URL`); the app runs as `daelys_app` (`APP_DATABASE_URL`).
- [ ] `COOKIE_SECURE=true` and `FORWARDED_ALLOW_IPS` are set in any HTTPS deployment.
- [ ] Every new environment variable is in [`.env.example`](../.env.example) and [Configuration](CONFIGURATION.md).

## 2. Is it healthy?

| Check | Healthy | If not |
| --- | --- | --- |
| `GET /health` | 200 `{"status": "ok", "checks": {"db": "ok", "redis": "ok"}}` | 503 with `"degraded"` names the failing check; look at that service |
| Worker logs (`make logs`) | `project`, `evaluate` and sweep jobs every 30 s / 10 min | Restart the worker. Projection resumes from `connector_cursors`; nothing is lost |
| Board updates live | Risk tags change without a reload after new events | See "Live updates stopped" below |
| `agent_runs` | Mostly `status = 'ok'` | Many `fallback` or `error` rows: the first model in `LLM_ROUTES` is failing or rate-limited |

Useful queries (run as the owner, read-only):

```sql
-- events waiting to be projected, per workspace
SELECT t.slug, max(e.id) - coalesce((c.position->>'last_event_id')::bigint, 0) AS behind
FROM tenants t JOIN events e ON e.tenant_id = t.id
LEFT JOIN connector_cursors c ON c.tenant_id = t.id AND c.connector = 'projector'
GROUP BY t.slug, c.position;

-- proposals stuck mid-execution (the sweep settles these after 5 minutes)
SELECT id, action_type, updated_at FROM proposals WHERE status = 'executing';
```

## 3. Common admin tasks

| Task | How | Who |
| --- | --- | --- |
| Reset the demo to the end of week 1 | Projects page → Demo scenario → Reset, or `POST /admin/reset-scenario` | Owner, demo workspaces only |
| Replay week 2 to watch risk flip | Projects page → Time machine, or `make replay WEEK=week2` | Owner |
| Make someone a project lead | `POST /projects/{id}/leads` | Owner |
| Map a meeting speaker to a person | Meetings page, or `PUT /speakers` | Lead+ |
| Retry a meeting extraction | `POST /meetings/{id}/extract` | Lead+ |
| Pull GitHub history | `POST /connectors/github/backfill` (resumes from a cursor) | Lead+ |
| Allow tier-2 auto-approval | `UPDATE policies SET auto_approve_max_tier = 2 WHERE tenant_id = …` (owner connection) | Database admin |
| Tune risk thresholds | `policies` columns (see [Configuration](CONFIGURATION.md#per-workspace-policy-database-not-environment)) | Database admin |

## 4. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| A service shows `Created` but never starts (`docker compose ps`), or `notify` tests fail | Another local project already uses its host port (commonly Mailpit's 1025/8025, or 5432/6379/8000) | Change the matching `*_HOST_PORT` in `.env`, then `make up`. Don't stop the other project |
| `503 not_configured` on plan, explain, proposals, summaries or Ask | No `ANTHROPIC_API_KEY` | Set it and restart api and worker |
| Audio upload answers 503 | No `NOTIV_PATH` (expected in the production image) | Use labelled transcripts, or run audio locally |
| GitHub webhooks answer 503 | No `GITHUB_WEBHOOK_SECRET` | Set it to the App's webhook secret |
| Webhooks answer 401 | The secret doesn't match the App | Copy the secret again; GitHub's delivery log shows the response |
| A list is empty that shouldn't be | The request ran with no tenant set; RLS fails closed | A bug in the calling code path, not missing data. Check that it uses `tenant_tx` |
| `429 too many login attempts` | 10 per minute per IP and per account | Wait a minute. If every user hits it on a hosted deploy, `FORWARDED_ALLOW_IPS` is missing |
| Live updates stopped | The worker isn't running, or the API lost its `LISTEN` connection | Streams end and reconnect by themselves after a lost listener. If they don't recover, restart the API |
| A proposal stays `approved` | The worker is down or the action's service isn't configured (`execute.not_configured` in logs) | Start the worker or configure the service |
| A proposal stays `executing` | The worker died mid-action | The sweep settles it after 5 minutes. Tier-3 actions are recorded from their marker, never re-sent |
| Risk looks wrong after editing data by hand | Work items are a projection | Never edit `work_items`; write an event. The projector and rules will catch up |
| The demo shows huge slips | The workspace clock moved past the scenario | Reset the demo. The event clock follows observed work only |

## 5. Data rules to keep

- **Never update or delete `events` or `audit_log`.** The app role can't; don't do it as the owner either. Undo is
  an inverse event.
- **Never connect the app as the owner or a superuser.** RLS wouldn't apply.
- **Restore by branch.** On Neon, restore to a new branch at a timestamp, point a staging deploy at it, verify,
  then switch ([Onboarding](ONBOARDING.md#1-deploy-render--neon)).

## 6. Known limits to tell a team

- Built and tested for a handful of teams; the time machine rebuilds the projection per request (O(events)).
- Audio meetings have no speaker diarization; use labelled transcripts or speaker mapping.
- Email `notify` works with Mailpit only until the mailer gains TLS and auth.
- Model quality hasn't been measured with a live key yet ([QA report](project-management/QA_REPORT.md#7-not-verified)).
