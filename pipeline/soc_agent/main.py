from config import config
from pipeline.soc_agent.agent import Agent
from pipeline.soc_agent.providers.gemini import Gemini
from pipeline.soc_agent.tools.tools import ToolFunction

GEMINI_MODEL= "models/gemini-3.5-flash-lite"


def run_agent(question, tools, provider, **agent_config):
    agent = Agent(tools, provider, **agent_config)
    reponse = agent.execute(question=question)
    print(reponse)


def main():
    app_config = config.load_config()
    model_provider_config = app_config.model_providers.get("providers", {})
    agent_config = app_config.model_providers.get("agent", {})
    gemini_config = dict(model_provider_config.get("gemini", {}))
    system_prompt = ""
    question="hello"

    tools = ToolFunction.get_tools()
    gemini_model = gemini_config.pop("model", GEMINI_MODEL)
    provider = Gemini(gemini_model, system_prompt, "HIGH", **gemini_config)
    run_agent(question, tools, provider, **agent_config)

if __name__ == "__main__":
    main()
