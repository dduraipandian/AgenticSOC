import logging
import time
from dataclasses import dataclass
from typing import Any, Literal

from common.utils import Timer
from pipeline.soc_agent.providers.providers import Provider
from pipeline.soc_agent.tools.tools import Tool

log = logging.getLogger("agent")

INVESTIGATION_STATUS = Literal["executing", "success", "failed"]

MAX_ITERATION = 5

class AgentRuntimeError(Exception): ...

@dataclass(slots=True)
class AgentResponse:
    history: list
    execution_tracing: list
    response: Any
    status: INVESTIGATION_STATUS = "executing"
    prompt_token_count: int = 0
    output_token_count: int = 0


class Agent:
    def __init__(self, tools: dict[str, Tool], provider: Provider, **kwargs):
        self.tools = tools
        self.provider = provider
        self.options = kwargs
        self.trace = self.options.get("trace", True)

    def execute_tool(self, name: str, args: dict) -> dict:
        tool_config = self.tools.get(name)
        func = tool_config.function if tool_config else None
        if func and callable(func):
            return func(**args)

        raise AgentRuntimeError(f"Unknown tool: {name}")

    def execute(self, question: str, role: str = "user") -> AgentResponse:
        history = []
        execution_tracing = []

        agent_response = AgentResponse(history=history, execution_tracing=execution_tracing, response=None)
        tools = self.provider.get_tools(list(self.tools.values()))
        self.provider.append_history(history, role, question)

        max_iteration = self.provider.provider_config.get("max_iteration")
        max_iteration = max_iteration or self.options.get("max_iteration", MAX_ITERATION)

        for step in range(max_iteration):
            try:
                with Timer() as t:
                    response = self.provider.generate(history, tools)
            except Exception as exc:
                log.exception("failed to call generate content step %s", step)
                self.add_event(execution_tracing, step=step, type="model", duration_ms=t.elapsed_ms, error=str(exc))
                return self.finish(agent_response, execution_tracing, "failed",
                                   f"failed to call generate content step {step} error {exc!r}")

            usage_metadata = self.provider.get_response_metadata(response)
            function_calls = self.provider.get_function_calls(response)

            self.add_event(execution_tracing,
                           step=step,
                           type="model",
                           duration_ms=t.elapsed_ms,
                           prompt_tokens=usage_metadata.prompt_token_count,
                           output_tokens=usage_metadata.output_token_count,
                           thoughts_tokens=usage_metadata.thoughts_token_count,
                           tool_calls=self.provider.get_all_function_names(function_calls),
                           error=None)
            log.info("step %s: model call %.0f ms, %s tool calls", step, t.elapsed_ms, len(function_calls))

            agent_response.prompt_token_count += usage_metadata.prompt_token_count
            # https://aistudio.google.com/docs/thinking#pricing
            agent_response.output_token_count += usage_metadata.thoughts_token_count + usage_metadata.output_token_count

            if not function_calls:
                return self.finish(agent_response, execution_tracing, "success",
                                   self.provider.get_response_text(response))

            self.provider.append_response_to_history(history, "model", response)

            function_response_parts = []
            for function_call in function_calls:
                function_name, function_args = self.provider.get_function_details(function_call)
                error = None

                with Timer() as f:
                    try:
                        function_response = self.execute_tool(function_name, function_args)
                    except AgentRuntimeError as exc:
                        log.warning("missing tool %s", function_name)
                        error = str(exc)
                        function_response = {"error": f"failed to execute tool {function_name} error: {exc}"}
                    except Exception as exc:
                        log.exception("failed to execute tool %s", function_name)
                        error = str(exc)  # the real cause, for the trace; the model only gets the generic message
                        function_response = {"error": f"failed to execute tool {function_name}"}

                self.add_event(execution_tracing, step=step, type="tool", name=function_name, args=function_args,
                               duration_ms=f.elapsed_ms, error=error)
                log.info("step %s: tool %s %.0f ms", step, function_name, f.elapsed_ms)

                formatted_response = self.provider.get_function_response(function_call, {"untrusted_data": function_response})
                function_response_parts.append(formatted_response)

            self.provider.append_history(history, role, function_response_parts)

        return self.finish(agent_response, execution_tracing, "failed", "Agent step limit reached.")

    def add_event(self, trace: list, **fields):
        if self.trace:
            fields["seq"] = len(trace)
            fields["at"] = time.time() * 1000
            trace.append(fields)

    def finish(self, agent_response: AgentResponse, trace: list, status: INVESTIGATION_STATUS,
               response: Any) -> AgentResponse:
        agent_response.status, agent_response.response = status, response
        self.add_event(trace, type="result", status=status)
        return agent_response
