"""Risk loop: per-tenant policies, derived risk columns on work_items, signal lifecycle + cached explanations.

Revision ID: 0003
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

APP_ROLE = "daelys_app"
CURRENT_TENANT = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"

STATEMENTS = [
    """CREATE TABLE policies (
        tenant_id            uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
        stale_pr_days        int  NOT NULL DEFAULT 3 CHECK (stale_pr_days BETWEEN 1 AND 60),
        due_window_days      int  NOT NULL DEFAULT 5 CHECK (due_window_days BETWEEN 1 AND 60),
        overload_limit       int  NOT NULL DEFAULT 3 CHECK (overload_limit BETWEEN 1 AND 50),
        blocker_window_days  int  NOT NULL DEFAULT 7 CHECK (blocker_window_days BETWEEN 1 AND 60),
        clock                text NOT NULL DEFAULT 'wall' CHECK (clock IN ('wall', 'event')),
        updated_at           timestamptz NOT NULL DEFAULT now()
    )""",
    # Derived by the rules engine (recomputable from events + rules); never set from an event.
    "ALTER TABLE work_items ADD COLUMN risk_score smallint NOT NULL DEFAULT 0 CHECK (risk_score BETWEEN 0 AND 100)",
    "ALTER TABLE work_items ADD COLUMN risk_level text NOT NULL DEFAULT 'ok' CHECK (risk_level IN ('ok', 'watch', 'at_risk'))",
    "ALTER TABLE work_items ADD COLUMN projected_slip_days real NOT NULL DEFAULT 0",
    "ALTER TABLE work_items ADD COLUMN risk_evaluated_at timestamptz",
    "CREATE INDEX ix_work_items_tenant_risk ON work_items (tenant_id, risk_level)",
    "ALTER TABLE signals ADD COLUMN explanation jsonb",
    "ALTER TABLE signals ADD COLUMN explanation_evidence bigint[]",
    "CREATE UNIQUE INDEX ux_signals_open ON signals (tenant_id, work_item_id, rule) WHERE resolved_at IS NULL",
    "CREATE INDEX ix_events_tenant_subject_time ON events (tenant_id, subject_key, occurred_at)",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)
    op.execute("ALTER TABLE policies ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON policies USING (tenant_id = %s) WITH CHECK (tenant_id = %s)"
        % (CURRENT_TENANT, CURRENT_TENANT)
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON policies TO %s" % APP_ROLE)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_events_tenant_subject_time")
    op.execute("DROP INDEX IF EXISTS ux_signals_open")
    op.execute("ALTER TABLE signals DROP COLUMN IF EXISTS explanation_evidence")
    op.execute("ALTER TABLE signals DROP COLUMN IF EXISTS explanation")
    for col in ("risk_score", "risk_level", "projected_slip_days", "risk_evaluated_at"):
        op.execute("ALTER TABLE work_items DROP COLUMN IF EXISTS %s" % col)
    op.execute("DROP TABLE IF EXISTS policies")
