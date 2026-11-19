from pipeline.soc_agent.tools.tools import Tool


class UnknownTask(Exception): ...


class TaskDefinitionError(Exception): ...


TASKS: dict[str, dict] = {
    "soc_investigation": {
        "finish_tool": "submit_investigation",
        "tools": ["submit_investigation", "get_host", "get_process_tree", "search_telemetry"],
        "provider_name": "gemini",
    },
    "case_summary": {
        "finish_tool": "submit_case_summary",
        "tools": ["get_host", "get_process_tree", "search_telemetry", "case_details", "submit_case_summary"],
        "provider_name": "gemini",
    },
}


def get_task(name: str) -> dict:
    if name not in TASKS:
        raise UnknownTask(f"unknown agent task {name!r}; known tasks: {sorted(TASKS)}")
    return TASKS[name]


def select_tools(name: str, task: dict, available: dict[str, Tool]) -> dict[str, Tool]:
    if task["finish_tool"] not in task["tools"]:
        raise TaskDefinitionError(f"{name}: finish tool {task['finish_tool']!r} is not in its tool list")

    missing = [t for t in task["tools"] if t not in available]
    if missing:
        raise TaskDefinitionError(f"{name}: tools not available: {missing}; built: {sorted(available)}")

    return {t: available[t] for t in task["tools"]}
