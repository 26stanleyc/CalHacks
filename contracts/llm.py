"""The one way any part calls an LLM, plus a mock that needs no credits.

Eric implements the real Bitdeer client behind `LLMClient`; everyone else codes against the
protocol and gets `MockLLM` until credits arrive (see `config.use_mock_llm`).
"""

import types
import typing
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: typing.Literal["system", "user", "assistant", "tool"]
    content: str


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict = Field(default_factory=dict)  # JSON schema


class LLMToolCall(BaseModel):
    name: str
    arguments: dict = Field(default_factory=dict)


class LLMResponse(BaseModel):
    text: str = ""
    parsed: Any = None  # an instance of response_schema, when one was requested
    tool_calls: list[LLMToolCall] = Field(default_factory=list)
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


@runtime_checkable
class LLMClient(Protocol):
    def complete(
        self,
        messages: list[Message],
        *,
        role: str,  # a key of config.MODEL_ROLES; the client picks the model
        response_schema: type[BaseModel] | None = None,
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse: ...


def mock_instance(schema: type[BaseModel]) -> BaseModel:
    """Build a schema-valid placeholder instance: defaults where given, simple values otherwise."""
    values = {}
    for name, field in schema.model_fields.items():
        if not field.is_required():
            continue
        values[name] = _mock_value(field.annotation, field)
    return schema.model_validate(values)


def _mock_value(annotation: Any, field: Any = None) -> Any:
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin in (typing.Union, types.UnionType):
        non_none = [a for a in args if a is not type(None)]
        return _mock_value(non_none[0]) if non_none else None
    if origin is typing.Literal:
        return args[0]
    if origin in (list, set, tuple):
        return [] if origin is not tuple else tuple(_mock_value(a) for a in args)
    if origin is dict:
        return {}
    if isinstance(annotation, type):
        if issubclass(annotation, BaseModel):
            return mock_instance(annotation)
        if issubclass(annotation, Enum):
            return next(iter(annotation))
        if issubclass(annotation, bool):
            return False
        if issubclass(annotation, int):
            return 0
        if issubclass(annotation, float):
            ge = _bound(field, "ge")
            return ge if ge is not None else 0.5
        if issubclass(annotation, str):
            return "mock"
        if issubclass(annotation, datetime):
            return datetime(2026, 1, 1)
    return None


def _bound(field: Any, name: str) -> float | None:
    for meta in getattr(field, "metadata", []) or []:
        value = getattr(meta, name, None)
        if value is not None:
            return value
    return None


class MockLLM:
    """Deterministic, free stand-in. Register canned factories per schema for realistic answers."""

    def __init__(self, canned: dict[type[BaseModel], typing.Callable[[list[Message]], BaseModel]] | None = None):
        self.canned = canned or {}
        self.calls: list[tuple[str, list[Message]]] = []

    def complete(
        self,
        messages: list[Message],
        *,
        role: str,
        response_schema: type[BaseModel] | None = None,
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        self.calls.append((role, messages))
        parsed = None
        if response_schema is not None:
            factory = self.canned.get(response_schema)
            parsed = factory(messages) if factory else mock_instance(response_schema)
        return LLMResponse(text="mock response", parsed=parsed, model=f"mock:{role}")
