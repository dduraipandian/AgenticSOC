import logging

from pipeline.soc_agent.providers.providers import Provider
from pipeline.soc_agent.tools.tools import Tool

log = logging.getLogger("agent")

MAX_ITERATION = 5

class AgentRuntimeError(Exception): ...

class Agent:
    def __init__(self, tools: dict[str, Tool], provider: Provider, **kwargs):
        self.tools = tools
        self.provider = provider
        self.options = kwargs

    def execute_tool(self, name: str, args: dict) -> dict:
        tool_config = self.tools.get(name)
        func = tool_config.function if tool_config else None
        if func and callable(func):
            try:
                return func(**args)
            except Exception:
                log.exception("failed to execute tool %s", name)
                return {"error": f"failed to execute tool {name}"}

        return {"error": f"Unknown tool: {name}"}

    def execute(self, question: str, role: str = "user"):
        history = []
        tools = self.provider.get_tools(list(self.tools.values()))
        self.provider.append_history(history, role, question)

        max_iteration = self.provider.provider_config.get("max_iteration")
        max_iteration = max_iteration or self.options.get("max_iteration", MAX_ITERATION)

        for step in range(max_iteration):
            print(f"executing step {step}")
            response = self.provider.generate(history, tools)
            function_calls = self.provider.get_function_calls(response)

            if not function_calls:
                return self.provider.get_response_text(response)

            self.provider.append_response_to_history(history, "model", response)

            function_response_parts = []

            print(f"no. of function calls in step {step}: {len(function_calls)}")
            for function_call in function_calls:
                function_name, function_args = self.provider.get_function_details(function_call)
                function_response = self.execute_tool(function_name, function_args)
                formatted_response = self.provider.get_function_response(function_call, {"untrusted_data": function_response})
                function_response_parts.append(formatted_response)

            self.provider.append_history(history, role, function_response_parts)

        raise AgentRuntimeError("Agent step limit reached")
