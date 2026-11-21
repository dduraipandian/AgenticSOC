"""
variant names are from production task from config/agent_tasks.yaml and it is recorded. agent options are not
recorded separately. it comes from model_providers.
"""

from pathlib import Path

import yaml
from pydantic import ValidationError

from config import config
from pipeline.soc_agent.tasks import TaskConfig, TaskDefinitionError, TaskOption
from pipeline.soc_agent.tools.tools import Tool

DEFAULT_PATH = Path(__file__).parent / "variants.yaml"


def load_variants(app_config: config.AppConfig, path: Path = DEFAULT_PATH) -> dict[str, TaskConfig]:
    variants = {}
    for name, fields in (yaml.safe_load(path.read_text()) or {}).items():
        fields = dict(fields or {})
        task = fields.pop("task", None)
        if task not in app_config.agent_tasks:
            raise TaskDefinitionError(
                f"variant {name!r}: task {task!r} is not in agent_tasks.yaml; known: {sorted(app_config.agent_tasks)}"
            )
        try:
            variants[name] = TaskConfig(**{**app_config.agent_tasks[task], **fields})
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
            raise TaskDefinitionError(f"variant {name!r}: {problems}") from exc
    return variants


def to_task_options(
    variants: dict[str, TaskConfig], app_config: config.AppConfig, available_tools: dict[str, Tool]
) -> dict[str, TaskOption]:
    options = {}
    for name, task_config in variants.items():
        try:
            options[name] = TaskOption.from_string(task_config, app_config, available_tools)
        except TaskDefinitionError as exc:
            raise TaskDefinitionError(f"variant {name!r}: {exc}") from exc
    return options
