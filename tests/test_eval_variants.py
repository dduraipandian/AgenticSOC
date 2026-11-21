"""Eval variants: the production task with a few fields changed. What a variant says must be exactly what runs,
and a mistake in variants.yaml must stop a batch before its first run, naming the variant."""

from types import SimpleNamespace

import pytest

from config.config import BASE_CONFIG_PATH, load_agent_task_configs, load_model_configs
from pipeline.soc_agent.evals.variants import DEFAULT_PATH, load_variants, to_task_options
from pipeline.soc_agent.main import build_tools
from pipeline.soc_agent.tasks import TaskConfig, TaskDefinitionError
from tests.test_vendor_tools import VENDOR_CONFIG

BASE = {
    "name": "soc_investigation",
    "prompt_file": "soc_investigation.md",
    "finish_tool": "submit_investigation",
    "tools": ["submit_investigation", "get_host"],
    "provider": "ollama",
    "model": "bonsai-2:4b",
    "reasoning_level": "high",
}
APP = SimpleNamespace(agent_tasks={"soc_investigation": BASE})


def write(tmp_path, text: str):
    path = tmp_path / "variants.yaml"
    path.write_text(text)
    return path


def test_a_variant_is_the_production_task_with_only_its_changes(tmp_path):
    variants = load_variants(
        APP,
        write(
            tmp_path,
            """
production: {task: soc_investigation}
qwen2b: {task: soc_investigation, model: qwen3.5:2b-q4_K_M, reasoning_level: low}
""",
        ),
    )

    assert variants["production"] == TaskConfig(**BASE)
    assert variants["qwen2b"] == TaskConfig(
        **{**BASE, "model": "qwen3.5:2b-q4_K_M", "reasoning_level": "low"}
    )


def test_a_bad_variant_is_rejected_by_name(tmp_path):
    with pytest.raises(TaskDefinitionError, match="typo.*modle"):  # a misspelt field must not be ignored
        load_variants(APP, write(tmp_path, "typo: {task: soc_investigation, modle: x}"))
    with pytest.raises(TaskDefinitionError, match="ghost.*no_such_task"):
        load_variants(APP, write(tmp_path, "ghost: {task: no_such_task}"))


def test_every_variant_in_the_repo_builds():
    """variants.yaml against the real task and model config: provider, prompt file, model and tools all resolve."""
    app = SimpleNamespace(
        adapters={"sentrix-edr": {**VENDOR_CONFIG, "actors": {"agent": {"api-key": "agent-key"}}}},
        model_providers=load_model_configs(BASE_CONFIG_PATH),
        agent_tasks=load_agent_task_configs(BASE_CONFIG_PATH),
    )
    options = to_task_options(load_variants(app, DEFAULT_PATH), app, build_tools(app, pool=None))

    assert options
    assert all(o.model and o.prompt.exists() for o in options.values())
