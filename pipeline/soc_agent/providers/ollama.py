import json
import logging
from typing import Any

from ollama import ChatResponse, Client

from pipeline.soc_agent.providers.providers import (
    Provider,
    TokenUsage,
)
from pipeline.soc_agent.tools.tools import Tool

log = logging.getLogger("ollama")

DEFAULT_MODEL = "lfm3:1.7b-q4_K_M"
TIMEOUT_MS = 120_000  # per model call; a local model is slow, the first call also loads it


class Ollama(Provider):
    def __init__(self, config):
        super().__init__(config)
        self.host = self.provider_config["host"]  # http://localhost:11434
        self._client = None

    def get_tools(self, tools: list[Tool]) -> list:
        return [{"type": "function", "function": self.get_tool_definition(tool)} for tool in tools]

    @property
    def client(self):
        if not self._client:
            # httpx takes seconds; the config, like Gemini's, is in milliseconds
            timeout_s = self.provider_config.get("timeout_ms", TIMEOUT_MS) / 1000
            self._client = Client(host=self.host, timeout=timeout_s)

        return self._client

    def get_tool_definition(self, tool: Tool) -> dict:
        return {"name": tool.name, "description": tool.description, "parameters": tool.parameters}

    def generate(
        self,
        model: str | None,
        system_prompt: str | None,
        reasoning_level: str | None,
        history: list,
        tools: list,
        **kwargs: dict[str, Any],
    ) -> ChatResponse:
        model = model or self.provider_config.get("default_model", DEFAULT_MODEL)
        model_options = kwargs.get("model_options", {})
        system_prompt = system_prompt or ""

        system_instruction = [{"role": "system", "content": system_prompt}]

        response = self.client.chat(
            model=model,
            messages=system_instruction + history,
            tools=tools,
            think=True,
            options=model_options,
            stream=False,
        )
        return response

    def get_response_text(self, response: ChatResponse) -> str | None:
        return response.message.content

    def get_model_thought(self, response: ChatResponse) -> str | None:
        return response.message.thinking or None

    def append_history(self, history: list, role: str, content: Any):
        # Ollama wants one {"role", "content"} message each: a text turn becomes one, and a turn of tool results
        # (already role "tool" messages from get_function_response) adds one message per result.
        if isinstance(content, list):
            history.extend(content)
        else:
            history.append({"role": role, "content": content})

    def append_response_to_history(self, history: list, role: str, response: ChatResponse):
        history.append(response.message)

    def get_function_calls(self, response: ChatResponse) -> list:
        return response.message.tool_calls or []

    def get_function_details(self, function_call) -> tuple[str, dict]:
        return function_call.function.name, dict(function_call.function.arguments or {})

    def get_all_function_names(self, function_calls) -> list[str]:
        return [f.function.name for f in function_calls]

    def get_function_response(self, function_call, response: ChatResponse):
        return {
            "role": "tool",
            "tool_name": function_call.function.name,
            "content": json.dumps(response, default=str),
        }

    def get_response_metadata(self, response: ChatResponse) -> TokenUsage:
        if not response.prompt_eval_count:
            return TokenUsage()

        usage_metadata = response
        usage = {
            "prompt_token_count": usage_metadata.prompt_eval_count or 0,
            "output_token_count": 0,
            "thoughts_token_count": usage_metadata.eval_count or 0,
            "total_token_count": 0,
        }
        return TokenUsage(**usage)

    def is_response_healthy(self, response: ChatResponse) -> tuple[bool, str | None]:
        if response.done_reason == "length":
            return False, "model stopped: answer was cut off"

        return True, None
