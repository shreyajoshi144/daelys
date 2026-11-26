"""Proposals lifecycle, project leads, action policy, audit tier.

Revision ID: 0004
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

APP_ROLE = "daelys_app"
CURRENT_TENANT = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"

STATEMENTS = [
    """CREATE TABLE project_members (
        tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        project_id  uuid NOT NULL,
        user_id     uuid NOT NULL,
        role        text NOT NULL DEFAULT 'lead' CHECK (role IN ('lead')),
        created_at  timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (project_id, user_id),
        FOREIGN KEY (tenant_id, project_id) REFERENCES projects (tenant_id, id) ON DELETE CASCADE,
        FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE CASCADE
    )""",
    "CREATE INDEX ix_project_members_tenant_user ON project_members (tenant_id, user_id)",
    # Tier 3 can never be auto-approved: the column cannot hold 3 (and the code refuses it too).
    """ALTER TABLE policies ADD COLUMN auto_approve_max_tier smallint NOT NULL DEFAULT 0
        CHECK (auto_approve_max_tier BETWEEN 0 AND 2)""",
    "ALTER TABLE policies ADD COLUMN proposal_ttl_hours int NOT NULL DEFAULT 48 CHECK (proposal_ttl_hours BETWEEN 1 AND 720)",
    "ALTER TABLE proposals DROP CONSTRAINT proposals_status_check",
    """ALTER TABLE proposals ADD CONSTRAINT proposals_status_check CHECK (status IN
        ('pending', 'approved', 'executing', 'executed', 'rejected', 'expired', 'failed', 'undone'))""",
    "ALTER TABLE proposals ADD COLUMN signal_rules text[] NOT NULL DEFAULT '{}'",
    "ALTER TABLE proposals ADD COLUMN original_args jsonb",
    "ALTER TABLE proposals ADD COLUMN approval_source text CHECK (approval_source IN ('human', 'policy'))",
    "ALTER TABLE proposals ADD COLUMN executed_at timestamptz",
    "ALTER TABLE proposals ADD COLUMN execution_result jsonb",
    "ALTER TABLE proposals ADD COLUMN inverse jsonb",
    "ALTER TABLE proposals ADD COLUMN undone_at timestamptz",
    "ALTER TABLE proposals ADD COLUMN undone_by uuid",
    "ALTER TABLE proposals ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now()",
    """ALTER TABLE proposals ADD CONSTRAINT fk_proposals_undone_by
        FOREIGN KEY (tenant_id, undone_by) REFERENCES users (tenant_id, id)""",
    "CREATE INDEX ix_proposals_tenant_created ON proposals (tenant_id, created_at DESC)",
    "ALTER TABLE audit_log ADD COLUMN tier smallint CHECK (tier BETWEEN 0 AND 3)",
    "CREATE INDEX ix_audit_log_tenant_actor ON audit_log (tenant_id, actor_type, created_at)",
    "CREATE INDEX ix_audit_log_tenant_proposal ON audit_log (tenant_id, proposal_id)",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)
    op.execute("ALTER TABLE project_members ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON project_members USING (tenant_id = %s) WITH CHECK (tenant_id = %s)"
        % (CURRENT_TENANT, CURRENT_TENANT)
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON project_members TO %s" % APP_ROLE)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS project_members")
    for col in ("auto_approve_max_tier", "proposal_ttl_hours"):
        op.execute("ALTER TABLE policies DROP COLUMN IF EXISTS %s" % col)
    op.execute("ALTER TABLE proposals DROP CONSTRAINT IF EXISTS fk_proposals_undone_by")
    for col in (
        "signal_rules",
        "original_args",
        "approval_source",
        "executed_at",
        "execution_result",
        "inverse",
        "undone_at",
        "undone_by",
        "updated_at",
    ):
        op.execute("ALTER TABLE proposals DROP COLUMN IF EXISTS %s" % col)
    op.execute("ALTER TABLE audit_log DROP COLUMN IF EXISTS tier")
