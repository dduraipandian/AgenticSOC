"""case agent status

Revision ID: d1f3a8c5e207
Revises: c4e8b2d71f39
Create Date: 2026-11-18 14:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1f3a8c5e207"
down_revision: str | Sequence[str] | None = "c4e8b2d71f39"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE pipeline.cases
            ADD COLUMN agent_status text NOT NULL DEFAULT 'pending'
                CHECK (agent_status IN ('pending', 'investigating', 'done', 'failed'));
    """)

    # cases investigated before this migration take their latest run's outcome
    op.execute("""
        UPDATE pipeline.cases c SET agent_status = CASE latest.status WHEN 'success' THEN 'done' ELSE 'failed' END
        FROM (SELECT DISTINCT ON (case_id) case_id, status FROM pipeline.investigations
              ORDER BY case_id, completed_at DESC, investigation_id DESC) latest
        WHERE latest.case_id = c.case_id;
    """)


def downgrade() -> None:
    op.execute("""ALTER TABLE pipeline.cases DROP COLUMN agent_status;""")
