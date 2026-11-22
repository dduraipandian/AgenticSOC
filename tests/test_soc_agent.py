from dataclasses import dataclass
from itertools import chain, repeat
from pathlib import Path

import pytest
from google.genai import types
from pydantic import Field

from pipeline.soc_agent.agent import Agent
from pipeline.soc_agent.providers.gemini import Gemini
from pipeline.soc_agent.tasks import TaskOption
from pipeline.soc_agent.tools.default import SUBMIT_INVESTIGATION
from pipeline.soc_agent.tools.tools import Tool, ToolArgs

HOST = {"host": "build-01", "role": "ci-build-server"}
SYSTEM_PROMPT = "you are a SOC investigator"
MODEL = "models/test"


class HostArgs(ToolArgs):
    host: str = Field(description="hostname, e.g. build-01")


class FakeModels:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []  # (contents, config) of every call, as the model saw them

    def generate_content(self, model, contents, config):
        self.requests.append((list(contents), config))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


class FakeClient:
    """Stands in for genai.Client: client.models.generate_content gives the next scripted response."""

    def __init__(self, responses):
        self.models = FakeModels(responses)


def calls(*function_calls: tuple[str, dict]) -> types.GenerateContentResponse:
    parts = [
        types.Part(function_call=types.FunctionCall(name=name, args=args)) for name, args in function_calls
    ]
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts))]
    )


def answer(text: str) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part(text=text)]))]
    )


def with_usage(response: types.GenerateContentResponse, prompt: int, output: int, thoughts: int = 0):
    response.usage_metadata = types.GenerateContentResponseUsageMetadata(
        prompt_token_count=prompt,
        candidates_token_count=output,
        thoughts_token_count=thoughts,
        total_token_count=prompt + output + thoughts,
    )
    return response


@dataclass
class InlinePromptTask(TaskOption):
    """A task whose system prompt is given as text instead of read from a prompts/ file."""

    prompt_text: str = SYSTEM_PROMPT

    def get_prompt_text(self):
        return self.prompt_text


def make_agent(
    responses,
    get_host=lambda args: HOST,
    max_iteration: int = 3,
    finish_tool=None,
    extra_tools: dict | None = None,
    **agent_options,
):
    """An agent on a scripted Gemini, and run(question) to execute the test task with it."""
    provider = Gemini({"default_model": MODEL})
    provider._client = FakeClient(responses)
    task = InlinePromptTask(
        name="test",
        model=MODEL,
        prompt=Path("inline"),
        provider=provider,
        finish_tool=finish_tool,
        tools={"get_host": Tool("get_host", "Look up an endpoint by hostname.", HostArgs, get_host)}
        | (extra_tools or {}),
    )
    agent = Agent(max_iteration=max_iteration, **agent_options)
    return (lambda question: agent.execute(question, task)), provider._client.models


def function_responses(content: types.Content) -> list[tuple[str, dict]]:
    return [(p.function_response.name, p.function_response.response) for p in content.parts]


# --- critical ---------------------------------------------------------------------------------


def test_tools_are_declared_to_the_model():
    run, models = make_agent([answer("hi")])

    run("hello")

    [(_, config)] = models.requests
    [declaration] = config.tools[0].function_declarations
    assert declaration.name == "get_host"
    assert declaration.model_dump(mode="json")["parameters_json_schema"]["required"] == ["host"]


def test_a_tool_call_is_run_and_its_result_goes_back_to_the_model():
    looked_up = []
    run, models = make_agent(
        [calls(("get_host", {"host": "build-01"})), answer("build-01 is a CI server")],
        get_host=lambda args: looked_up.append(args.host) or HOST,
    )

    result = run("What kind of machine is build-01?")

    assert (result.status, result.response) == ("success", "build-01 is a CI server")
    assert looked_up == ["build-01"]

    contents, _ = models.requests[1]
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[0].parts[0].text == "What kind of machine is build-01?"
    assert contents[1].parts[0].function_call.name == "get_host"  # the model's turn, kept as it was
    assert function_responses(contents[2]) == [
        ("get_host", {"untrusted_data": HOST, "tool_step": 2})
    ]  # S8: marked as data

    # flat events, in order: what the UI draws as request -> model -> tool -> model
    request, *steps, end = result.execution_tracing
    assert (request["seq"], request["type"], request["text"]) == (
        0,
        "request",
        "What kind of machine is build-01?",
    )
    assert [(e["step"], e["type"], e.get("name"), e["error"]) for e in steps] == [
        (1, "model", None, None),
        (2, "tool", "get_host", None),  # every model turn and every tool call gets its own step
        (3, "model", None, None),
    ]
    assert (end["type"], end["status"]) == ("result", "success")  # the UI stops on this
    assert steps[0]["tool_calls"] == ["get_host"]

    # what the UI shows inside each step: the model's words and the tool's answer
    first_model, tool, last_model = steps
    assert first_model["text"] is None  # a turn of only tool calls has no text
    assert (tool["args"], tool["result"]) == (
        {"host": "build-01"},
        HOST,
    )  # unwrapped, as the tool returned it
    assert last_model["text"] == "build-01 is a CI server"


