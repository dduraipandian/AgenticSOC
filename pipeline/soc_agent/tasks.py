from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field

from config import config
from pipeline.soc_agent.providers import PROVIDERS
from pipeline.soc_agent.providers.providers import Provider
from pipeline.soc_agent.tools.tools import Tool

BASE_AGENT_PATH = Path(__file__).parent


class UnknownTask(Exception): ...


class TaskDefinitionError(Exception): ...


class TaskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    prompt_file: str
    provider: str
    finish_tool: str
    model: str | None = None
    reasoning_level: Literal["low", "medium", "high"] | None = None
    trace: bool = True
    tools: list[str] = Field(min_length=1)


@dataclass()
class TaskOption:
    name: str
    model: str
    prompt: Path
    provider: Provider
    finish_tool: str
    trace: bool = True

    reasoning_level: str | None = None
    tools: dict[str, Tool] = field(default_factory=dict)

    model_options: dict = field(default_factory=dict)
    agent_options: dict = field(default_factory=dict)
    provider_config: dict = field(default_factory=dict)

    @classmethod
    def get_provider(cls, name, app_config):
        model_provider_config = app_config.model_providers.get("providers", {})
        provider_config = dict(model_provider_config.get(name, {}))

        provider_cls = PROVIDERS[name]
        provider = provider_cls(provider_config)

        return provider

    @classmethod
    def from_string(
        cls, task_config: TaskConfig, app_config: config.AppConfig, available_tools: dict[str, Tool]
    ) -> Self:
        name = task_config.name
        model = task_config.model
        reasoning_level = task_config.reasoning_level
        finish_tool = task_config.finish_tool

        tools = select_tools(task_config, available_tools)

        prompt = Path(task_config.prompt_file)
        if not prompt.is_absolute():
            prompt = BASE_AGENT_PATH.joinpath("prompts", prompt)

        if not prompt.exists():
            raise TaskDefinitionError(f"Prompt file {prompt!r} does not exist.")

        if task_config.provider not in PROVIDERS:
            raise TaskDefinitionError(
                f"unknown provider {task_config.provider!r}; known: {sorted(PROVIDERS)}"
            )

        provider = cls.get_provider(task_config.provider, app_config)

        model_providers = app_config.model_providers
        provider_config = dict(app_config.model_providers.get("providers", {}).get(task_config.provider, {}))

        model = model or provider_config.get("default_model")

        if not model:
            raise TaskDefinitionError(f"model is not provided for task {name!r}")

        model_options = model_providers.get("models", {}).get(model, {})
        agent_options = model_providers.get("agent", {})

        return cls(
            name=name,
            model=model,
            reasoning_level=reasoning_level,
            finish_tool=finish_tool,
            tools=tools,
            prompt=prompt,
            provider=provider,
            model_options=model_options,
            agent_options=agent_options,
        )

    def get_prompt_text(self):
        return self.prompt.read_text()


def select_tools(task: TaskConfig, available: dict[str, Tool]) -> dict[str, Tool]:
    if task.finish_tool not in task.tools:
        raise TaskDefinitionError(f"{task.name}: finish tool {task.finish_tool!r} is not in its tool list")

    missing = [t for t in task.tools if t not in available]
    if missing:
        raise TaskDefinitionError(f"{task.name}: tools not available: {missing}; built: {sorted(available)}")

    return {t: available[t] for t in task.tools}


def load_domain_tasks(available_tools: dict[str, Tool]) -> dict[str, TaskOption]:
    app_config = config.load_config()
    task_options = {}
    for task_name, task_config in app_config.agent_tasks.items():
        task_options[task_name] = TaskOption.from_string(
            TaskConfig(**task_config), app_config, available_tools
        )

    return task_options
