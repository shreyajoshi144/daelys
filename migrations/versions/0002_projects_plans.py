"""Projects, plan drafts, and plan fields on work items.

Revision ID: 0002
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

APP_ROLE = "daelys_app"
NEW_TABLES = ["projects", "plan_drafts"]
CURRENT_TENANT = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"

STATEMENTS = [
    """CREATE TABLE projects (
        id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        name        text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
        created_by  uuid,
        created_at  timestamptz NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, id),
        FOREIGN KEY (tenant_id, created_by) REFERENCES users (tenant_id, id) ON DELETE SET NULL (created_by)
    )""",
    "CREATE INDEX ix_projects_tenant_created ON projects (tenant_id, created_at)",
    "ALTER TABLE artifacts ADD COLUMN project_id uuid",
    """ALTER TABLE artifacts ADD CONSTRAINT fk_artifacts_project
        FOREIGN KEY (tenant_id, project_id) REFERENCES projects (tenant_id, id) ON DELETE CASCADE""",
    "CREATE INDEX ix_artifacts_tenant_project ON artifacts (tenant_id, project_id)",
    "ALTER TABLE work_items ADD COLUMN project_id uuid",
    "ALTER TABLE work_items ADD COLUMN owner_role text",
    "ALTER TABLE work_items ADD COLUMN estimate_days real CHECK (estimate_days > 0 AND estimate_days <= 365)",
    "ALTER TABLE work_items ADD COLUMN source_span jsonb",
    """ALTER TABLE work_items ADD CONSTRAINT fk_work_items_project
        FOREIGN KEY (tenant_id, project_id) REFERENCES projects (tenant_id, id) ON DELETE SET NULL (project_id)""",
    "CREATE INDEX ix_work_items_tenant_project ON work_items (tenant_id, project_id)",
    """CREATE TABLE plan_drafts (
        id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        project_id     uuid NOT NULL,
        artifact_id    uuid NOT NULL,
        plan           jsonb NOT NULL,
        dropped        jsonb NOT NULL DEFAULT '[]',
        repaired       boolean NOT NULL DEFAULT false,
        agent_run_ids  uuid[] NOT NULL DEFAULT '{}',
        status         text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'accepted')),
        created_by     uuid,
        accepted_by    uuid,
        accepted_at    timestamptz,
        created_at     timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY (tenant_id, project_id) REFERENCES projects (tenant_id, id) ON DELETE CASCADE,
        FOREIGN KEY (tenant_id, artifact_id) REFERENCES artifacts (tenant_id, id) ON DELETE CASCADE,
        FOREIGN KEY (tenant_id, created_by) REFERENCES users (tenant_id, id) ON DELETE SET NULL (created_by),
        FOREIGN KEY (tenant_id, accepted_by) REFERENCES users (tenant_id, id) ON DELETE SET NULL (accepted_by)
    )""",
    "CREATE INDEX ix_plan_drafts_tenant_project ON plan_drafts (tenant_id, project_id, created_at)",
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


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS plan_drafts")
    op.execute("ALTER TABLE work_items DROP CONSTRAINT IF EXISTS fk_work_items_project")
    for col in ("project_id", "owner_role", "estimate_days", "source_span"):
        op.execute("ALTER TABLE work_items DROP COLUMN IF EXISTS %s" % col)
    op.execute("ALTER TABLE artifacts DROP COLUMN IF EXISTS project_id")
    op.execute("DROP TABLE IF EXISTS projects")
