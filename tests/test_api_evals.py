"""The portal's eval endpoints. The scoreboard must be the CLI report's (report.summarise), the run view must survive
runs that crashed early, and the review must only ever be saved for a run that could still be correct."""

from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

import api.evals
from api.eval_mapping import batch_detail, run_detail
from api.investigation import create_app
from pipeline.soc_agent.evals.graders import Grade
from pipeline.soc_agent.evals.report import run_outcome, summarise

T0 = datetime(2026, 11, 21, 5, 0, tzinfo=UTC)
LABELS = {
    1: {"scenario_id": "S6", "expected": {"verdict": "benign"}},
    3: {"scenario_id": "S2", "expected": {"verdict": "true_positive"}},
}
SETTINGS = {"name": "soc_investigation", "provider": "ollama", "model": "bonsai-2:4b", "reasoning_level": "high",
            "prompt_file": "soc_investigation.md"}  # fmt: skip
TRACE = [
    {"type": "request", "seq": 0, "at": 1795209200097.0, "text": "Case 3: ...", "model": "bonsai-2:4b"},
    {"type": "tool", "seq": 1, "step": 0, "at": 1795209209931.0, "name": "get_process_tree", "duration_ms": 24,
     "args": {"pid": 472}, "result": {"found": True}, "error": None},
]  # fmt: skip


def run(
    case_id, variant="production", attempt=1, failure_kind=None, status="success", steps=TRACE, error=None
):
    return {
        "run_id": uuid4(),
        "batch_id": UUID(int=1),
        "case_id": case_id,
        "variant": variant,
        "attempt": attempt,
        "failure_kind": failure_kind,
        "status": status,
        "verdict": "malicious" if status == "success" else None,
        "confidence": "high" if status == "success" else None,
        "rationale": "arun.m read /etc/shadow" if status == "success" else None,
        "evidence": {"user": "arun.m"} if status == "success" else None,
        "error": error,
        "steps": steps,
        "settings": SETTINGS,
        "prompt_tokens": 100,
        "output_tokens": 10,
        "duration_ms": 1000,
        "started_at": T0,
        "completed_at": T0,
    }


def grades(**passed) -> dict[str, Grade]:
    return {name: Grade(name, 1, p, {}) for name, p in passed.items()}


def batch(**kw):
    return {"batch_id": UUID(int=1), "note": "baseline", "started_at": T0, "finished_at": None,
            "variants": ["production", "qwen2b"], "case_ids": [1, 2, 3], "k": 3} | kw  # fmt: skip


def test_the_scoreboard_is_the_reports():
    runs = [
        run(3),
        run(3, attempt=2),
        run(1),
        run(3, "qwen2b", failure_kind="infra", status="failed", error="x"),
    ]
    g = {
        runs[0]["run_id"]: grades(verdict=True, process=True, grounding=True),
        runs[1]["run_id"]: grades(verdict=True, process=False, grounding=True),
        runs[2]["run_id"]: grades(verdict=True, process=True, grounding=True, rationale_human=True),
    }
    detail = batch_detail(batch(), runs, g, LABELS)

    expected = summarise(runs, g, LABELS, 3)
    assert [r.model_dump() for r in detail.rows] == [
        {k: v for k, v in vars(r).items() if k != "failing"} for r in expected
    ]
    assert [r.outcome for r in detail.runs] == [run_outcome(r, g.get(r["run_id"], {})) for r in runs]
    assert (detail.batch.runs_done, detail.batch.runs_total, detail.pending_reviews, detail.infra) == (
        3,
        18,
        1,
        1,
    )
    assert detail.variants[0].model == "bonsai-2:4b"


def test_a_batch_with_no_runs_yet():
    detail = batch_detail(batch(), [], {}, LABELS)

    assert (detail.rows, detail.runs, detail.batch.runs_done, detail.batch.runs_total) == ([], [], 0, 18)
    assert detail.median_ms == 0
    assert detail.batch.last_run_at is None


def test_an_unchecked_case_drops_off_the_scoreboard():
    runs = [run(1), run(3)]
    detail = batch_detail(batch(), runs, {}, {3: LABELS[3]})

    assert {r.case_id for r in detail.rows} == {3}
    assert [c.case_id for c in detail.cases] == [3]
    assert {r.case_id for r in detail.runs} == {1, 3}  # its runs still open by id


def test_a_run_that_crashed_before_its_first_request():
    crashed = run(
        3, status="failed", failure_kind="agent", steps=[], error="the run crashed: ValueError('x')"
    )
    view = run_detail(crashed, {})

    assert (view.steps, view.evidence, view.error, view.outcome) == ([], [], crashed["error"], "incorrect")


class FakeStore:
    def __init__(self, runs=()):
        self.runs = {r["run_id"]: r for r in runs}
        self.saved: list = []

    def get_run(self, conn, run_id):
        return self.runs.get(run_id)

    def latest_grades(self, conn, run_ids):
        return {i: self.grades for i in run_ids}

    def save_grades(self, conn, run_id, new):
        self.saved.append((run_id, new))
        self.grades = self.grades | {g.grader: g for g in new}


