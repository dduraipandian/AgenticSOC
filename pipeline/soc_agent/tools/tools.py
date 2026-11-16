from abc import abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import ClassVar


class DuplicateToolError(Exception): ...
class ToolDescriptionNotFound(Exception): ...


@dataclass(frozen=True)
class InputSchema:
    properties: dict
    required: list[str]
    type: str = "object"
    additionalProperties: bool = False


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: InputSchema
    function: Callable


class ToolFunction:
    tools: ClassVar[dict[str, Tool]] = {}

    def __init_subclass__(cls) -> None:
        tool_definition = cls.get_tool_definition()
        name = tool_definition.name
        if name in cls.tools:
            raise DuplicateToolError(f"{name} is already registered in tools")

        cls.tools[name] = tool_definition

    @classmethod
    def get_tools(cls) -> dict[str, Tool]:
        return cls.tools

    @classmethod
    @abstractmethod
    def get_tool_definition(cls) -> Tool:
        raise NotImplementedError()


    def __call__(self, parameters: InputSchema, description: str | None  = None):
        def wrap(func):
            name = func.__name__
            func_description = description or func.__doc__

            if not func_description:
                raise ToolDescriptionNotFound(f"function description not found for {name}")

            self.tools[name] = Tool(name=name,
                                    description=func_description,
                                    parameters=parameters,
                                    function=func
                                )

            @wraps(func)
            def inner(*args, **kwargs):
                return func(*args, **kwargs)

            return inner

        return wrap
