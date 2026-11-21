import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from common.utils import Timer
from pipeline.soc_agent.tasks import TaskOption
from pipeline.soc_agent.tools.tools import Tool

log = logging.getLogger("agent")

INVESTIGATION_STATUS = Literal["executing", "success", "failed"]

MAX_ITERATION = 5
FORCE_TOOLCALL = False
MAX_TOOL_CALLS_PER_TURN = 5
MAX_SECONDS = 300

BASE_AGENT_PATH = Path(__file__).parent


# the model's own mistake (unknown tool, bad arguments, too many calls): its message goes back to the model
class AgentRuntimeError(Exception): ...


class ProcessDeadlineError(Exception): ...


@dataclass(slots=True)
class AgentResponse:
    history: list
    execution_tracing: list
    response: Any
    status: INVESTIGATION_STATUS = "executing"
    prompt_token_count: int = 0
    output_token_count: int = 0
    started_at: float = 0  # wall clock, epoch ms: when the investigation ran
    duration_ms: float = 0  # from the monotonic clock: how long it took


class Agent:
    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        **kwargs,
    ):
        self.clock = clock  # monotonic, so a wall-clock jump can't end or extend an investigation
        self.options = kwargs
        self.trace = self.options.get("trace", True)
        self.max_tool_calls_per_turn = self.options.get("max_tool_calls_per_turn", MAX_TOOL_CALLS_PER_TURN)
        self.max_seconds = self.options.get("max_seconds", MAX_SECONDS)
        # sent back when the model answers in text although force_toolcall is on; "{finish_tool}" in it becomes
        # the task's finish tool. Without one, a text answer ends the run as before.
        self.toolcall_reminder_text = self.options.get("toolcall_reminder_text")

    def is_trace_enabled(self, task_config: TaskOption) -> bool:
        return self.trace or task_config.trace

    def get_system_prompt(self, name: str, path: Path = BASE_AGENT_PATH) -> str:
        sp_path = path.joinpath("prompts", f"{name}.md")
        return sp_path.read_text()

    def execute_tool(self, tools: dict[str, Tool], name: str, args: dict | None) -> dict:
        tool = tools.get(name)
        if not tool:
            raise AgentRuntimeError(f"Unknown tool: {name}")

        try:
            valid_args = tool.args_model.model_validate(args or {})
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(map(str, e['loc'])) or 'arguments'}: {e['msg']}" for e in exc.errors()
            )
            raise AgentRuntimeError(f"invalid arguments: {problems}") from None

        return tool.function(valid_args)

    def execute(self, question: str, task_config: TaskOption, role: str = "user", **options) -> AgentResponse:
        history = []
        execution_tracing = []

        max_iteration = self.options.get("max_iteration", MAX_ITERATION)
        force_toolcall = self.options.get("force_toolcall", FORCE_TOOLCALL)

        system_prompt = task_config.get_prompt_text() or ""
        reasoning_level = task_config.reasoning_level
        finish_tool = task_config.finish_tool
        model_options = task_config.model_options
        model = task_config.model

        agent_response = AgentResponse(history=history, execution_tracing=execution_tracing, response=None)
        tools = task_config.provider.get_tools(list(task_config.tools.values()))
        task_config.provider.append_history(history, role, question)
        # the first event: what the agent was asked, so the portal can show it above the steps
        self.add_event(
            task_config,
            execution_tracing,
            type="request",
            tools={name: t.hash for name, t in task_config.tools.items()},
            model=model,
            prompt_hash=hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:12],
            reasoning_level=reasoning_level,
            model_options=model_options,
            finish_tool=finish_tool if force_toolcall else None,
            text=question,
            error=None,
        )

        started = (
            self.clock()
        )  # monotonic: for the deadline and the duration, meaningless outside this process
        agent_response.started_at = time.time() * 1000
        for step in range(max_iteration):
            try:
                self.check_deadline(started)
                with Timer() as t:
                    response = task_config.provider.generate(
                        model=model,
                        system_prompt=system_prompt,
                        reasoning_level=reasoning_level,
                        history=history,
                        tools=tools,
                        force_toolcall=force_toolcall,
                        model_options=model_options,
                    )
            except ProcessDeadlineError:
                return self.finish(
                    task_config,
                    agent_response,
                    execution_tracing,
                    started,
                    "failed",
                    f"investigation exceeded {self.max_seconds}s time limit",
                )
            except Exception as exc:
                log.exception("failed to call generate content step %s", step)
                self.add_event(
                    task_config,
                    execution_tracing,
                    step=step,
                    type="model",
                    duration_ms=t.elapsed_ms,
                    error=str(exc),
                )
                return self.finish(
                    task_config,
                    agent_response,
                    execution_tracing,
                    started,
                    "failed",
                    f"failed to call generate content step {step} error {exc!r}",
                )

            usage_metadata = task_config.provider.get_response_metadata(response)
            function_calls = task_config.provider.get_function_calls(response)

            agent_response.prompt_token_count += usage_metadata.prompt_token_count
            # https://aistudio.google.com/docs/thinking#pricing
            agent_response.output_token_count += (
                usage_metadata.thoughts_token_count + usage_metadata.output_token_count
            )

            healthy, reason = task_config.provider.is_response_healthy(response)
            if not healthy:
                self.add_event(
                    task_config,
                    execution_tracing,
                    step=step,
                    type="model",
                    duration_ms=t.elapsed_ms,
                    prompt_tokens=usage_metadata.prompt_token_count,
                    output_tokens=usage_metadata.output_token_count,
                    thoughts_tokens=usage_metadata.thoughts_token_count,
                    error=reason,
                )
                return self.finish(task_config, agent_response, execution_tracing, started, "failed", reason)

            self.add_event(
                task_config,
                execution_tracing,
                step=step,
                type="model",
                duration_ms=t.elapsed_ms,
                prompt_tokens=usage_metadata.prompt_token_count,
                output_tokens=usage_metadata.output_token_count,
                thoughts_tokens=usage_metadata.thoughts_token_count,
                tool_calls=task_config.provider.get_all_function_names(function_calls),
                text=task_config.provider.get_response_text(response),
                thought=task_config.provider.get_model_thought(response),
                error=None,
            )
            log.info("step %s: model call %.0f ms, %s tool calls", step, t.elapsed_ms, len(function_calls))

            if not function_calls and force_toolcall and self.toolcall_reminder_text:
                # Gemini's ANY mode guarantees a tool call; Ollama has no such mode, so a model can still answer
                # in text. Keep its turn, remind it, and go on: max_iteration and max_seconds still bound this.
                task_config.provider.append_response_to_history(history, "model", response)
                reminder = self.toolcall_reminder_text.format(finish_tool=finish_tool)
                task_config.provider.append_history(history, role, reminder)
                self.add_event(
                    task_config,
                    execution_tracing,
                    step=step,
                    type="reminder",
                    text=task_config.provider.get_response_text(response),
                    thought=task_config.provider.get_model_thought(response),
                    error=None,
                )
                log.info("step %s: answered without a tool call; reminded", step)
                continue

            if not function_calls:
                text = task_config.provider.get_response_text(response)
                if not text:
                    return self.finish(
                        task_config, agent_response, execution_tracing, started, "failed", "empty response"
                    )

                return self.finish(task_config, agent_response, execution_tracing, started, "success", text)

            task_config.provider.append_response_to_history(history, "model", response)

            function_response_parts = []
            for index, function_call in enumerate(function_calls):
                function_name, function_args = task_config.provider.get_function_details(function_call)
                error = None

                try:
                    self.check_deadline(started)
                    with Timer() as f:
                        # every call still gets a response, so the extra ones are answered with the refusal
                        if index >= self.max_tool_calls_per_turn:
                            raise AgentRuntimeError(
                                f"refused: more than {self.max_tool_calls_per_turn} tool calls in one turn"
                            )
                        if function_name == finish_tool and len(function_calls) > 1:
                            raise AgentRuntimeError(
                                f"{finish_tool} must be called on its own. Read the results of your other tool "
                                f"calls first, then call {finish_tool} again by itself."
                            )

                        function_response = self.execute_tool(task_config.tools, function_name, function_args)
                except AgentRuntimeError as exc:
                    log.warning("rejected tool call %s: %s", function_name, exc)
                    error = str(exc)
                    function_response = {"error": f"failed to execute tool {function_name} error: {exc}"}
                except ProcessDeadlineError:
                    return self.finish(
                        task_config,
                        agent_response,
                        execution_tracing,
                        started,
                        "failed",
                        f"investigation exceeded {self.max_seconds}s time limit",
                    )
                except Exception as exc:
                    log.exception("failed to execute tool %s", function_name)
                    error = str(exc)  # the real cause, for the trace; the model only gets the generic message
                    function_response = {"error": f"failed to execute tool {function_name}"}

                self.add_event(
                    task_config,
                    execution_tracing,
                    step=step,
                    type="tool",
                    name=function_name,
                    args=function_args,
                    result=function_response,
                    duration_ms=f.elapsed_ms,
                    error=error,
                )
                log.info("step %s: tool %s %.0f ms", step, function_name, f.elapsed_ms)

                if len(function_calls) == 1 and function_name == finish_tool and error is None:
                    return self.finish(
                        task_config, agent_response, execution_tracing, started, "success", function_response
                    )

                formatted_response = task_config.provider.get_function_response(
                    function_call, {"untrusted_data": function_response}
                )
                function_response_parts.append(formatted_response)

            task_config.provider.append_history(history, role, function_response_parts)

        return self.finish(
            task_config, agent_response, execution_tracing, started, "failed", "Agent step limit reached."
        )

    def add_event(self, task_config: TaskOption, trace: list, **fields):
        if self.is_trace_enabled(task_config):
            fields["seq"] = len(trace)
            fields["at"] = time.time() * 1000
            trace.append(fields)

    def finish(
        self,
        task_config: TaskOption,
        agent_response: AgentResponse,
        trace: list,
        started: float,
        status: INVESTIGATION_STATUS,
        response: Any,
    ) -> AgentResponse:
        agent_response.status, agent_response.response = status, response
        agent_response.duration_ms = (self.clock() - started) * 1000
        self.add_event(
            task_config, trace, type="result", status=status, duration_ms=agent_response.duration_ms
        )
        return agent_response

    def check_deadline(self, started: float) -> None:
        if self.clock() - started > self.max_seconds:
            raise ProcessDeadlineError(f"investigation exceeded {self.max_seconds}s time limit")
