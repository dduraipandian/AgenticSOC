import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, get_type_hints

from beartype.door import is_subhint
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
    function: Callable[..., dict]

    @property
    def parameters(self) -> dict:
        return self.args_model.model_json_schema()


def validate_signature(args_model: type[ToolArgs], strict: bool = True):
    def wrap(func):
        func_hints = get_type_hints(func)
        model_hints = get_type_hints(args_model)

        signature = inspect.signature(func)
        func_params = set(signature.parameters)
        model_fields = set(args_model.model_fields)

        if func_params != model_fields:
            raise ToolDefinitionError(
                f"{func.__name__}: parameter mismatch. "
                f"Missing: {model_fields - func_params}, "
                f"Extra: {func_params - model_fields}"
            )

        for name in model_fields:
            actual = func_hints.get(name)
            expected = model_hints[name]

            if (strict and actual != expected) or (actual is None or not is_subhint(expected, actual)):
                raise ToolDefinitionError(
                    f"{func.__name__}.{name}: "
                    f"expected {expected}, got {actual}"
                )

        return func

    return wrap

def make_tool(func: Callable[..., dict], args_model: type[ToolArgs], description: str | None = None):
    description = description or func.__doc__
    if not description:
        raise ToolDescriptionNotFound(f"{func.__name__}: is missing a description")

    return Tool(
        name=func.__name__,
        description=description.strip(),
        args_model=args_model,
        function=func
    )

def tool_map(tools: list[Tool]) -> dict[str, Tool]:
    by_name = {}
    for t in tools:
        if t.name in by_name:
            raise DuplicateToolError(f"{t.name} is already in the tool list")
        by_name[t.name] = t

    return by_name
