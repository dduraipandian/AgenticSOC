from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings

BASE_CONFIG_PATH = Path(__file__).parent


class SystemSettings(BaseSettings):
    DATABASE_URL: str
    GEMINI_API_KEY: str | None = None


@dataclass(frozen=True)
class AppConfig:
    settings: SystemSettings
    adapters: dict[str, dict]
    model_providers: dict[str, dict]
    agent_tasks: dict[str, dict]


def load_adapter_configs(path: Path):
    adapter_config_path = path.joinpath("adapters")
    config_files = list(adapter_config_path.glob("*.yaml")) + list(adapter_config_path.glob("*.yml"))
    return {f.stem: yaml.safe_load(f.read_text()) for f in config_files}


def load_model_configs(path: Path):
    config_file = path.joinpath("model_providers.yaml")
    return yaml.safe_load(config_file.read_text())


def load_agent_task_configs(path: Path):
    config_file = path.joinpath("agent_tasks.yaml")
    return yaml.safe_load(config_file.read_text())


def load_config(path: Path = BASE_CONFIG_PATH) -> AppConfig:
    return AppConfig(
        settings=SystemSettings(),
        adapters=load_adapter_configs(path),
        model_providers=load_model_configs(path),
        agent_tasks=load_agent_task_configs(path),
    )
