"""bounded cases

Revision ID: a91d3f6c2b80
Revises: 7c2e9a41d5b3
Create Date: 2026-11-16 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a91d3f6c2b80"
down_revision: str | Sequence[str] | None = "7c2e9a41d5b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE pipeline.cases
            ADD COLUMN first_alert_at    timestamptz,
            ADD COLUMN alert_count       integer NOT NULL DEFAULT 0,
            ADD COLUMN related_case_ids  bigint[] NOT NULL DEFAULT '{}';
    """)
    # cases made before this migration
    op.execute("""
        UPDATE pipeline.cases c SET first_alert_at = s.first_alert_at, alert_count = s.alert_count
        FROM (SELECT ca.case_id, min(a.alert_time) AS first_alert_at, count(*) AS alert_count
                FROM pipeline.case_alerts ca JOIN pipeline.alerts a USING (alert_id)
                GROUP BY ca.case_id) s
        WHERE s.case_id = c.case_id;
    """)
    op.execute("""UPDATE pipeline.cases SET first_alert_at = last_alert_at WHERE first_alert_at IS NULL;""")
    op.execute("""ALTER TABLE pipeline.cases ALTER COLUMN first_alert_at SET NOT NULL;""")

    # the latest case of an entity, open or closed, for related_case_ids
    op.execute("""CREATE INDEX cases_by_entity ON pipeline.cases (entity_key, last_alert_at);""")


def downgrade() -> None:
    op.execute("""DROP INDEX pipeline.cases_by_entity;""")
    op.execute("""
        ALTER TABLE pipeline.cases
            DROP COLUMN related_case_ids,
            DROP COLUMN alert_count,
            DROP COLUMN first_alert_at;
    """)
