from pipeline.soc_agent.agent import Agent
from pipeline.soc_agent.providers.gemini import Gemini
from pipeline.soc_agent.tools.tools import ToolFunction

from config import config


def main():
    app_config = config.load_config()

    system_prompt = ""
    GEMINI_MODEL= "models/gemini-3.5-flash-lite"
    tools = ToolFunction.get_tools()
    provider = Gemini(GEMINI_MODEL, system_prompt, "HIGH", list(tools.values()))
    agent = Agent(tools, provider)
    reponse = agent.execute(question="hello")
    print(reponse)


if __name__ == "__main__":
    main()
