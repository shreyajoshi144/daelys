"""Foundation: tenants, auth, event log, projections, RLS, app role.

Revision ID: 0001
"""

import os

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

APP_ROLE = "daelys_app"

# Every table here is tenant-scoped and gets RLS. `tenants` is scoped by its own id.
TENANT_TABLES = [
    "users",
    "memberships",
    "integrations",
    "events",
    "work_items",
    "edges",
    "artifacts",
    "chunks",
    "signals",
    "agent_runs",
    "proposals",
    "audit_log",
    "connector_cursors",
]
APPEND_ONLY = ["events", "audit_log"]

# Unset or reset GUC -> NULL -> no rows (fail closed). NULLIF guards the '' a pooled connection reverts to.
CURRENT_TENANT = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE tenants (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
    name        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email         text NOT NULL CHECK (email = lower(email)),
    display_name  text NOT NULL,
    department    text CHECK (department IN ('Product', 'Engineering', 'Marketing')),
    github_login  text,
    password_hash text,  -- NULL means the principal cannot log in (the agent)
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, email),
    UNIQUE (tenant_id, github_login)
);

CREATE TABLE memberships (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id     uuid NOT NULL,
    role        text NOT NULL CHECK (role IN ('owner', 'lead', 'member', 'viewer', 'agent')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, user_id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE CASCADE
);

CREATE TABLE integrations (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    provider     text NOT NULL CHECK (provider IN ('github')),
    external_id  text NOT NULL,
    config       jsonb NOT NULL DEFAULT '{}',
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (provider, external_id)
);
CREATE INDEX ix_integrations_tenant ON integrations (tenant_id);

CREATE TABLE events (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    source           text NOT NULL,
    type             text NOT NULL,
    idempotency_key  text NOT NULL,
    occurred_at      timestamptz NOT NULL,
    ingested_at      timestamptz NOT NULL DEFAULT now(),
    actor            text,
    subject_key      text,
    payload          jsonb NOT NULL DEFAULT '{}',
    UNIQUE (tenant_id, idempotency_key)
);
CREATE INDEX ix_events_tenant_id ON events (tenant_id, id);
CREATE INDEX ix_events_tenant_occurred ON events (tenant_id, occurred_at);
CREATE INDEX ix_events_tenant_subject ON events (tenant_id, subject_key);
CREATE INDEX ix_events_tenant_type ON events (tenant_id, type);

CREATE TABLE work_items (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    key               text NOT NULL,
    kind              text NOT NULL CHECK (kind IN ('issue', 'pr', 'task')),
    title             text,
    status            text NOT NULL DEFAULT 'open'
                      CHECK (status IN ('open', 'in_progress', 'in_review', 'blocked', 'done', 'cancelled')),
    lane              text CHECK (lane IN ('Product', 'Engineering', 'Marketing')),
    owner_login       text,
    owner_id          uuid,
    due_at            timestamptz,
    url               text,
    ci_status         text CHECK (ci_status IN ('pending', 'success', 'failure')),
    ci_checks         jsonb NOT NULL DEFAULT '{}',
    last_activity_at  timestamptz,
    field_versions    jsonb NOT NULL DEFAULT '{}',
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, key),
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, owner_id) REFERENCES users (tenant_id, id) ON DELETE SET NULL (owner_id)
);
CREATE INDEX ix_work_items_tenant_status ON work_items (tenant_id, status);
CREATE INDEX ix_work_items_tenant_owner ON work_items (tenant_id, owner_id);
CREATE INDEX ix_work_items_tenant_due ON work_items (tenant_id, due_at);
CREATE INDEX ix_work_items_tenant_updated ON work_items (tenant_id, updated_at DESC, id);

