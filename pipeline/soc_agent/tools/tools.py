from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict


class DuplicateToolError(Exception): ...


class ToolDescriptionNotFound(Exception): ...


class ToolDefinitionError(Exception): ...


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        if cls is ToolArgs:
            return

        missing = [
            name
            for name, field_info in cls.model_fields.items()
            if not field_info.description or not field_info.description.strip()
        ]
        if missing:
            raise ToolDescriptionNotFound(
                f"Model '{cls.__name__}' is missing field descriptions for: {', '.join(missing)}"
            )


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[ToolArgs]
    function: Callable[[ToolArgs], dict]

    @property
    def parameters(self) -> dict:
        return self.args_model.model_json_schema()


def make_tool(
    func: Callable[[ToolArgs], dict], args_model: type[ToolArgs], description: str | None = None
) -> Tool:
    description = description or func.__doc__
    if not description:
        raise ToolDescriptionNotFound(f"{func.__name__}: is missing a description")

    return Tool(name=func.__name__, description=description.strip(), args_model=args_model, function=func)


def tool_map(tools: list[Tool]) -> dict[str, Tool]:
    by_name = {}
    for t in tools:
        if t.name in by_name:
            raise DuplicateToolError(f"{t.name} is already in the tool list")
        by_name[t.name] = t

    return by_name


def update_tools(tools: dict[str, Tool], tool_list: list[Tool]):
    new_tools = tool_map(tool_list)
    overlapping_tools = set(tools).intersection(set(new_tools))

    if overlapping_tools:
        raise DuplicateToolError(f"{overlapping_tools} are already in the tool list")

    tools.update(new_tools)
    return tools