def test_the_thinking_summary_is_traced_apart_from_the_message_and_sent_back_to_the_model():
    thinking = types.Part(text="the alert names build-01, so look it up", thought=True)
    message = types.Part(text="Looking up build-01.")
    call = types.Part(function_call=types.FunctionCall(name="get_host", args={"host": "build-01"}))
    turn = types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[thinking, message, call]))]
    )
    run, models = make_agent([turn, answer("build-01 is a CI server")])

    result = run("What kind of machine is build-01?")

    _request, first_model, _tool, last_model, _end = result.execution_tracing
    assert first_model["thought"] == "the alert names build-01, so look it up"
    assert first_model["text"] == "Looking up build-01."  # the thinking never ends up in the message
    assert last_model["thought"] is None  # a turn without thinking has none

    contents, _ = models.requests[1]
    assert contents[1].parts[0].thought  # Gemini gets its own thought parts back unchanged


def test_every_call_in_one_turn_gets_a_response():
    run, models = make_agent(
        [calls(("get_host", {"host": "build-01"}), ("get_host", {"host": "laptop-priya"})), answer("done")]
    )

    run("Compare build-01 and laptop-priya")

    contents, _ = models.requests[1]
    assert [name for name, _ in function_responses(contents[2])] == ["get_host", "get_host"]


def broken_tool(args):
    raise TimeoutError("EDR did not answer")


@pytest.mark.parametrize(
    "tool_name, get_host",
    [("get_host", broken_tool), ("delete_host", lambda args: HOST)],
    ids=["tool fails", "unknown tool"],
)
def test_a_tool_error_goes_back_to_the_model_instead_of_ending_the_investigation(tool_name, get_host):
    run, models = make_agent(
        [calls((tool_name, {"host": "build-01"})), answer("could not look up build-01")], get_host=get_host
    )

    result = run("What kind of machine is build-01?")

    assert (result.status, result.response) == ("success", "could not look up build-01")

    contents, _ = models.requests[1]
    [(name, response)] = function_responses(contents[2])
    assert name == tool_name
    assert "error" in response["untrusted_data"]


def test_the_loop_stops_at_max_iteration():
    run, models = make_agent([calls(("get_host", {"host": "build-01"}))] * 10, max_iteration=3)

    result = run("What kind of machine is build-01?")

    assert result.status == "failed"
    assert len(models.requests) == 3
    end = result.execution_tracing[-1]
    assert (end["type"], end["status"]) == ("result", "failed")  # the UI knows the run is over


def test_each_investigation_starts_with_an_empty_history():
    run, models = make_agent([calls(("get_host", {"host": "build-01"})), answer("first"), answer("second")])

    run("case 1")
    run("case 2")

    contents, _ = models.requests[2]
    assert [c.parts[0].text for c in contents] == ["case 2"]  # nothing of case 1 leaks in


def test_tokens_are_summed_over_every_step_and_thinking_counts_as_output():
    run, _ = make_agent(
        [
            with_usage(calls(("get_host", {"host": "build-01"})), prompt=100, output=10, thoughts=5),
            with_usage(answer("build-01 is a CI server"), prompt=150, output=20),
        ]
    )

    result = run("What kind of machine is build-01?")

    assert (result.prompt_token_count, result.output_token_count) == (
        250,
        35,
    )  # the cost of the investigation


def test_a_failed_model_call_ends_the_investigation_as_failed():
    run, models = make_agent(
        [calls(("get_host", {"host": "build-01"})), RuntimeError("429 RESOURCE_EXHAUSTED")]
    )

    result = run("What kind of machine is build-01?")

    assert result.status == "failed"
    assert len(models.requests) == 2  # not retried by the agent, and nothing raised to the caller
    *_, failed_call, end = result.execution_tracing
    assert failed_call["error"] == "429 RESOURCE_EXHAUSTED"  # the failed call is in the trace
    assert (end["type"], end["status"]) == ("result", "failed")


def model_turn(finish_reason: str, *parts: types.Part) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                finish_reason=finish_reason, content=types.Content(role="model", parts=list(parts))
            )
        ]
    )


