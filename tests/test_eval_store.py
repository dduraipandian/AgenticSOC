"""An eval run row is built from the same InvestigationRow production saves, so a run is classified (success,
failed, "finished without a verdict") exactly as it would be in production. The variant's settings are read
back from the run's own trace, not from what the runner believes it ran."""

from types import SimpleNamespace
from uuid import uuid4

from pipeline.soc_agent.db import InvestigationRow
from pipeline.soc_agent.evals.store import EvalRunRow

REQUEST = {
    "type": "request",
    "model": "bonsai-2:4b",
    "model_options": {"num_ctx": 65536},
    "prompt_hash": "p-hash",
    "reasoning_level": "high",
    "tools": {"get_host": "t-hash"},
    "finish_tool": "submit_investigation",
}
LABEL = {"scenario_id": "S2", "expected": {"verdict": "true_positive"}}
SETTINGS = {"name": "soc_investigation", "provider": "ollama", "model": "bonsai-2:4b"}


def response(status: str, result, steps=(REQUEST,)):
    return SimpleNamespace(
        status=status,
        response=result,
        execution_tracing=list(steps),
        history=[],
        started_at=1_795_000_000_000,
        duration_ms=1234.5,
        prompt_token_count=10,
        output_token_count=5,
    )


def eval_row(resp, **kw):
    investigation = InvestigationRow.from_agent_response(3, 1, resp)
    return EvalRunRow.from_investigation(
        investigation, batch_id=uuid4(), variant="production", attempt=1, label=LABEL, settings=SETTINGS, **kw
    )


def test_a_run_records_what_its_trace_says_it_ran_with():
    submitted = {
        "verdict": "malicious",
        "confidence": "high",
        "rationale": "r",
        "evidence": {"user": "arun.m"},
    }
    row = eval_row(response("success", submitted))

    assert (row.investigation.status, row.investigation.verdict) == ("success", "malicious")
    assert (row.provider, row.model, row.reasoning_level) == ("ollama", "bonsai-2:4b", "high")
    assert (row.prompt_hash, row.tool_hashes, row.model_options) == (
        "p-hash",
        {"get_host": "t-hash"},
        {"num_ctx": 65536},
    )
    assert (row.scenario_id, row.expected, row.failure_kind) == ("S2", {"verdict": "true_positive"}, None)


def test_a_failed_run_is_the_agents_failure_unless_told_otherwise():
    # a run that ends without a verdict is failed, as in production; the DB requires every failed run to say whose
    assert eval_row(response("success", "I think it is fine")).failure_kind == "agent"
    assert eval_row(response("failed", "connection refused"), failure_kind="infra").failure_kind == "infra"


def test_a_run_that_failed_before_its_first_request_still_saves():
    row = eval_row(response("failed", "provider not found", steps=()))
    assert (row.model, row.prompt_hash, row.failure_kind) == (
        "bonsai-2:4b",
        None,
        "agent",
    )  # model from settings
