from abc import abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

from pipeline.soc_agent.tools.tools import Tool

REASONING_LEVEL = Literal["HIGH", "LOW", "XHIGH", "MEDIUM"]

DEFAULT_REASONING_LEVEL = "MEDIUM"


@dataclass(frozen=True, slots=True)
class TokenUsage:
    prompt_token_count: int = 0
    output_token_count: int = 0
    thoughts_token_count: int = 0
    total_token_count: int = 0


class Provider:
    def __init__(self, config):
        self.provider_config = config

    @abstractmethod
    def get_tools(self, tools: list[Tool]):
        raise NotImplementedError()

    @abstractmethod
    def create_client(self):
        raise NotImplementedError()

    @abstractmethod
    def get_tool_definition(self, tool: Tool) -> dict:
        raise NotImplementedError()

    @abstractmethod
    def generate(
        self,
        model: str | None,
        system_prompt: str | None,
        reasoning_level: str | None,
        history: list,
        tools: Any,
        **kwargs: dict[str, Any],
    ):
        raise NotImplementedError()

    @abstractmethod
    def execute(self, question):
        raise NotImplementedError()

    @abstractmethod
    def append_history(self, history: list, role: str, content: Any):
        raise NotImplementedError()

    @abstractmethod
    def append_response_to_history(self, history: list, role: str, response: Any):
        raise NotImplementedError()

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
    def get_all_function_names(self, function_calls) -> list[str]:
        raise NotImplementedError()

    @abstractmethod
    def get_function_response(self, function_call, response):
        raise NotImplementedError()

    @abstractmethod
    def get_response_metadata(self, response: Any) -> TokenUsage:
        raise NotImplementedError()

    @abstractmethod
    def is_response_healthy(self, response: Any) -> tuple[bool, str | None]:
        raise NotImplementedError()
