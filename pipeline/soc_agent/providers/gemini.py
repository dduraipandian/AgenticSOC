from typing import Any

from google import genai
from google.genai import types

from pipeline.soc_agent.providers.providers import REASONING_LEVEL, Provider
from pipeline.soc_agent.tools.tools import Tool


class Gemini(Provider):
    def __init__(self, model: str, system_prompt: str, reasoning_level: REASONING_LEVEL, **kwargs):
        super().__init__(model, system_prompt, reasoning_level, **kwargs)
        self._client = None

    def get_tools(self, tools: list[Tool]) -> list[types.Tool]:
        _tools = [types.FunctionDeclaration(**self.get_tool_definition(t)) for t in tools]
        return [types.Tool(function_declarations=_tools)]

    def get_config(self, tools: list[types.Tool]):
        return types.GenerateContentConfig(system_instruction=self.system_prompt, tools=tools)

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

    def generate(self, history: list, tools: list[types.Tool]):
        return self.client.models.generate_content(model=self.model,
                                                    contents=history,
                                                    config=self.get_config(tools))

    def get_response_text(self, response: types.GenerateContentResponse) -> str | None:
        return response.text

    def append_history(self, history: list, role: str, content: Any):
        if isinstance(content, str):
            content = types.Content(role=role, parts=[types.Part(text=content)])
        elif isinstance(content, list):
            content = types.Content(role=role, parts=content)

        history.append(content)

    def append_response_to_history(self, history: list,  role: str, response: types.GenerateContentResponse):
        history.append(response.candidates[0].content)

    def get_function_calls(self, response: types.GenerateContentResponse) -> list:
        return response.function_calls or []

    def get_function_details(self, function_call) -> tuple[str, dict]:
        return function_call.name, function_call.args

    def get_function_response(self, function_call, response):
        return types.Part.from_function_response(
                    name=function_call.name,
                    response=response
                )
