"""Infra failures are not charged to the model: a run that failed because Ollama, the EDR or the network was down
is kept but left out of pass rates, and the batch waits for the lab instead of burning attempts on it.
The error texts below are real ones from the lab's pipeline.investigations."""

import httpx
import pytest

from pipeline.soc_agent.db import InvestigationRow
from pipeline.soc_agent.evals.infra import (
    LabUnavailable,
    classify_failure,
    ollama_problems,
    wait_until_healthy,
)


def failed(error: str, tool_errors=()) -> InvestigationRow:
    steps = [{"type": "tool", "name": "get_process_tree", "error": e} for e in tool_errors]
    return InvestigationRow(3, 1, "failed", None, 10, steps, [], error=error)


@pytest.mark.parametrize(
    "error, kind",
    [
        ("failed to call generate content step 1 error ReadTimeout('timed out')", "infra"),
        (
            "failed to call generate content step 5 error RemoteProtocolError('Server disconnected without sending a response.')",
            "infra",
        ),
        (
            "failed to call generate content step 0 error ResponseError(\"model 'lfm3:1.7b' not found\")",
            "infra",
        ),
        (
            "failed to call generate content step 0 error ServerError('503 UNAVAILABLE. The model is overloaded.')",
            "infra",
        ),
        # the model's own doing: malformed tool-call output, too many steps, too slow overall
        (
            "failed to call generate content step 13 error ResponseError('XML syntax error on line 15: element <function> closed by </parameter>')",
            "agent",
        ),
        ("Agent step limit reached.", "agent"),
        ("investigation exceeded 300s time limit", "agent"),
        ("finished without a verdict: 'I think it is fine'", "agent"),
    ],
)
def test_failures_are_classified(error, kind):
    assert classify_failure(failed(error)) == kind


def test_a_vendor_connection_error_in_a_tool_makes_the_run_infra():
    # the tool error is str(exc): httpx's ReadTimeout is just "timed out"
    assert classify_failure(failed("Agent step limit reached.", tool_errors=["timed out"])) == "infra"
    assert (
        classify_failure(failed("Agent step limit reached.", tool_errors=["invalid arguments: limit"]))
        == "agent"
    )


def test_a_successful_run_has_no_failure_kind():
    assert classify_failure(InvestigationRow(3, 1, "success", None, 10, [], [], verdict="benign")) is None


def test_a_model_missing_from_ollama_is_named():
    def ollama(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.40.16"})
        return httpx.Response(
            200, json={"models": [{"name": "bonsai-2:4b"}, {"name": "gemma4:e2b-it-q4_K_M"}]}
        )

    http = httpx.Client(transport=httpx.MockTransport(ollama))
    assert ollama_problems(http, "http://ollama", "bonsai-2:4b") == []
    assert ollama_problems(http, "http://ollama", "qwen3.5:2b-q4_K_M") == [
        "ollama at http://ollama has no model 'qwen3.5:2b-q4_K_M' (ollama pull qwen3.5:2b-q4_K_M)"
    ]


def test_the_batch_waits_for_the_lab_then_stops_with_what_is_down():
    now = [0.0]
    checks = iter([["edr: connection refused"], []])
    wait_until_healthy(
        lambda: next(checks),
        timeout_s=300,
        sleep=lambda s: now.__setitem__(0, now[0] + s),
        clock=lambda: now[0],
    )
    assert now[0] == 15  # came back on the second check

    with pytest.raises(LabUnavailable, match="edr: connection refused"):
        wait_until_healthy(
            lambda: ["edr: connection refused"],
            timeout_s=60,
            sleep=lambda s: now.__setitem__(0, now[0] + s),
            clock=lambda: now[0],
        )
