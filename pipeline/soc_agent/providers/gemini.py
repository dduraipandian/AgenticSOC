import logging
from typing import Any

from google import genai
from google.genai import types

from pipeline.soc_agent.providers.providers import DEFAULT_REASONING_LEVEL, Provider, TokenUsage
from pipeline.soc_agent.tools.tools import Tool

log = logging.getLogger("gemini")

GEMINI_MODEL = "models/gemini-3.5-flash-lite"

TIMEOUT_MS = 60_000  # can be configured in model_providers.yaml
RETRY_ATTEMPTS = 3  # the first try included; retried on 408, 429 and 5xx with backoff


class Gemini(Provider):
    def __init__(self, config):
        super().__init__(config)
        self._client = None

    def get_tools(self, tools: list[Tool]) -> list[types.Tool]:
        _tools = [types.FunctionDeclaration(**self.get_tool_definition(t)) for t in tools]
        return [types.Tool(function_declarations=_tools)]

    @property
    def client(self):
        if not self._client:
            self._client = genai.Client(
                http_options=types.HttpOptions(
                    timeout=self.provider_config.get("timeout_ms", TIMEOUT_MS),
                    retry_options=types.HttpRetryOptions(
                        attempts=self.provider_config.get("retry_attempts", RETRY_ATTEMPTS),
                        http_status_codes=[408, 429, 500, 502, 503, 504],
                    ),
                )
            )

        return self._client

    def get_tool_definition(self, tool: Tool) -> dict:
        return {"name": tool.name, "description": tool.description, "parameters_json_schema": tool.parameters}

    def generate(
        self,
        model: str | None,
        system_prompt: str | None,
        reasoning_level: str | None,
        history: list,
        tools: list[types.Tool],
        **kwargs: dict[str, Any],
    ):
        model = model or self.provider_config.get("model", GEMINI_MODEL)
        reasoning_level = reasoning_level or self.provider_config.get(
            "reasoning_level", DEFAULT_REASONING_LEVEL
        )
        system_prompt = system_prompt or ""
        force_toolcall = kwargs.get("force_toolcall", False)
        mode = types.FunctionCallingConfigMode.ANY if force_toolcall else types.FunctionCallingConfigMode.AUTO

        try:
            thinking_level = types.ThinkingLevel[reasoning_level.upper()]
        except Exception as e:  # noqa BLE001
            log.warning(f"invalid gemini reasoning level {reasoning_level} {e}")
            thinking_level = types.ThinkingLevel["MEDIUM"]

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=tools,
            thinking_config=types.ThinkingConfig(thinking_level=thinking_level, include_thoughts=True),
            tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode=mode)),
        )
        return self.client.models.generate_content(model=model, contents=history, config=config)

    def get_response_text(self, response: types.GenerateContentResponse) -> str | None:
        if not response.candidates or not response.candidates[0].content:
            return None
        parts = response.candidates[0].content.parts or []
        return "".join(p.text for p in parts if p.text and not p.thought) or None

    def get_model_thought(self, response: types.GenerateContentResponse) -> str | None:
        if not response.candidates or not response.candidates[0].content:
            return None
        parts = response.candidates[0].content.parts or []
        return "".join(p.text for p in parts if p.text and p.thought) or None

    def append_history(self, history: list, role: str, content: Any):
        if isinstance(content, str):
            content = types.Content(role=role, parts=[types.Part(text=content)])
        elif isinstance(content, list):
            content = types.Content(role=role, parts=content)

        history.append(content)

    def append_response_to_history(self, history: list, role: str, response: types.GenerateContentResponse):
        history.append(response.candidates[0].content)

    def get_function_calls(self, response: types.GenerateContentResponse) -> list:
        return response.function_calls or []

    def get_function_details(self, function_call) -> tuple[str, dict]:
        return function_call.name, function_call.args

    def get_all_function_names(self, function_calls) -> list[str]:
        return [f.name for f in function_calls]

    def get_function_response(self, function_call, response):
        return types.Part.from_function_response(name=function_call.name, response=response)

    def get_response_metadata(self, response: types.GenerateContentResponse) -> TokenUsage:
        if not response.usage_metadata:
            return TokenUsage()

        usage_metadata = response.usage_metadata
        usage = {
            "prompt_token_count": usage_metadata.prompt_token_count or 0,
            "output_token_count": usage_metadata.candidates_token_count or 0,
            "thoughts_token_count": usage_metadata.thoughts_token_count or 0,
            "total_token_count": usage_metadata.total_token_count or 0,
        }
        return TokenUsage(**usage)

    def is_response_healthy(self, response: types.GenerateContentResponse) -> tuple[bool, str | None]:
        if response.prompt_feedback and response.prompt_feedback.block_reason:
            return False, f"prompt blocked: {response.prompt_feedback.block_reason}"
        if not response.candidates:
            return False, "no candidates in response"

        finish_reason = response.candidates[0].finish_reason
        if finish_reason not in (None, types.FinishReason.STOP):
            return False, f"model stopped: {finish_reason}"

        return True, None