CREATE TABLE edges (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    src_id           uuid NOT NULL,
    dst_id           uuid NOT NULL,
    kind             text NOT NULL CHECK (kind IN ('closes', 'depends_on', 'relates_to')),
    source_event_id  bigint REFERENCES events(id),
    created_at       timestamptz NOT NULL DEFAULT now(),
    CHECK (src_id <> dst_id),
    UNIQUE (tenant_id, src_id, dst_id, kind),
    FOREIGN KEY (tenant_id, src_id) REFERENCES work_items (tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, dst_id) REFERENCES work_items (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX ix_edges_tenant_dst ON edges (tenant_id, dst_id);
CREATE INDEX ix_edges_tenant_kind ON edges (tenant_id, kind);

CREATE TABLE artifacts (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    kind          text NOT NULL,
    title         text NOT NULL,
    content_type  text NOT NULL,
    content       text NOT NULL,
    sha256        text NOT NULL,
    created_by    uuid,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, created_by) REFERENCES users (tenant_id, id) ON DELETE SET NULL (created_by)
);
CREATE INDEX ix_artifacts_tenant ON artifacts (tenant_id, created_at);

CREATE TABLE chunks (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    artifact_id  uuid NOT NULL,
    ordinal      int NOT NULL,
    content      text NOT NULL,
    char_start   int NOT NULL,
    char_end     int NOT NULL CHECK (char_end >= char_start),
    embedding    vector(1024),
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (artifact_id, ordinal),
    FOREIGN KEY (tenant_id, artifact_id) REFERENCES artifacts (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX ix_chunks_tenant_artifact ON chunks (tenant_id, artifact_id);

CREATE TABLE signals (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    work_item_id        uuid NOT NULL,
    rule                text NOT NULL,
    severity            text NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    evidence_event_ids  bigint[] NOT NULL DEFAULT '{}',
    details             jsonb NOT NULL DEFAULT '{}',
    computed_at         timestamptz NOT NULL DEFAULT now(),
    resolved_at         timestamptz,
    FOREIGN KEY (tenant_id, work_item_id) REFERENCES work_items (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX ix_signals_tenant_item ON signals (tenant_id, work_item_id);
CREATE INDEX ix_signals_tenant_rule ON signals (tenant_id, rule);

CREATE TABLE agent_runs (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    agent           text NOT NULL,
    provider        text,
    model           text,
    prompt_version  text,
    input_tokens    int,
    output_tokens   int,
    latency_ms      int,
    cost_usd        numeric(12, 6),
    status          text NOT NULL CHECK (status IN ('ok', 'error', 'fallback')),
    error           text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id)
);
CREATE INDEX ix_agent_runs_tenant_created ON agent_runs (tenant_id, created_at);

CREATE TABLE proposals (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    work_item_id        uuid NOT NULL,
    agent_run_id        uuid,
    action_type         text NOT NULL
                        CHECK (action_type IN ('reassign', 'move_due', 'split', 'notify', 'comment_on_pr')),
    args                jsonb NOT NULL,
    evidence_event_ids  bigint[] NOT NULL DEFAULT '{}',
    expected_effect     text NOT NULL,
    reversible          boolean NOT NULL,
    tier                smallint NOT NULL CHECK (tier BETWEEN 0 AND 3),
    status              text NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'approved', 'rejected', 'executed', 'expired', 'failed')),
    idempotency_key     text NOT NULL,
    approved_by         uuid,
    decided_at          timestamptz,
    rejection_reason    text,
    expires_at          timestamptz NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, idempotency_key),
    FOREIGN KEY (tenant_id, work_item_id) REFERENCES work_items (tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, agent_run_id) REFERENCES agent_runs (tenant_id, id),
    FOREIGN KEY (tenant_id, approved_by) REFERENCES users (tenant_id, id)
);
CREATE INDEX ix_proposals_tenant_status ON proposals (tenant_id, status);
CREATE INDEX ix_proposals_tenant_item ON proposals (tenant_id, work_item_id);

CREATE TABLE audit_log (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    actor_type      text NOT NULL CHECK (actor_type IN ('human', 'agent', 'system')),
    actor_id        uuid,
    on_behalf_of    uuid,
    action          text NOT NULL,
    target_type     text NOT NULL,
    target_id       text NOT NULL,
    before          jsonb,
    after           jsonb,
    proposal_id     uuid,
    model           text,
    prompt_version  text,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_audit_log_tenant_created ON audit_log (tenant_id, created_at);

CREATE TABLE connector_cursors (
    tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    connector   text NOT NULL,
    position    jsonb NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, connector)
);

-- Pre-tenant lookups. SECURITY DEFINER runs as the table owner, which RLS does not apply to (not FORCEd),
-- so each function returns only an id, never row data.
CREATE FUNCTION daelys_resolve_tenant(p_slug text) RETURNS uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
    AS $$ SELECT id FROM tenants WHERE slug = p_slug $$;

CREATE FUNCTION daelys_resolve_integration(p_provider text, p_external_id text) RETURNS uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
    AS $$ SELECT tenant_id FROM integrations WHERE provider = p_provider AND external_id = p_external_id $$;

CREATE FUNCTION daelys_tenant_ids() RETURNS SETOF uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
    AS $$ SELECT id FROM tenants ORDER BY id $$;
"""

FUNCTIONS = [
    "daelys_resolve_tenant(text)",
    "daelys_resolve_integration(text, text)",
    "daelys_tenant_ids()",
]


def _role_statements() -> list[str]:
    return [
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '%s') THEN "
        "CREATE ROLE %s LOGIN; END IF; END $$" % (APP_ROLE, APP_ROLE),
        "ALTER ROLE %s NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE" % APP_ROLE,
    ]


def _rls_statements() -> list[str]:
    stmts = [
        "ALTER TABLE tenants ENABLE ROW LEVEL SECURITY",
        "CREATE POLICY tenant_isolation ON tenants USING (id = %s) WITH CHECK (id = %s)"
        % (CURRENT_TENANT, CURRENT_TENANT),
    ]
    for table in TENANT_TABLES:
        stmts.append("ALTER TABLE %s ENABLE ROW LEVEL SECURITY" % table)
        stmts.append(
            "CREATE POLICY tenant_isolation ON %s USING (tenant_id = %s) WITH CHECK (tenant_id = %s)"
            % (table, CURRENT_TENANT, CURRENT_TENANT)
        )
    return stmts


def _grant_statements() -> list[str]:
    stmts = [
        "DO $$ BEGIN EXECUTE format('GRANT CONNECT ON DATABASE %%I TO %s', current_database()); END $$"
        % APP_ROLE,
        "GRANT USAGE ON SCHEMA public TO %s" % APP_ROLE,
        "GRANT SELECT, INSERT, UPDATE ON tenants TO %s" % APP_ROLE,
        "GRANT SELECT, INSERT, UPDATE, DELETE ON %s TO %s" % (", ".join(TENANT_TABLES), APP_ROLE),
        "REVOKE UPDATE, DELETE ON %s FROM %s" % (", ".join(APPEND_ONLY), APP_ROLE),
        "GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO %s" % APP_ROLE,
    ]
    for fn in FUNCTIONS:
        stmts.append("REVOKE ALL ON FUNCTION %s FROM PUBLIC" % fn)
        stmts.append("GRANT EXECUTE ON FUNCTION %s TO %s" % (fn, APP_ROLE))
    return stmts


def upgrade() -> None:
    # Statements run one at a time: asyncpg prepares each, and prepared statements cannot hold several.
    # All SQL below is built from module constants only; no runtime input is interpolated.
    for stmt in _role_statements():
        op.execute(stmt)
    password = os.environ.get("DAELYS_APP_PASSWORD")
    if password:
        # Passed as a bind parameter, then quoted by format(%L): never interpolated into SQL text.
        bind = op.get_bind()
        bind.execute(sa.text("SELECT set_config('daelys.app_password', :pw, true)"), {"pw": password})
        op.execute(
            "DO $$ BEGIN EXECUTE format('ALTER ROLE %s PASSWORD %%L', "
            "current_setting('daelys.app_password')); END $$" % APP_ROLE
        )
    for stmt in (s.strip() for s in SCHEMA.split(";\n")):
        if stmt:
            op.execute(stmt)
    for stmt in _rls_statements() + _grant_statements():
        op.execute(stmt)


def downgrade() -> None:
    for fn in FUNCTIONS:
        op.execute("DROP FUNCTION IF EXISTS %s" % fn)
    for table in reversed(["tenants", *TENANT_TABLES]):
        op.execute("DROP TABLE IF EXISTS %s CASCADE" % table)
