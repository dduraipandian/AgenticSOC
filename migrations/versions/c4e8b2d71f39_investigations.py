"""investigations

Revision ID: c4e8b2d71f39
Revises: a91d3f6c2b80
Create Date: 2026-11-18 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e8b2d71f39"
down_revision: str | Sequence[str] | None = "a91d3f6c2b80"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE pipeline.investigations (
            investigation_id  uuid PRIMARY KEY DEFAULT uuidv7(),
            case_id           bigint NOT NULL,
            case_version      integer NOT NULL,
            status            text NOT NULL CHECK (status IN ('success', 'failed')),
            verdict           text CHECK (verdict IN ('malicious', 'benign', 'inconclusive')),
            confidence        text CHECK (confidence IN ('low', 'medium', 'high')),
            rationale         text,
            evidence          jsonb,
            evidence_hash     text,
            error             text,
            steps             jsonb NOT NULL DEFAULT '[]',
            history           jsonb,
            prompt_tokens     integer NOT NULL DEFAULT 0,
            output_tokens     integer NOT NULL DEFAULT 0,
            duration_ms       integer NOT NULL,
            started_at        timestamptz NOT NULL,
            completed_at      timestamptz NOT NULL DEFAULT now(),
            -- a successful run always has a verdict; a failed one says why instead
            CHECK (status = 'failed' OR (verdict IS NOT NULL AND confidence IS NOT NULL)),
            CHECK (status = 'success' OR error IS NOT NULL)
        );
    """)

    op.execute(
        """CREATE INDEX investigations_by_case ON pipeline.investigations (case_id, completed_at DESC);"""
    )


def downgrade() -> None:
    op.execute("""DROP TABLE pipeline.investigations;""")
