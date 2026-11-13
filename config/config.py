from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path(__file__).parent

APP_CONFIG = {}

def get_appconfig() -> dict[str, Any]:
    return APP_CONFIG


def load_config():
    config_files =  list(CONFIG_PATH.glob("*.yaml")) +  list(CONFIG_PATH.glob("*.yml"))

    for file_path in config_files:
        with file_path.open("r", encoding="utf-8") as file:
            APP_CONFIG[file_path.stem] = yaml.safe_load(file)


load_config()
