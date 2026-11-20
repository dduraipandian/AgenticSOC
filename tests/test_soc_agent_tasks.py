import hashlib
from types import SimpleNamespace
from unittest.mock import patch

from pipeline.soc_agent.agent import Agent
from pipeline.soc_agent.main import build_tools
from pipeline.soc_agent.tasks import BASE_AGENT_PATH, TaskConfig, TaskOption
from tests.test_vendor_tools import VENDOR_CONFIG

SUBMIT = {"verdict": "benign", "confidence": "low", "rationale": "test", "evidence": {"x": "y"}}

APP_CONFIG = SimpleNamespace(
    # the task's EDR tools are built but never called: the stand-in model submits straight away
    adapters={"sentrix-edr": {**VENDOR_CONFIG, "actors": {"agent": {"api-key": "agent-key"}}}},
    model_providers={
        "agent": {"max_iteration": 2, "force_toolcall": True},
        "providers": {"ollama": {"default_model": "qwen3.5:2b-q4_K_M", "host": "http://localhost:11434"}},
        "models": {"bonsai-2:4b": {"num_ctx": 65536, "num_gpu": 999}},
    },
)


def submit_at_once(self, **kwargs):
    submit_at_once.calls.append(kwargs)
    call = SimpleNamespace(function=SimpleNamespace(name="submit_investigation", arguments=SUBMIT))
    return SimpleNamespace(
        message=SimpleNamespace(tool_calls=[call], content="", thinking=None),
        prompt_eval_count=1,
        eval_count=1,
        done_reason="stop",
        done=True,
    )


# --- critical ---------------------------------------------------------------------------------


def test_a_task_config_is_exactly_what_the_model_runs_with_and_what_the_run_records():
    """A task config (an eval variant has the same shape) decides model, its options, prompt and finish tool."""
    submit_at_once.calls = []
    config = TaskConfig(
        name="soc_investigation",
        prompt_file="case_summary.md",  # any prompt file: shows the config's choice wins
        provider="ollama",
        model="bonsai-2:4b",
        finish_tool="submit_investigation",
        tools=["submit_investigation", "get_host", "get_process_tree", "search_telemetry"],
    )
    task = TaskOption.from_string(config, APP_CONFIG, build_tools(APP_CONFIG, pool=None))

    with patch("pipeline.soc_agent.providers.ollama.Ollama.generate", submit_at_once):
        response = Agent(**task.agent_options).execute("Case 3: test", task)

    [call] = submit_at_once.calls
    assert (call["model"], call["model_options"]) == ("bonsai-2:4b", {"num_ctx": 65536, "num_gpu": 999})
    prompt = BASE_AGENT_PATH.joinpath("prompts", "case_summary.md").read_text()
    assert call["system_prompt"] == prompt

    request = response.execution_tracing[0]
    assert request["model"] == "bonsai-2:4b"
    assert request["prompt_hash"] == hashlib.sha256(prompt.encode()).hexdigest()[:12]
    assert response.status == "success"  # the task's finish tool ends the run
