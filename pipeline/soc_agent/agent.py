from pipeline.soc_agent.providers.providers import Provider
from pipeline.soc_agent.tools.tools import Tool

MAX_STEPS = 5

class AgentRuntimeError(Exception): ...

class Agent:
    def __init__(self, tools: dict[str, Tool], provider: Provider, **kwargs):
        self.tools = tools
        self.provider = provider
        self.max_steps = kwargs.get("MAX_STEPS", MAX_STEPS)
        # self.provider = provider_cls(system_prompt, reasoning_level, tools)

    def execute_tool(self, name: str, args: dict) -> dict:
        tool_config = self.tools.get(name)
        func = tool_config.function if tool_config else None
        if func and callable(func):
            return func(**args)

        raise ValueError(f"Unknown tool: {name}")

    def execute(self, question: str, role: str = "user"):
        self.provider.append_history(role, question)
        for step in range(self.max_steps):
            print(f"executing step {step}")
            response = self.provider.generate()
            function_calls = self.provider.get_function_calls(response)

            if not function_calls:
                return self.provider.get_response_text(response)

            self.provider.append_history(role, response.candidates[0].content)

            function_response_parts = []

            print(f"no. of function calls in step {step}: {len(function_calls)}")
            for function_call in function_calls:
                function_name, function_args = self.provider.get_function_details(function_call)
                function_response = self.execute_tool(function_name, function_args)
                formatted_response = self.provider.get_function_response(function_call, {"untrusted_data": function_response})
                function_response_parts.append(formatted_response)

            self.provider.append_history(role, function_response_parts)

        raise AgentRuntimeError("Agent step limit reached")
