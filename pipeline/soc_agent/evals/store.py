"""Saving and reading eval batches, runs and grades (schema `eval`). Every SQL statement of the eval lives here.

A run's row is the InvestigationRow production would save (same success / failed classification) plus what the
eval needs: batch, variant, attempt, the label at run time, and the settings it ran with. Model, prompt hash,
tool hashes, reasoning level and model options are read from the run's own request event, so a row records what
actually ran, not what the runner meant to run.
"""

from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row, tuple_row
from psycopg.types.json import Jsonb

from pipeline.soc_agent.db import InvestigationRow, _dumps
from pipeline.soc_agent.evals.graders import Grade


def _json(value: Any) -> Jsonb | None:
    return None if value is None else Jsonb(value, dumps=_dumps)


def _rows(conn, sql: str, params) -> list[tuple]:
    """Plain tuples whatever row factory the caller's connection uses."""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


@dataclass(frozen=True)
class EvalRunRow:
    investigation: InvestigationRow
    batch_id: UUID
    variant: str
    attempt: int
    scenario_id: str
    expected: dict  # the label as it was when the run started
    settings: dict  # the variant's TaskConfig.model_dump(): never keys or secrets
    provider: str
    model: str
    model_options: dict
    reasoning_level: str | None = None
    prompt_hash: str | None = None
    tool_hashes: dict | None = None
    failure_kind: str | None = None  # 'agent' | 'infra' for a failed run, None for a successful one
    note: str | None = None
    world: str = "live"

    @classmethod
    def from_investigation(
        cls,
        investigation: InvestigationRow,
        *,
        batch_id: UUID,
        variant: str,
        attempt: int,
        label: dict,
        settings: dict,
        failure_kind: str | None = None,
        note: str | None = None,
    ) -> EvalRunRow:
        # a run that failed before its first model call has no request event: fall back to the variant's settings
        request = next((e for e in investigation.steps if e.get("type") == "request"), {})
        if investigation.status == "failed":
            failure_kind = failure_kind or "agent"
        else:
            failure_kind = None

        return cls(
            investigation=investigation,
            batch_id=batch_id,
            variant=variant,
            attempt=attempt,
            scenario_id=label["scenario_id"],
            expected=label["expected"],
            settings=settings,
            provider=settings["provider"],
            model=request.get("model") or settings.get("model"),
            model_options=request.get("model_options") or {},
            reasoning_level=request.get("reasoning_level", settings.get("reasoning_level")),
            prompt_hash=request.get("prompt_hash"),
            tool_hashes=request.get("tools"),
            failure_kind=failure_kind,
            note=note,
        )


class EvalStore:
    def create_batch(
        self, conn, variants: list[str], case_ids: list[int], k: int, note: str | None = None
    ) -> UUID:
        return _rows(
            conn,
            "INSERT INTO eval.batches (variants, case_ids, k, note) VALUES (%s, %s, %s, %s) RETURNING batch_id",
            (variants, case_ids, k, note),
        )[0][0]

    def finish_batch(self, conn, batch_id: UUID) -> None:
        conn.execute("UPDATE eval.batches SET finished_at = now() WHERE batch_id = %s", (batch_id,))

    def get_batch(self, conn, batch_id: UUID) -> dict | None:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM eval.batches WHERE batch_id = %s", (batch_id,))
            return cur.fetchone()

    def save_run(self, conn, row: EvalRunRow) -> UUID:
        values = asdict(row.investigation) | {k: v for k, v in asdict(row).items() if k != "investigation"}
        for column in (
            "steps",
            "history",
            "evidence",
            "expected",
            "settings",
            "model_options",
            "tool_hashes",
        ):
            values[column] = _json(values[column])

        return _rows(
            conn,
            """INSERT INTO eval.runs
                    (case_id, case_version, status, verdict, confidence, rationale, evidence, evidence_hash, error,
                     steps, history, prompt_tokens, output_tokens, duration_ms, started_at,
                     batch_id, variant, attempt, scenario_id, expected, world, provider, model, model_options,
                     reasoning_level, prompt_hash, tool_hashes, settings, failure_kind, note)
               VALUES (%(case_id)s, %(case_version)s, %(status)s, %(verdict)s, %(confidence)s, %(rationale)s,
                       %(evidence)s, %(evidence_hash)s, %(error)s, %(steps)s, %(history)s, %(prompt_tokens)s,
                       %(output_tokens)s, %(duration_ms)s, %(started_at)s,
                       %(batch_id)s, %(variant)s, %(attempt)s, %(scenario_id)s, %(expected)s, %(world)s,
                       %(provider)s, %(model)s, %(model_options)s, %(reasoning_level)s, %(prompt_hash)s,
                       %(tool_hashes)s, %(settings)s, %(failure_kind)s, %(note)s)
               RETURNING run_id""",
            values,
        )[0][0]

    def save_grades(self, conn, run_id: UUID, grades: list[Grade]) -> None:
        """Re-grading with the same grader version replaces that grade; a new version is added beside the old."""
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO eval.grades (run_id, grader, grader_version, passed, details)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (run_id, grader, grader_version)
                   DO UPDATE SET passed = excluded.passed, details = excluded.details, graded_at = now()""",
                [(run_id, g.grader, g.version, g.passed, _json(g.details)) for g in grades],
            )

    def batch_runs(self, conn, batch_id: UUID) -> list[dict]:
        """Every run of a batch, infra failures included, in the order they ran."""
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT * FROM eval.runs WHERE batch_id = %s ORDER BY started_at, run_id", (batch_id,)
            )
            return cur.fetchall()

    def latest_grades(self, conn, run_ids: list[UUID]) -> dict[UUID, dict[str, Grade]]:
        """run_id -> grader -> its grade from the newest version of that grader."""
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT DISTINCT ON (run_id, grader) run_id, grader, grader_version, passed, details
                   FROM eval.grades WHERE run_id = ANY(%s)
                   ORDER BY run_id, grader, grader_version DESC""",
                (run_ids,),
            )
            grades: dict[UUID, dict[str, Grade]] = {}
            for r in cur.fetchall():
                grades.setdefault(r["run_id"], {})[r["grader"]] = Grade(
                    r["grader"], r["grader_version"], r["passed"], r["details"]
                )
            return grades

    def done_slots(self, conn, batch_id: UUID) -> set[tuple[int, str, int]]:
        """(case_id, variant, attempt) slots that already have a run that counts, so --resume skips them.
        An infra-failed run does not count: that slot runs again."""
        rows = _rows(
            conn,
            """SELECT case_id, variant, attempt FROM eval.runs
               WHERE batch_id = %s AND failure_kind IS DISTINCT FROM 'infra'""",
            (batch_id,),
        )
        return {(int(c), v, a) for c, v, a in rows}
