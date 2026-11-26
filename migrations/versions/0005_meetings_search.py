"""Meetings, speaker mapping, role-restricted documents, search indexes, summaries, duplicates, comments, demo reset.

Revision ID: 0005
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

APP_ROLE = "daelys_app"
CURRENT_TENANT = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
NEW_TABLES = ["meetings", "speaker_mappings", "summaries", "duplicate_suggestions", "comments"]
ROLES = "('viewer', 'member', 'lead', 'owner')"

STATEMENTS = [
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    # Documents can be restricted to a minimum role; retrieval filters on this in SQL before ranking.
    "ALTER TABLE artifacts ADD COLUMN min_role text NOT NULL DEFAULT 'viewer' CHECK (min_role IN "
    + ROLES
    + ")",
    "CREATE INDEX ix_chunks_fts ON chunks USING gin (to_tsvector('simple', content))",
    "CREATE INDEX ix_events_fts ON events USING gin (to_tsvector('simple', coalesce(payload->>'body', '') || ' ' "
    "|| coalesce(payload->>'text', '') || ' ' || coalesce(payload->'fields'->>'title', '')))",
    "CREATE INDEX ix_work_items_title_trgm ON work_items USING gin (title gin_trgm_ops)",
    # Derived, recomputable: embedding of the title (when an embedding provider is configured).
    "ALTER TABLE work_items ADD COLUMN title_embedding vector(1024)",
    "ALTER TABLE work_items ADD COLUMN duplicates_checked_at timestamptz",
    """CREATE TABLE meetings (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        project_id    uuid,
        title         text NOT NULL,
        occurred_at   timestamptz NOT NULL,
        language      text NOT NULL DEFAULT 'auto',
        source        text NOT NULL CHECK (source IN ('audio', 'transcript')),
        artifact_id   uuid,
        status        text NOT NULL DEFAULT 'transcribed'
                      CHECK (status IN ('transcribed', 'extracted', 'failed')),
        extraction    jsonb,
        created_by    uuid,
        created_at    timestamptz NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, id),
        FOREIGN KEY (tenant_id, project_id) REFERENCES projects (tenant_id, id) ON DELETE SET NULL (project_id),
        FOREIGN KEY (tenant_id, artifact_id) REFERENCES artifacts (tenant_id, id) ON DELETE SET NULL (artifact_id),
        FOREIGN KEY (tenant_id, created_by) REFERENCES users (tenant_id, id) ON DELETE SET NULL (created_by)
    )""",
    "CREATE INDEX ix_meetings_tenant_time ON meetings (tenant_id, occurred_at DESC)",
    """CREATE TABLE speaker_mappings (
        tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        label       text NOT NULL CHECK (length(label) BETWEEN 1 AND 100),
        user_id     uuid NOT NULL,
        created_at  timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id, label),
        FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE CASCADE
    )""",
    """CREATE TABLE summaries (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        project_id    uuid NOT NULL,
        lane          text NOT NULL CHECK (lane IN ('Product', 'Engineering', 'Marketing')),
        week_start    date NOT NULL,
        version       int NOT NULL,
        sentences     jsonb NOT NULL,
        dropped       int NOT NULL DEFAULT 0,
        agent_run_id  uuid,
        created_by    uuid,
        created_at    timestamptz NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, project_id, lane, week_start, version),
        FOREIGN KEY (tenant_id, project_id) REFERENCES projects (tenant_id, id) ON DELETE CASCADE,
        FOREIGN KEY (tenant_id, agent_run_id) REFERENCES agent_runs (tenant_id, id)
    )""",
    """CREATE TABLE duplicate_suggestions (
        id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        item_id        uuid NOT NULL,
        duplicate_of   uuid NOT NULL,
        similarity     real NOT NULL,
        reason         text NOT NULL,
        status         text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'dismissed', 'confirmed')),
        agent_run_id   uuid,
        created_at     timestamptz NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, item_id, duplicate_of),
        CHECK (item_id <> duplicate_of),
        FOREIGN KEY (tenant_id, item_id) REFERENCES work_items (tenant_id, id) ON DELETE CASCADE,
        FOREIGN KEY (tenant_id, duplicate_of) REFERENCES work_items (tenant_id, id) ON DELETE CASCADE
    )""",
    """CREATE TABLE comments (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        work_item_id  uuid NOT NULL,
        author_id     uuid NOT NULL,
        body          text NOT NULL CHECK (length(body) BETWEEN 1 AND 4000),
        created_at    timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY (tenant_id, work_item_id) REFERENCES work_items (tenant_id, id) ON DELETE CASCADE,
        FOREIGN KEY (tenant_id, author_id) REFERENCES users (tenant_id, id) ON DELETE CASCADE
    )""",
    "CREATE INDEX ix_comments_tenant_item ON comments (tenant_id, work_item_id, created_at)",
    # A sixth action, `create_item`, explicitly requested by the Sprint 5 spec: an unmatched meeting action item
    # becomes a proposal through the normal approval flow, never a silent new work item.
    "ALTER TABLE proposals DROP CONSTRAINT proposals_action_type_check",
    """ALTER TABLE proposals ADD CONSTRAINT proposals_action_type_check CHECK (action_type IN
        ('reassign', 'move_due', 'split', 'notify', 'comment_on_pr', 'create_item'))""",
    "ALTER TABLE tenants ADD COLUMN is_demo boolean NOT NULL DEFAULT false",
    # Demo reset is the one sanctioned way to clear a tenant's log, and only for tenants flagged as demos.
    """CREATE FUNCTION daelys_reset_demo(p_tenant uuid) RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
    BEGIN
        IF p_tenant IS DISTINCT FROM NULLIF(current_setting('app.tenant_id', true), '')::uuid THEN
            RAISE EXCEPTION 'reset must run inside the tenant''s own transaction';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM tenants WHERE id = p_tenant AND is_demo) THEN
            RAISE EXCEPTION 'tenant is not a demo tenant';
        END IF;
        DELETE FROM proposals WHERE tenant_id = p_tenant;
        DELETE FROM signals WHERE tenant_id = p_tenant;
        DELETE FROM duplicate_suggestions WHERE tenant_id = p_tenant;
        DELETE FROM comments WHERE tenant_id = p_tenant;
        DELETE FROM summaries WHERE tenant_id = p_tenant;
        DELETE FROM meetings WHERE tenant_id = p_tenant;
        DELETE FROM plan_drafts WHERE tenant_id = p_tenant;
        DELETE FROM edges WHERE tenant_id = p_tenant;
        DELETE FROM work_items WHERE tenant_id = p_tenant;
        DELETE FROM connector_cursors WHERE tenant_id = p_tenant;
        DELETE FROM audit_log WHERE tenant_id = p_tenant;
        DELETE FROM events WHERE tenant_id = p_tenant;
    END $$""",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)
    for table in NEW_TABLES:
        op.execute("ALTER TABLE %s ENABLE ROW LEVEL SECURITY" % table)
        op.execute(
            "CREATE POLICY tenant_isolation ON %s USING (tenant_id = %s) WITH CHECK (tenant_id = %s)"
            % (table, CURRENT_TENANT, CURRENT_TENANT)
        )
        op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON %s TO %s" % (table, APP_ROLE))
    op.execute("REVOKE ALL ON FUNCTION daelys_reset_demo(uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION daelys_reset_demo(uuid) TO %s" % APP_ROLE)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS daelys_reset_demo(uuid)")
    for table in reversed(NEW_TABLES):
        op.execute("DROP TABLE IF EXISTS %s" % table)
    op.execute("ALTER TABLE proposals DROP CONSTRAINT proposals_action_type_check")
    op.execute("""ALTER TABLE proposals ADD CONSTRAINT proposals_action_type_check CHECK (action_type IN
        ('reassign', 'move_due', 'split', 'notify', 'comment_on_pr'))""")
    for stmt in (
        "DROP INDEX IF EXISTS ix_work_items_title_trgm",
        "DROP INDEX IF EXISTS ix_events_fts",
        "DROP INDEX IF EXISTS ix_chunks_fts",
        "ALTER TABLE work_items DROP COLUMN IF EXISTS title_embedding",
        "ALTER TABLE work_items DROP COLUMN IF EXISTS duplicates_checked_at",
        "ALTER TABLE artifacts DROP COLUMN IF EXISTS min_role",
        "ALTER TABLE tenants DROP COLUMN IF EXISTS is_demo",
    ):
        op.execute(stmt)
