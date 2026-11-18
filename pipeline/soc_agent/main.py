import logging
from pathlib import Path
from typing import Any

from config import config
from pipeline.soc_agent.agent import Agent
from pipeline.soc_agent.providers.gemini import Gemini
from pipeline.soc_agent.providers.providers import Provider
from pipeline.soc_agent.tools import TOOL_FACTORIES, TOOL_LIST
from pipeline.soc_agent.tools.tools import Tool, tool_map
from vendors import VENDORS
from vendors.base import make_client

GEMINI_MODEL = "models/gemini-3.5-flash-lite"

BASE_AGENT_PATH = Path(__file__).parent

log = logging.getLogger("agent")


class SystemPromptNotFound(Exception): ...


def run_agent(
    question: str, tools: dict[str, Tool], provider: Provider, task_config: dict | None = None, **agent_config
):
    if not task_config:
        task_config = {}

    agent = Agent(tools, provider, task_config, **agent_config)
    reponse = agent.execute(question=question)
    print(reponse.response)


def load_system_prompts(path: Path = BASE_AGENT_PATH) -> set:
    sp_path = path.joinpath("prompts")
    system_prompt_set = {f.stem for f in sp_path.glob("*.md")}
    return system_prompt_set


def get_system_prompt(name: str, path: Path = BASE_AGENT_PATH) -> str:
    sp_path = path.joinpath("prompts", f"{name}.md")
    return sp_path.read_text()


def build_tools(app_config) -> dict:
    tool_list = list(TOOL_LIST)
    for source, vendor_config in app_config.adapters.items():
        actors = vendor_config.get("actors", {})
        if "agent" not in actors:
            continue

        agent_config: dict[str, Any] = actors["agent"]

        if agent_config.get("enabled") == False:
            continue

        vendor_api_cls = VENDORS[source]

        # use actor's own key
        client = make_client(vendor_config["url"], agent_config["api-key"], agent_config.get("timeout", 10))
        vendor_instance = vendor_api_cls(client, vendor_config)
        make_vendor_tools = TOOL_FACTORIES.get(source)

        if callable(make_vendor_tools):
            tool_list += make_vendor_tools(vendor_instance)
        else:
            log.warning(f"no tools are provided for {source}")

    tools = tool_map(tool_list)
    return tools


def soc_investigation(question: str, name: str = "soc_investigation"):
    system_prompt_set = load_system_prompts()
    if name not in system_prompt_set:
        raise SystemPromptNotFound(f"system prompt {name} not found.")

    app_config = config.load_config()
    model_provider_config = app_config.model_providers.get("providers", {})
    agent_tasks_config = app_config.model_providers.get("agent_tasks", {})
    agent_task_config = agent_tasks_config.get(name, {})
    agent_config = app_config.model_providers.get("agent", {})
    gemini_config = dict(model_provider_config.get("gemini", {}))

    system_prompt = get_system_prompt(name)

    tools = build_tools(app_config)
    gemini_model = gemini_config.pop("model", GEMINI_MODEL)
    reasoning = gemini_config.pop("reasoning", GEMINI_MODEL)
    provider = Gemini(gemini_model, system_prompt, reasoning, **gemini_config)
    run_agent(question, tools, provider, agent_task_config, **agent_config)


if __name__ == "__main__":
    soc_investigation("What do you know about host build-01?")