class TxConn:
    """A connection that knows whether it is inside conn.transaction()."""

    def __init__(self):
        self.depth = 0

    @contextmanager
    def transaction(self):
        self.depth += 1
        try:
            yield
        finally:
            self.depth -= 1


@pytest.fixture
def client_with(monkeypatch):
    def make(store):
        monkeypatch.setattr(api.evals, "eval_store", store)
        conn = TxConn()
        app = create_app(
            pool=SimpleNamespace(connection=lambda: nullcontext(conn)), investigate=lambda *a: None
        )
        return TestClient(app)

    return make


def test_a_missing_run_or_step_is_404(client_with):
    known = run(3)
    store = FakeStore([known])
    store.grades = {}
    with client_with(store) as client:
        assert client.get(f"/evals/runs/{uuid4()}").json()["error"] == "not_found"
        assert client.get(f"/evals/runs/{known['run_id']}/steps/99").status_code == 404
        step = client.get(f"/evals/runs/{known['run_id']}/steps/1").json()
        assert (step["kind"], step["name"], step["investigation_id"]) == (
            "tool",
            "get_process_tree",
            str(known["run_id"]),
        )


CODE_PASS = grades(verdict=True, process=True, grounding=True)


def test_a_review_is_saved_with_the_analyst_and_replaced_when_saved_again(client_with):
    pending = run(3)
    store = FakeStore([pending])
    store.grades = dict(CODE_PASS)
    url = f"/evals/runs/{pending['run_id']}/review"
    with client_with(store) as client:
        first = client.put(url, json={"correct": True, "note": " ok "}, headers={"X-Analyst": "dd"})
        second = client.put(
            url, json={"correct": False, "note": "missed arun.m"}, headers={"X-Analyst": "dd"}
        )

    assert (first.status_code, first.json()["outcome"]) == (200, "correct")
    assert (second.status_code, second.json()["outcome"]) == (200, "incorrect")
    saved = [grade for _, new in store.saved for grade in new]
    assert [(g.grader, g.version, g.passed) for g in saved] == [
        ("rationale_human", 1, True),
        ("rationale_human", 1, False),
    ]
    assert saved[0].details == {"note": "ok", "reviewer": "dd"}
    assert saved[1].details == {"note": "missed arun.m", "reviewer": "dd"}


@pytest.mark.parametrize(
    "the_run, its_grades",
    [
        (
            run(3),
            grades(verdict=True, process=False, grounding=True),
        ),  # incorrect whatever the rationale says
        (run(3, failure_kind="infra", status="failed", error="timed out"), {}),
    ],
)
def test_a_run_that_failed_a_code_check_or_was_infra_cannot_be_reviewed(client_with, the_run, its_grades):
    store = FakeStore([the_run])
    store.grades = its_grades
    with client_with(store) as client:
        response = client.put(
            f"/evals/runs/{the_run['run_id']}/review", json={"correct": True}, headers={"X-Analyst": "dd"}
        )

    assert (response.status_code, response.json()["error"]) == (409, "not_reviewable")
    assert store.saved == []


def test_a_review_without_the_analyst_header_is_refused(client_with):
    pending = run(3)
    store = FakeStore([pending])
    store.grades = dict(CODE_PASS)
    with client_with(store) as client:
        response = client.put(f"/evals/runs/{pending['run_id']}/review", json={"correct": True})

    assert response.status_code == 400
    assert store.saved == []


class TxRecordingStore(FakeStore):
    """Records, for each call, whether it ran inside conn.transaction()."""

    def __init__(self, runs):
        super().__init__(runs)
        self.in_tx: list[tuple[str, bool]] = []

    def get_run(self, conn, run_id):
        self.in_tx.append(("get_run", conn.depth > 0))
        return super().get_run(conn, run_id)

    def latest_grades(self, conn, run_ids):
        self.in_tx.append(("latest_grades", conn.depth > 0))
        return super().latest_grades(conn, run_ids)

    def save_grades(self, conn, run_id, new):
        self.in_tx.append(("save_grades", conn.depth > 0))
        super().save_grades(conn, run_id, new)


def test_the_reviews_reads_and_write_share_one_transaction(client_with):
    # psycopg opens a real transaction only on an idle connection; a transaction() entered after a SELECT is a
    # savepoint, and the commit would then come after the answer. So the reads must be inside it too.
    pending = run(3)
    store = TxRecordingStore([pending])
    store.grades = dict(CODE_PASS)
    with client_with(store) as client:
        client.put(
            f"/evals/runs/{pending['run_id']}/review", json={"correct": True}, headers={"X-Analyst": "dd"}
        )

    assert store.in_tx == [("get_run", True), ("latest_grades", True), ("save_grades", True)]
