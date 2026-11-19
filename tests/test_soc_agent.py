from itertools import chain, repeat

import pytest
from google.genai import types
from pydantic import Field

from pipeline.soc_agent.agent import Agent
from pipeline.soc_agent.providers.gemini import Gemini
from pipeline.soc_agent.tools.tools import Tool, ToolArgs

HOST = {"host": "build-01", "role": "ci-build-server"}
SYSTEM_PROMPT = "you are a SOC investigator"


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


def make_agent(
    responses, get_host=lambda args: HOST, max_iteration: int = 3, **agent_options
) -> tuple[Agent, FakeModels]:
    provider = Gemini({"model": "models/test"})
    provider._client = FakeClient(responses)
    tools = {"get_host": Tool("get_host", "Look up an endpoint by hostname.", HostArgs, get_host)}
    return Agent(tools, provider, max_iteration=max_iteration, **agent_options), provider._client.models


def function_responses(content: types.Content) -> list[tuple[str, dict]]:
    return [(p.function_response.name, p.function_response.response) for p in content.parts]


# --- critical ---------------------------------------------------------------------------------


def test_tools_are_declared_to_the_model():
    agent, models = make_agent([answer("hi")])

    agent.execute(SYSTEM_PROMPT, "hello")

    [(_, config)] = models.requests
    [declaration] = config.tools[0].function_declarations
    assert declaration.name == "get_host"
    assert declaration.model_dump(mode="json")["parameters_json_schema"]["required"] == ["host"]


def test_a_tool_call_is_run_and_its_result_goes_back_to_the_model():
    looked_up = []
    agent, models = make_agent(
        [calls(("get_host", {"host": "build-01"})), answer("build-01 is a CI server")],
        get_host=lambda args: looked_up.append(args.host) or HOST,
    )

    result = agent.execute(SYSTEM_PROMPT, "What kind of machine is build-01?")

    assert (result.status, result.response) == ("success", "build-01 is a CI server")
    assert looked_up == ["build-01"]

    contents, _ = models.requests[1]
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[0].parts[0].text == "What kind of machine is build-01?"
    assert contents[1].parts[0].function_call.name == "get_host"  # the model's turn, kept as it was
    assert function_responses(contents[2]) == [("get_host", {"untrusted_data": HOST})]  # S8: marked as data

    # flat events, in order: what the UI draws as model -> tool -> model
    *steps, end = result.execution_tracing
    assert [(e["step"], e["type"], e.get("name"), e["error"]) for e in steps] == [
        (0, "model", None, None),
        (0, "tool", "get_host", None),
        (1, "model", None, None),
    ]
    assert (end["type"], end["status"]) == ("result", "success")  # the UI stops on this
    assert result.execution_tracing[0]["tool_calls"] == ["get_host"]


def test_every_call_in_one_turn_gets_a_response():
    agent, models = make_agent(
        [calls(("get_host", {"host": "build-01"}), ("get_host", {"host": "laptop-priya"})), answer("done")]
    )

    agent.execute(SYSTEM_PROMPT, "Compare build-01 and laptop-priya")

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
    agent, models = make_agent(
        [calls((tool_name, {"host": "build-01"})), answer("could not look up build-01")], get_host=get_host
    )

    result = agent.execute(SYSTEM_PROMPT, "What kind of machine is build-01?")

    assert (result.status, result.response) == ("success", "could not look up build-01")

    contents, _ = models.requests[1]
    [(name, response)] = function_responses(contents[2])
    assert name == tool_name
    assert "error" in response["untrusted_data"]


def test_the_loop_stops_at_max_iteration():
    agent, models = make_agent([calls(("get_host", {"host": "build-01"}))] * 10, max_iteration=3)

    result = agent.execute(SYSTEM_PROMPT, "What kind of machine is build-01?")

    assert result.status == "failed"
    assert len(models.requests) == 3
    end = result.execution_tracing[-1]
    assert (end["type"], end["status"]) == ("result", "failed")  # the UI knows the run is over


