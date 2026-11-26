"""`create_item` proposals have no existing work item; they point at the meeting they came from.

Revision ID: 0006
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

STATEMENTS = [
    "ALTER TABLE proposals ALTER COLUMN work_item_id DROP NOT NULL",
    "ALTER TABLE proposals ADD COLUMN meeting_id uuid",
    """ALTER TABLE proposals ADD CONSTRAINT fk_proposals_meeting
        FOREIGN KEY (tenant_id, meeting_id) REFERENCES meetings (tenant_id, id) ON DELETE SET NULL (meeting_id)""",
    """ALTER TABLE proposals ADD CONSTRAINT proposals_target_check
        CHECK ((action_type = 'create_item') = (work_item_id IS NULL))""",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DELETE FROM proposals WHERE work_item_id IS NULL")
    op.execute("ALTER TABLE proposals DROP CONSTRAINT IF EXISTS proposals_target_check")
    op.execute("ALTER TABLE proposals DROP CONSTRAINT IF EXISTS fk_proposals_meeting")
    op.execute("ALTER TABLE proposals DROP COLUMN IF EXISTS meeting_id")
    op.execute("ALTER TABLE proposals ALTER COLUMN work_item_id SET NOT NULL")
