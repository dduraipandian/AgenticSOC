import pytest
from google.genai import types

from pipeline.soc_agent.agent import Agent, AgentRuntimeError
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
        return next(self.responses)


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

    assert result == "build-01 is a CI server"
    assert looked_up == ["build-01"]

    contents, _ = models.requests[1]
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[0].parts[0].text == "What kind of machine is build-01?"
    assert contents[1].parts[0].function_call.name == "get_host"  # the model's turn, kept as it was
    assert function_responses(contents[2]) == [("get_host", {"untrusted_data": HOST})]  # S8: marked as data


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

    assert agent.execute("What kind of machine is build-01?") == "could not look up build-01"

    contents, _ = models.requests[1]
    [(name, response)] = function_responses(contents[2])
    assert name == tool_name
    assert "error" in response["untrusted_data"]


def test_the_loop_stops_at_max_iteration():
    agent, models = make_agent([calls(("get_host", {"host": "build-01"}))] * 10, max_iteration=3)

    with pytest.raises(AgentRuntimeError):
        agent.execute("What kind of machine is build-01?")

    assert len(models.requests) == 3


def test_each_investigation_starts_with_an_empty_history():
    agent, models = make_agent([calls(("get_host", {"host": "build-01"})), answer("first"), answer("second")])

    agent.execute("case 1")
    agent.execute("case 2")

    contents, _ = models.requests[2]
    assert [c.parts[0].text for c in contents] == ["case 2"]  # nothing of case 1 leaks in
