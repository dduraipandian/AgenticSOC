import pytest
from google.genai import types

from pipeline.soc_agent.agent import Agent
from pipeline.soc_agent.providers.gemini import Gemini
from pipeline.soc_agent.tools.tools import InputSchema, Tool

HOST = {"host": "build-01", "role": "ci-build-server"}
HOST_SCHEMA = InputSchema(properties={"host": {"type": "string"}}, required=["host"])


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
    parts = [types.Part(function_call=types.FunctionCall(name=name, args=args)) for name, args in function_calls]
    return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=parts))])


def answer(text: str) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part(text=text)]))])


def with_usage(response: types.GenerateContentResponse, prompt: int, output: int, thoughts: int = 0):
    response.usage_metadata = types.GenerateContentResponseUsageMetadata(
        prompt_token_count=prompt, candidates_token_count=output, thoughts_token_count=thoughts,
        total_token_count=prompt + output + thoughts)
    return response


def make_agent(responses, get_host=lambda host: HOST, max_iteration: int = 3) -> tuple[Agent, FakeModels]:
    provider = Gemini("models/test", "you are a SOC investigator", "HIGH", max_iteration=max_iteration)
    provider._client = FakeClient(responses)
    tools = {"get_host": Tool("get_host", "Look up an endpoint by hostname.", HOST_SCHEMA, get_host)}
    return Agent(tools, provider), provider._client.models


def function_responses(content: types.Content) -> list[tuple[str, dict]]:
    return [(p.function_response.name, p.function_response.response) for p in content.parts]


# --- critical ---------------------------------------------------------------------------------

def test_tools_are_declared_to_the_model():
    agent, models = make_agent([answer("hi")])

    agent.execute("hello")

    [(_, config)] = models.requests
    [declaration] = config.tools[0].function_declarations
    assert declaration.name == "get_host"
    assert declaration.model_dump(mode="json")["parameters_json_schema"]["required"] == ["host"]


def test_a_tool_call_is_run_and_its_result_goes_back_to_the_model():
    looked_up = []
    agent, models = make_agent([calls(("get_host", {"host": "build-01"})), answer("build-01 is a CI server")],
                               get_host=lambda host: looked_up.append(host) or HOST)

    result = agent.execute("What kind of machine is build-01?")

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
        (0, "model", None, None), (0, "tool", "get_host", None), (1, "model", None, None)]
    assert (end["type"], end["status"]) == ("result", "success")  # the UI stops on this
    assert result.execution_tracing[0]["tool_calls"] == ["get_host"]


def test_every_call_in_one_turn_gets_a_response():
    agent, models = make_agent([calls(("get_host", {"host": "build-01"}), ("get_host", {"host": "laptop-priya"})),
                                answer("done")])

    agent.execute("Compare build-01 and laptop-priya")

    contents, _ = models.requests[1]
    assert [name for name, _ in function_responses(contents[2])] == ["get_host", "get_host"]


def broken_tool(host):
    raise TimeoutError("EDR did not answer")


@pytest.mark.parametrize("tool_name, get_host", [("get_host", broken_tool), ("delete_host", lambda host: HOST)],
                         ids=["tool fails", "unknown tool"])
def test_a_tool_error_goes_back_to_the_model_instead_of_ending_the_investigation(tool_name, get_host):
    agent, models = make_agent([calls((tool_name, {"host": "build-01"})), answer("could not look up build-01")],
                               get_host=get_host)

    result = agent.execute("What kind of machine is build-01?")

    assert (result.status, result.response) == ("success", "could not look up build-01")

    contents, _ = models.requests[1]
    [(name, response)] = function_responses(contents[2])
    assert name == tool_name
    assert "error" in response["untrusted_data"]


def test_the_loop_stops_at_max_iteration():
    agent, models = make_agent([calls(("get_host", {"host": "build-01"}))] * 10, max_iteration=3)

    result = agent.execute("What kind of machine is build-01?")

    assert result.status == "failed"
    assert len(models.requests) == 3
    end = result.execution_tracing[-1]
    assert (end["type"], end["status"]) == ("result", "failed")  # the UI knows the run is over


def test_each_investigation_starts_with_an_empty_history():
    agent, models = make_agent([calls(("get_host", {"host": "build-01"})), answer("first"), answer("second")])

    agent.execute("case 1")
    agent.execute("case 2")

    contents, _ = models.requests[2]
    assert [c.parts[0].text for c in contents] == ["case 2"]  # nothing of case 1 leaks in


def test_tokens_are_summed_over_every_step_and_thinking_counts_as_output():
    agent, _ = make_agent([with_usage(calls(("get_host", {"host": "build-01"})), prompt=100, output=10, thoughts=5),
                           with_usage(answer("build-01 is a CI server"), prompt=150, output=20)])

    result = agent.execute("What kind of machine is build-01?")

    assert (result.prompt_token_count, result.output_token_count) == (250, 35)  # the cost of the investigation


def test_a_failed_model_call_ends_the_investigation_as_failed():
    agent, models = make_agent([calls(("get_host", {"host": "build-01"})), RuntimeError("429 RESOURCE_EXHAUSTED")])

    result = agent.execute("What kind of machine is build-01?")

    assert result.status == "failed"
    assert len(models.requests) == 2  # not retried by the agent, and nothing raised to the caller
    *_, failed_call, end = result.execution_tracing
    assert failed_call["error"] == "429 RESOURCE_EXHAUSTED"  # the failed call is in the trace
    assert (end["type"], end["status"]) == ("result", "failed")