def test_each_investigation_starts_with_an_empty_history():
    agent, models = make_agent([calls(("get_host", {"host": "build-01"})), answer("first"), answer("second")])

    agent.execute(SYSTEM_PROMPT, "case 1")
    agent.execute(SYSTEM_PROMPT, "case 2")

    contents, _ = models.requests[2]
    assert [c.parts[0].text for c in contents] == ["case 2"]  # nothing of case 1 leaks in


def test_tokens_are_summed_over_every_step_and_thinking_counts_as_output():
    agent, _ = make_agent(
        [
            with_usage(calls(("get_host", {"host": "build-01"})), prompt=100, output=10, thoughts=5),
            with_usage(answer("build-01 is a CI server"), prompt=150, output=20),
        ]
    )

    result = agent.execute(SYSTEM_PROMPT, "What kind of machine is build-01?")

    assert (result.prompt_token_count, result.output_token_count) == (
        250,
        35,
    )  # the cost of the investigation


def test_a_failed_model_call_ends_the_investigation_as_failed():
    agent, models = make_agent(
        [calls(("get_host", {"host": "build-01"})), RuntimeError("429 RESOURCE_EXHAUSTED")]
    )

    result = agent.execute(SYSTEM_PROMPT, "What kind of machine is build-01?")

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
    agent, _ = make_agent([response])

    result = agent.execute(SYSTEM_PROMPT, "What kind of machine is build-01?")

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
    agent, models = make_agent(
        [calls(("get_host", args)), answer("let me fix that")],
        get_host=lambda args: looked_up.append(args.host) or HOST,
    )

    agent.execute(SYSTEM_PROMPT, "What kind of machine is build-01?")

    assert looked_up == []
    contents, _ = models.requests[1]
    [(_, response)] = function_responses(contents[2])
    assert problem in response["untrusted_data"]["error"]  # specific enough for the model to correct itself


def test_tool_calls_beyond_the_cap_in_one_turn_are_refused():
    looked_up = []
    agent, models = make_agent(
        [calls(*[("get_host", {"host": f"host-{i}"}) for i in range(3)]), answer("done")],
        get_host=lambda args: looked_up.append(args.host) or HOST,
        max_tool_calls_per_turn=2,
    )

    agent.execute(SYSTEM_PROMPT, "Look up three hosts")

    assert looked_up == ["host-0", "host-1"]
    contents, _ = models.requests[1]
    *_, (_, refused) = function_responses(contents[2])  # every call still gets a response
    assert "refused" in refused["untrusted_data"]["error"]


def test_a_text_answer_under_force_toolcall_gets_the_reminder_and_the_run_goes_on():
    agent, models = make_agent(
        [answer("it looks benign"), calls(("get_host", {"host": "build-01"}))],
        force_toolcall=True,
        toolcall_reminder_text="Reply only with tool calls. When you are done, call {finish_tool}.",
    )

    result = agent.execute(
        system_prompt=SYSTEM_PROMPT, question="Investigate build-01", finish_tool="get_host"
    )

    assert result.status == "success"  # the run went on to the finish tool instead of ending on the text
    contents, _ = models.requests[1]
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[1].parts[0].text == "it looks benign"  # the model's turn, kept
    assert contents[2].parts[0].text == "Reply only with tool calls. When you are done, call get_host."
    assert [e["type"] for e in result.execution_tracing][:2] == ["model", "reminder"]


def test_the_investigation_stops_when_its_time_budget_is_spent():
    clock = chain([0, 1], repeat(301))  # started, before step 0, then 301 from step 1 on
    agent, models = make_agent(
        [calls(("get_host", {"host": "build-01"})), answer("never asked")],
        clock=lambda: next(clock),
        max_seconds=300,
    )

    result = agent.execute(SYSTEM_PROMPT, "What kind of machine is build-01?")

    assert result.status == "failed"
    assert "300s" in result.response  # says which limit ended it, without pinning the wording
    assert len(models.requests) == 1
    assert result.execution_tracing[-1]["type"] == "result"
