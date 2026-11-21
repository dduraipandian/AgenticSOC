"""The batch runner, with a fake store and a fake investigation: what runs, in which order, what is retried and
resumed, and that an eval never writes the portal's investigations."""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from pipeline.soc_agent.db import InvestigationRow
from pipeline.soc_agent.evals.run import Variant, check_cases, make_investigator, run_batch, run_order
from pipeline.soc_agent.providers.gemini import Gemini
from pipeline.soc_agent.providers.ollama import Ollama
from pipeline.soc_agent.tasks import TaskConfig

LABELS = {3: {"scenario_id": "S2", "expected": {"verdict": "true_positive", "entities": ["arun.m"]}}}


def variant(name: str, provider: str, model: str) -> Variant:
    config = TaskConfig(
        name="soc_investigation",
        prompt_file="soc_investigation.md",
        provider=provider,
        finish_tool="submit_investigation",
        model=model,
        tools=["submit_investigation"],
    )
    provider_obj = Ollama({"host": "http://ollama"}) if provider == "ollama" else Gemini({})
    option = SimpleNamespace(provider=provider_obj, model=model, agent_options={})
    return Variant(name, config, option)


GEMINI = variant("gemini", "gemini", "models/gemini-3.5-flash-lite")
QWEN_A = variant("qwen-a", "ollama", "qwen3.5:2b-q4_K_M")
BONSAI = variant("bonsai", "ollama", "bonsai-2:4b")
QWEN_B = variant("qwen-b", "ollama", "qwen3.5:2b-q4_K_M")  # same model as qwen-a, another prompt say


def success(case_id=3) -> InvestigationRow:
    return InvestigationRow(case_id, 1, "success", None, 10, [], [], verdict="malicious", confidence="high")


def failed(error: str, case_id=3) -> InvestigationRow:
    return InvestigationRow(case_id, 1, "failed", None, 10, [], [], error=error)


class FakeStore:
    def __init__(self, runs=()):
        self.batch_id = uuid4()
        self.batch = None
        self.runs = list(runs)  # (case_id, variant, attempt, failure_kind) of earlier runs
        self.new_runs: list = []
        self.grades: dict = {}

    def create_batch(self, conn, variants, case_ids, k, note=None):
        self.batch = {"batch_id": self.batch_id, "variants": variants, "case_ids": case_ids, "k": k}
        return self.batch_id

    def get_batch(self, conn, batch_id):
        return self.batch

    def finish_batch(self, conn, batch_id):
        self.batch["finished"] = True

    def save_run(self, conn, row):
        self.new_runs.append(row)
        self.runs.append((row.investigation.case_id, row.variant, row.attempt, row.failure_kind))
        return uuid4()

    def save_grades(self, conn, run_id, grades):
        self.grades[run_id] = grades

    def done_slots(self, conn, batch_id):
        return {(c, v, a) for c, v, a, kind in self.runs if kind != "infra"}


def batch(store, investigate, variants, k=1, resume=None):
    return run_batch(
        store=store,
        db=nullcontext,
        investigate=investigate,
        grade=lambda v, run, label, alerts: [],
        variants=variants,
        case_ids=[3],
        k=k,
        labels=LABELS,
        health=lambda v: None,
        resume_batch=resume,
    )


def test_variants_alternate_by_case_and_same_model_ollama_runs_stay_together():
    order = run_order([1, 3], [GEMINI, QWEN_A, BONSAI, QWEN_B], k=1)

    assert [(c, v.name) for _, c, v in order] == [
        (1, "gemini"), (1, "qwen-a"), (1, "qwen-b"), (1, "bonsai"),
        (3, "gemini"), (3, "qwen-a"), (3, "qwen-b"), (3, "bonsai"),
    ]  # fmt: skip


def test_an_infra_failure_is_retried_once_and_both_runs_are_kept():
    results = iter([failed("error ReadTimeout('timed out')"), success()])
    store = FakeStore()
    batch(store, lambda v, case_id: (next(results), []), [QWEN_A])

    assert [r.failure_kind for r in store.new_runs] == ["infra", None]
    assert store.batch["finished"]


def test_a_run_that_crashes_is_saved_as_the_agents_failure_and_the_batch_goes_on():
    def investigate(v, case_id):
        if v is QWEN_A:
            raise ValueError("case 3 not found")
        return success(), []

    store = FakeStore()
    batch(store, investigate, [QWEN_A, BONSAI])

    crashed, ok = store.new_runs
    assert (crashed.investigation.status, crashed.failure_kind) == ("failed", "agent")
    assert "case 3 not found" in crashed.investigation.error
    assert ok.investigation.status == "success"


def test_resume_skips_done_slots_and_reruns_infra_ones():
    store = FakeStore(runs=[(3, "qwen-a", 1, None), (3, "qwen-a", 2, "infra")])
    store.batch = {"batch_id": store.batch_id, "variants": ["qwen-a"], "case_ids": [3], "k": 2}
    batch(store, lambda v, case_id: (success(), []), [QWEN_A], k=2, resume=store.batch_id)

    assert [(r.attempt, r.failure_kind) for r in store.new_runs] == [(2, None)]


def test_an_unlabelled_case_is_refused_before_anything_runs():
    with pytest.raises(SystemExit, match="case 7 has no checked label"):
        check_cases([3, 7], LABELS)


def test_the_eval_never_writes_the_portals_investigations():
    response = SimpleNamespace(
        status="success",
        response={"verdict": "benign", "confidence": "low", "rationale": "r", "evidence": {"a": "b"}},
        execution_tracing=[],
        history=[],
        started_at=0,
        duration_ms=1,
        prompt_token_count=0,
        output_token_count=0,
    )
    task = "pipeline.soc_agent.domain_tasks.soc_investigation.SocInvestigationTask"
    with (
        patch(f"{task}.build_case_question", return_value=("q", 1, [])),
        patch("pipeline.soc_agent.agent.Agent.execute", return_value=response),
        patch("pipeline.soc_agent.db.InvestigationStore.save", side_effect=AssertionError("saved")) as save,
        patch("pipeline.cases.db.CaseStore.set_agent_status", side_effect=AssertionError("set")) as status,
    ):
        row, _ = make_investigator(SimpleNamespace(connection=nullcontext))(BONSAI, 3)

    assert row.verdict == "benign"
    assert not save.called and not status.called
