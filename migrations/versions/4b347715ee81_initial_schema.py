"""initial schema

Revision ID: 4b347715ee81
Revises:
Create Date: 2026-11-15 05:43:42.473502

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '4b347715ee81'
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""CREATE SCHEMA IF NOT EXISTS pipeline;""")
    op.execute("""
        CREATE TABLE pipeline.puller_checkpoints (
            source      text PRIMARY KEY,
            checkpoint  jsonb NOT NULL,
            updated_at  timestamptz NOT NULL DEFAULT now()
        );
    """)
    op.execute("""
        CREATE TABLE pipeline.raw_events (
            event_id        uuid PRIMARY KEY DEFAULT uuidv7(),
            id              BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
            source          text NOT NULL,
            vendor_event_id text NOT NULL,
            event           jsonb NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT unique_source_event UNIQUE (source, vendor_event_id)
        );
    """)
    op.execute("""
        CREATE TABLE pipeline.puller_failures (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            source          text NOT NULL,
            vendor_event_id text,
            event           text NOT NULL,
            error           text NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT now(),
            processed       bool NOT NULL DEFAULT false
        );
    """)

    # -- IMPORTANT: this method is safe for one writer/reader per source
    # -- If it is concurrent, we need to move to queue like implementation.
    op.execute("""
        CREATE TABLE pipeline.normalizer_checkpoints (
            source       text PRIMARY KEY,
            last_raw_id  bigint NOT NULL,
            updated_at   timestamptz NOT NULL DEFAULT now()
        );
    """)
    op.execute("""
        CREATE TABLE pipeline.alerts (
            alert_id        uuid PRIMARY KEY,
            schema_version  text NOT NULL,
            source          text NOT NULL,
            vendor_event_id text NOT NULL,
            alert_time      timestamptz NOT NULL,
            severity        smallint NOT NULL CHECK (severity BETWEEN 1 AND 5),
            category        text NOT NULL,
            technique       text,
            host            text,
            user_name       text,
            ip              inet,
            process         jsonb,
            network         jsonb,
            file            jsonb,
            created_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT unique_alert_source_event UNIQUE (source, vendor_event_id)
        );
    """)

    op.execute("""
        CREATE TABLE pipeline.normalizer_failures (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            event_id        uuid NOT NULL,
            source          text NOT NULL,
            error           text NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT now(),
            processed       bool NOT NULL DEFAULT false
        );
    """)


def downgrade() -> None:
    op.execute("""DROP TABLE pipeline.normalizer_failures;""")
    op.execute("""DROP TABLE pipeline.alerts;""")
    op.execute("""DROP TABLE pipeline.normalizer_checkpoints;""")
    op.execute("""DROP TABLE pipeline.puller_failures;""")
    op.execute("""DROP TABLE pipeline.raw_events;""")
    op.execute("""DROP TABLE pipeline.puller_checkpoints;""")
