from typing import Any

from google import genai
from google.genai import types

from pipeline.soc_agent.providers.providers import REASONING_LEVEL, Provider
from pipeline.soc_agent.tools.tools import Tool


class Gemini(Provider):
    def __init__(self, model: str, system_prompt: str, reasoning_level: REASONING_LEVEL, tools: list[Tool]):
        super().__init__(model, system_prompt, reasoning_level, tools)
        self._tools = None
        self._config = None
        self._client = None

    @property
    def tools(self):
        if not self._tools:
            _tools = [types.FunctionDeclaration(**self.get_tool_definition(t)) for t in self._tool_list]
            self._tools = [types.Tool(function_declarations=_tools)]

        return self._tools

    @property
    def config(self):
        if not self._config:
            self._config = types.GenerateContentConfig(
                system_instruction=self.system_prompt,
                tools=self._tools
            )

        return self._config

    @property
    def client(self):
        if not self._client:
            self._client = genai.Client()

        return self._client

    def get_tool_definition(self, tool: Tool) -> dict:
        return {
                "name": tool.name,
                "description": tool.description,
                "parameters_json_schema": tool.parameters
            }

    def generate(self):
        return self.client.models.generate_content(model=self.model,
                                                    contents=self.history,
                                                    config=self.config)

    def get_response_text(self, response: types.GenerateContentResponse) -> str | None:
        if not response:
            return None

        return response.text

    def append_history(self, role: str, content: Any):
        self.history.append(types.Content(role=role, parts=[types.Part(text=content)]))

    def get_function_calls(self, response: types.GenerateContentResponse) -> list:
        if not response:
            return []

        return response.function_calls or []

    def get_function_details(self, function_call) -> tuple[str, dict]:
        return function_call.name, function_call.args

    def get_function_response(self, function_call, response):
        return types.Part.from_function_response(
                    name=function_call.name,
                    response=response
                )
