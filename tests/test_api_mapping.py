from api.mapping import agent_steps, step_detail

AT = 1795058400000.0  # epoch ms
RUN = "01a1ff00-0000-7000-8000-000000000000"

MODEL = {
    "step": 0,
    "seq": 0,
    "type": "model",
    "at": AT,
    "duration_ms": 900,
    "prompt_tokens": 100,
    "output_tokens": 10,
    "thoughts_tokens": 5,
    "tool_calls": ["get_host"],
    "text": "checking the host",
    "thought": "the alert names build-01, so start with the host",
    "error": None,
}
TOOL = {
    "step": 0,
    "seq": 1,
    "type": "tool",
    "at": AT + 50,
    "duration_ms": 40,
    "name": "get_host",
    "args": {"host": "build-01"},
    "result": {"found": True, "host": "build-01"},
    "error": None,
}
END = {"seq": 2, "type": "result", "at": AT + 60, "status": "success", "duration_ms": 1000}


def test_the_step_list_stays_small_and_the_detail_has_the_text_and_result():
    model, tool = agent_steps([MODEL, TOOL, END])  # the result event is not a step

    assert (model.summary, model.tokens_out) == ("called get_host", 15)
    assert (tool.summary, tool.args) == ("get_host(host=build-01)", {"host": "build-01"})
    # the large fields are fetched one step at a time
    assert not {"text", "thought"} & model.model_dump().keys() and "result" not in tool.model_dump()

    detail = step_detail(RUN, TOOL)
    assert (detail.kind, detail.name, detail.args, detail.result) == (
        "tool",
        "get_host",
        {"host": "build-01"},
        {"found": True, "host": "build-01"},
    )
    model_detail = step_detail(RUN, MODEL)
    assert (model_detail.text, model_detail.thought) == (
        "checking the host",
        "the alert names build-01, so start with the host",
    )


def test_only_model_and_tool_events_are_steps_with_detail():
    assert step_detail(RUN, END) is None  # its index exists in the trace, but it is not a step


def test_a_run_saved_before_text_and_result_were_traced_has_them_as_null():
    old = {k: v for k, v in TOOL.items() if k != "result"}

    assert step_detail(RUN, old).result is None
    assert agent_steps([old])[0].summary == "get_host(host=build-01)"


def test_a_reminded_text_answer_is_marked_on_its_model_step():
    text_answer = MODEL | {"tool_calls": [], "text": "it looks benign"}
    reminder = {
        "step": 0,
        "seq": 1,
        "type": "reminder",
        "at": AT + 1,
        "text": "it looks benign",
        "error": None,
    }

    [model] = agent_steps([text_answer, reminder])

    assert model.summary == "answered without a tool call; reminded"
