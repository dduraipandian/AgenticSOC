"""cases

Revision ID: 7c2e9a41d5b3
Revises: 4b347715ee81
Create Date: 2026-11-16 10:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '7c2e9a41d5b3'
down_revision: str | Sequence[str] | None = '4b347715ee81'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # the case builder reads alerts in the order they were written. alert_id is the raw event's uuid7,
    # so it is the puller's order, not this table's.
    op.execute("""ALTER TABLE pipeline.alerts ADD COLUMN id BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE;""")

    # -- IMPORTANT: this method is safe for one writer/reader per source
    # -- If it is concurrent, we need to move to queue like implementation.
    op.execute("""
        CREATE TABLE pipeline.case_checkpoints (
            source         text PRIMARY KEY,
            last_alert_id  bigint NOT NULL,
            updated_at     timestamptz NOT NULL DEFAULT now()
        );
    """)
    op.execute("""
        CREATE TABLE pipeline.cases (
            case_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            version        integer NOT NULL DEFAULT 0,
            status         text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed', 'merged')),
            entity_key     text,
            severity       smallint NOT NULL CHECK (severity BETWEEN 1 AND 5),
            hosts          text[] NOT NULL DEFAULT '{}',
            users          text[] NOT NULL DEFAULT '{}',
            ips            text[] NOT NULL DEFAULT '{}',
            last_alert_at  timestamptz NOT NULL,
            opened_at      timestamptz NOT NULL DEFAULT now(),
            updated_at     timestamptz NOT NULL DEFAULT now()
        );
    """)
    op.execute("""CREATE INDEX open_cases_by_entity ON pipeline.cases (entity_key, last_alert_at)
                  WHERE status = 'open';""")

    # alert_id as the key: an alert belongs to one case only
    op.execute("""
        CREATE TABLE pipeline.case_alerts (
            alert_id    uuid PRIMARY KEY,
            case_id     bigint NOT NULL,
            created_at  timestamptz NOT NULL DEFAULT now()
        );
    """)
    op.execute("""CREATE INDEX case_alerts_by_case ON pipeline.case_alerts (case_id);""")


def downgrade() -> None:
    op.execute("""DROP TABLE pipeline.case_alerts;""")
    op.execute("""DROP TABLE pipeline.cases;""")
    op.execute("""DROP TABLE pipeline.case_checkpoints;""")
    op.execute("""ALTER TABLE pipeline.alerts DROP COLUMN id;""")