@pytest.mark.parametrize(
    "response",
    [
        types.GenerateContentResponse(
            prompt_feedback=types.GenerateContentResponsePromptFeedback(block_reason="SAFETY")
        ),
        model_turn("SAFETY"),
        model_turn("MAX_TOKENS", types.Part(text="build-01 is")),  # a half verdict must not pass as an answer
        model_turn("STOP"),
    ],
    ids=["prompt blocked", "answer blocked", "cut off", "empty answer"],
)
def test_a_blocked_cut_off_or_empty_answer_fails_the_investigation(response):
    run, _ = make_agent([response])

    result = run("What kind of machine is build-01?")

    assert result.status == "failed"
    *_, end = result.execution_tracing
    assert (end["type"], end["status"]) == ("result", "failed")


@pytest.mark.parametrize(
    "args, problem",
    [
        ({"host": 42}, "host: Input should be a valid string"),
        ({}, "host: Field required"),
        ({"host": "build-01", "path": "/etc/shadow"}, "path: Extra inputs are not permitted"),
    ],
    ids=["wrong type", "missing", "extra field"],
)
def test_arguments_that_break_the_schema_never_reach_the_tool(args, problem):
    looked_up = []
    run, models = make_agent(
        [calls(("get_host", args)), answer("let me fix that")],
        get_host=lambda args: looked_up.append(args.host) or HOST,
    )

    run("What kind of machine is build-01?")

    assert looked_up == []
    contents, _ = models.requests[1]
    [(_, response)] = function_responses(contents[2])
    assert problem in response["untrusted_data"]["error"]  # specific enough for the model to correct itself


def test_tool_calls_beyond_the_cap_in_one_turn_are_refused():
    looked_up = []
    run, models = make_agent(
        [calls(*[("get_host", {"host": f"host-{i}"}) for i in range(3)]), answer("done")],
        get_host=lambda args: looked_up.append(args.host) or HOST,
        max_tool_calls_per_turn=2,
    )

    run("Look up three hosts")

    assert looked_up == ["host-0", "host-1"]
    contents, _ = models.requests[1]
    *_, (_, refused) = function_responses(contents[2])  # every call still gets a response
    assert "refused" in refused["untrusted_data"]["error"]


def test_a_text_answer_under_force_toolcall_gets_the_reminder_and_the_run_goes_on():
    run, models = make_agent(
        [answer("it looks benign"), calls(("get_host", {"host": "build-01"}))],
        finish_tool="get_host",
        force_toolcall=True,
        toolcall_reminder_text="Reply only with tool calls. When you are done, call {finish_tool}.",
    )

    result = run("Investigate build-01")

    assert result.status == "success"  # the run went on to the finish tool instead of ending on the text
    contents, _ = models.requests[1]
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[1].parts[0].text == "it looks benign"  # the model's turn, kept
    assert contents[2].parts[0].text == "Reply only with tool calls. When you are done, call get_host."
    assert [e["type"] for e in result.execution_tracing][:3] == ["request", "model", "reminder"]


def test_the_investigation_stops_when_its_time_budget_is_spent():
    clock = chain([0, 1], repeat(301))  # started, before step 0, then 301 from step 1 on
    run, models = make_agent(
        [calls(("get_host", {"host": "build-01"})), answer("never asked")],
        clock=lambda: next(clock),
        max_seconds=300,
    )

    result = run("What kind of machine is build-01?")

    assert result.status == "failed"
    assert "300s" in result.response  # says which limit ended it, without pinning the wording
    assert len(models.requests) == 1
    assert result.execution_tracing[-1]["type"] == "result"


def submit(*facts: dict) -> types.GenerateContentResponse:
    args = {"verdict": "benign", "confidence": "high", "rationale": "a CI server", "evidence": list(facts)}
    return calls(("submit_investigation", args))


def test_evidence_must_cite_a_tool_result_and_is_verified_only_when_its_value_is_there():
    run, models = make_agent(
        [
            calls(("get_host", {"host": "build-01"})),  # model step 1, tool_step 2
            submit({"fact": "role", "value": "ci-build-server", "step": 9}),  # no tool_step 9: refused
            submit(
                {"fact": "role", "value": "ci-build-server", "step": 2},
                {"fact": "host", "value": "prod-db-01", "step": 2},  # a real step, but not its value
            ),
        ],
        max_iteration=5,
        finish_tool="submit_investigation",
        extra_tools={"submit_investigation": SUBMIT_INVESTIGATION},
    )

    result = run("Is build-01 compromised?")

    contents, _ = models.requests[2]
    [(_, refused)] = function_responses(contents[-1])
    assert "cites tool_step 9, which is not a tool result" in refused["untrusted_data"]["error"]
    assert result.status == "success"
    assert [(f["value"], f["verified"]) for f in result.response["evidence"]] == [
        ("ci-build-server", True),
        ("prod-db-01", False),
    ]
