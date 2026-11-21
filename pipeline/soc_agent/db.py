import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel

# datetimes and other non-JSON values in a trace are stored as text instead of failing the insert
_dumps = partial(json.dumps, default=str)


def evidence_hash(evidence: Any) -> str:
    canonical = json.dumps(evidence, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def to_json(history: list) -> list:
    return [m.model_dump(mode="json", exclude_none=True) if isinstance(m, BaseModel) else m for m in history]


@dataclass(frozen=True)
class InvestigationRow:
    case_id: int
    case_version: int  # the case's version when the agent read it
    status: str  # success | failed
    started_at: datetime
    duration_ms: int
    steps: list
    history: list
    prompt_tokens: int = 0
    output_tokens: int = 0
    verdict: str | None = None
    confidence: str | None = None
    rationale: str | None = None
    evidence: Any = None
    evidence_hash: str | None = None
    error: str | None = None

    @classmethod
    def from_agent_response(cls, case_id: int, case_version: int, response) -> InvestigationRow:
        common = dict(
            case_id=case_id,
            case_version=case_version,
            started_at=datetime.fromtimestamp(response.started_at / 1000, UTC),
            duration_ms=round(response.duration_ms),
            steps=response.execution_tracing,
            history=to_json(response.history),
            prompt_tokens=response.prompt_token_count,
            output_tokens=response.output_token_count,
        )

        result = response.response
        if response.status == "success" and isinstance(result, dict) and "verdict" in result:
            return cls(
                **common,
                status="success",
                verdict=result["verdict"],
                confidence=result["confidence"],
                rationale=result["rationale"],
                evidence=result["evidence"],
                evidence_hash=evidence_hash(result["evidence"]),
            )

        # a failed run, or one that "succeeded" without submitting a verdict: either way there is no result
        error = str(result) if response.status == "failed" else f"finished without a verdict: {result!r}"
        return cls(**common, status="failed", error=error)


class InvestigationStore:
    def save(self, conn, row: InvestigationRow) -> UUID:
        values = asdict(row)
        for column in ("steps", "history", "evidence"):
            values[column] = Jsonb(values[column], dumps=_dumps) if values[column] is not None else None

        return conn.execute(
            """INSERT INTO pipeline.investigations
                    (case_id, case_version, status, verdict, confidence, rationale, evidence, evidence_hash, error,
                     steps, history, prompt_tokens, output_tokens, duration_ms, started_at)
               VALUES (%(case_id)s, %(case_version)s, %(status)s, %(verdict)s, %(confidence)s, %(rationale)s,
                       %(evidence)s, %(evidence_hash)s, %(error)s, %(steps)s, %(history)s, %(prompt_tokens)s,
                       %(output_tokens)s, %(duration_ms)s, %(started_at)s)
               RETURNING investigation_id""",
            values,
        ).fetchone()[0]

    _COLUMNS = """investigation_id, case_id, case_version, status, verdict, confidence, rationale, evidence,
                  evidence_hash, error,
                  (SELECT coalesce(jsonb_agg(e.step - 'text' - 'result' - 'thought' ORDER BY e.n), '[]')
                   FROM jsonb_array_elements(steps) WITH ORDINALITY AS e(step, n)) AS steps,
                  prompt_tokens, output_tokens, duration_ms, started_at, completed_at"""

    def get_step(self, conn, investigation_id, index: int) -> dict | None:
        row = conn.execute(
            "SELECT steps -> %(index)s FROM pipeline.investigations WHERE investigation_id = %(id)s",
            dict(id=investigation_id, index=index),
        ).fetchone()
        return row[0] if row else None

    def get_by_case(self, conn, case_id: int) -> list[dict]:
        """A case's runs, newest first (uuidv7 ids break ties between runs that completed together)."""
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""SELECT {self._COLUMNS} FROM pipeline.investigations WHERE case_id = %(case_id)s
                    ORDER BY completed_at DESC, investigation_id DESC""",
                dict(case_id=case_id),
            )
            return cur.fetchall()

    def latest_by_case(self, conn, case_ids: list[int]) -> dict[int, dict]:
        """Each case's latest run, success or failed: what the queue's agent_status is derived from."""
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT DISTINCT ON (case_id) case_id, case_version, status
                   FROM pipeline.investigations WHERE case_id = ANY(%(case_ids)s)
                   ORDER BY case_id, completed_at DESC, investigation_id DESC""",
                dict(case_ids=case_ids),
            )
            return {row["case_id"]: row for row in cur.fetchall()}
