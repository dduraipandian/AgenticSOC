
from abc import abstractmethod
from typing import Any, Literal

from pipeline.soc_agent.tools.tools import Tool

REASONING_LEVEL = Literal["HIGH", "LOW"]


class Provider:
    def __init__(self,  model: str, system_prompt: str, reasoning_level: REASONING_LEVEL, tools: list[Tool]):
        self._tool_list = tools
        self.model = model
        self.system_prompt = system_prompt
        self.history = []

    @property
    @abstractmethod
    def tools(self):
        raise NotImplementedError()

    @abstractmethod
    def create_client(self):
        raise NotImplementedError()

    @abstractmethod
    def get_tool_definition(self, tool: Tool) -> dict:
        raise NotImplementedError()

    @abstractmethod
    def generate(self):
        raise NotImplementedError()

    @abstractmethod
    def execute(self, question):
        raise NotImplementedError()

    def append_history(self, role: str, content: Any):
        self.history.append(content)

    @abstractmethod
    def get_response_text(self, response) -> str | None:
        raise NotImplementedError()

    @abstractmethod
    def get_function_calls(self, response) -> list:
        raise NotImplementedError()

    @abstractmethod
    def get_function_details(self, function_call) -> tuple[str, dict]:
        raise NotImplementedError()

    @abstractmethod
    def get_function_response(self, function_call, response):
        raise NotImplementedError()
