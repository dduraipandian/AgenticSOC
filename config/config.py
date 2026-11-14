from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings

CONFIG_PATH = Path(__file__).parent

class SystemSettings(BaseSettings):
    database_url: str


@dataclass(frozen=True)
class AppConfig:
    settings: SystemSettings
    adapters: dict[str, dict]


def load_adapter_configs(path: Path):
    config_files =  list(path.glob("*.yaml")) +  list(path.glob("*.yml"))
    return {f.stem: yaml.safe_load(f.read_text()) for f in config_files}


def load_config(path: Path = CONFIG_PATH) -> AppConfig:
    return AppConfig(settings=SystemSettings(), adapters=load_adapter_configs(path))
