from pathlib import Path

from config import config
from pipeline.soc_agent.agent import Agent
from pipeline.soc_agent.providers.gemini import Gemini
from pipeline.soc_agent.providers.providers import Provider
from pipeline.soc_agent.tools import TOOL_LIST
from pipeline.soc_agent.tools.tools import Tool, tool_map

GEMINI_MODEL = "models/gemini-3.5-flash-lite"

BASE_AGENT_PATH = Path(__file__).parent


class SystemPromptNotFound(Exception): ...


def run_agent(
    question: str, tools: dict[str, Tool], provider: Provider, task_config: dict | None = None, **agent_config
):
    if not task_config:
        task_config = {}

    agent = Agent(tools, provider, task_config, **agent_config)
    reponse = agent.execute(question=question)
    print(reponse)


def load_system_prompts(path: Path = BASE_AGENT_PATH) -> set:
    sp_path = path.joinpath("prompts")
    system_prompt_set = {f.stem for f in sp_path.glob("*.md")}
    return system_prompt_set


def get_system_prompt(name: str, path: Path = BASE_AGENT_PATH) -> str:
    sp_path = path.joinpath("prompts", f"{name}.md")
    return sp_path.read_text()


def main(name: str, question: str):
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

    tools = tool_map(TOOL_LIST)
    gemini_model = gemini_config.pop("model", GEMINI_MODEL)
    provider = Gemini(gemini_model, system_prompt, "HIGH", **gemini_config)
    run_agent(question, tools, provider, agent_task_config, **agent_config)


if __name__ == "__main__":
    main("soc_investigation", "")
