"""eval schema: labelled cases, batches, runs, grades

Revision ID: e5b9c3a7d214
Revises: d1f3a8c5e207
Create Date: 2026-11-21 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5b9c3a7d214"
down_revision: str | Sequence[str] | None = "d1f3a8c5e207"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""CREATE SCHEMA eval;""")

    # No foreign keys into pipeline.*: the lab's cases can be wiped and rebuilt, and eval history must survive that.
    op.execute("""
        CREATE TABLE eval.labelled_cases (
            case_id           bigint PRIMARY KEY,
            scenario_id       text NOT NULL,
            scenario_run_ids  uuid[] NOT NULL,
            expected          jsonb NOT NULL CHECK (expected ? 'verdict'),
            labelled_at       timestamptz NOT NULL DEFAULT now(),
            -- only cases a person has looked at are evaluated
            checked           boolean NOT NULL DEFAULT false
        );
    """)

    op.execute("""
        CREATE TABLE eval.batches (
            batch_id     uuid PRIMARY KEY DEFAULT uuidv7(),
            started_at   timestamptz NOT NULL DEFAULT now(),
            finished_at  timestamptz,
            variants     text[] NOT NULL,
            case_ids     bigint[] NOT NULL,
            k            integer NOT NULL CHECK (k >= 1),
            note         text
        );
    """)

    # similar to pipeline.investigations
    op.execute("""
        CREATE TABLE eval.runs (
            run_id            uuid PRIMARY KEY DEFAULT uuidv7(),
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

            batch_id          uuid NOT NULL REFERENCES eval.batches ON DELETE CASCADE,
            variant           text NOT NULL,
            attempt           integer NOT NULL CHECK (attempt >= 1),
            scenario_id       text NOT NULL,
            expected          jsonb NOT NULL,          -- the label as it was when the run started
            world             text NOT NULL DEFAULT 'live',
            provider          text NOT NULL,
            model             text NOT NULL,
            model_options     jsonb NOT NULL DEFAULT '{}',
            reasoning_level   text,
            prompt_hash       text,
            tool_hashes       jsonb,
            settings          jsonb NOT NULL,          -- the variant's full task config; never keys or secrets
            failure_kind      text CHECK (failure_kind IN ('agent', 'infra')),
            note              text,

            CHECK (status = 'failed' OR (verdict IS NOT NULL AND confidence IS NOT NULL)),
            CHECK (status = 'success' OR error IS NOT NULL),
            -- a failed run always says whose failure it was; a successful one has none
            CHECK ((status = 'failed') = (failure_kind IS NOT NULL))
        );
    """)

    op.execute("""
        CREATE UNIQUE INDEX runs_one_per_slot ON eval.runs (batch_id, case_id, variant, attempt)
            WHERE failure_kind IS DISTINCT FROM 'infra';
    """)
    op.execute("""CREATE INDEX runs_by_slot ON eval.runs (batch_id, case_id, variant, attempt);""")

    op.execute("""
        CREATE TABLE eval.grades (
            run_id          uuid NOT NULL REFERENCES eval.runs ON DELETE CASCADE,
            grader          text NOT NULL,
            grader_version  integer NOT NULL,
            passed          boolean,                   -- null for diagnostics that only measure (cost)
            details         jsonb NOT NULL DEFAULT '{}',
            graded_at       timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (run_id, grader, grader_version)
        );
    """)


def downgrade() -> None:
    op.execute("""DROP SCHEMA eval CASCADE;""")
