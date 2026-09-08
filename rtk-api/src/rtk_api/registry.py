"""Tool registry: a single decorator that both REST and MCP surfaces read
from. Tools are plain Python functions with type hints and docstrings;
`rtk-api/tools/__init__.py` imports every module under `tools/` so adding
a new file there is enough to register new tools -- no edits to app.py.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, get_type_hints

from pydantic import BaseModel, ConfigDict, TypeAdapter, create_model


@dataclass
class ToolSpec:
    name: str  # e.g. "things.list" -- REST route is /v1/things/list
    fn: Callable[..., Any]
    write: bool = False
    tags: list[str] = field(default_factory=list)
    description: str = ""
    # Lazily built synthetic pydantic model for the kwargs; shared by
    # `params_schema()` (what we advertise) and `call()` (what we enforce)
    # so the two can't drift.
    _params_model: type[BaseModel] | None = field(default=None, init=False, repr=False)

    @property
    def mcp_name(self) -> str:
        """MCP tool names can't contain '.', so 'things.list' -> 'things_list'."""
        return self.name.replace(".", "_")

    def params_model(self) -> type[BaseModel]:
        """The function signature as a synthetic pydantic model (cached).
        `extra="forbid"` so unknown kwargs are rejected rather than blowing
        up inside the tool as a TypeError.
        """
        if self._params_model is None:
            self._params_model = _make_params_model(_signature_fields(self.fn))
        return self._params_model

    def params_schema(self) -> dict[str, Any]:
        """Build a JSON schema for this tool's kwargs by treating the
        function signature as a synthetic pydantic model.
        """
        model = self.params_model()
        if not model.model_fields:
            return {"type": "object", "properties": {}}
        return TypeAdapter(model).json_schema()

    def call(self, kwargs: dict[str, Any]) -> Any:
        """Validate `kwargs` against the advertised schema, then call the
        tool. Raises `pydantic.ValidationError` (mapped to 400 by the REST
        layer) on a wrong type, a bad Literal, a missing required arg, or an
        unknown key. Defaults and explicit `None`s survive round-tripping.
        """
        validated = self.params_model().model_validate(kwargs)
        return self.fn(**validated.model_dump())


def _signature_fields(fn: Callable[..., Any]) -> dict[str, tuple[Any, Any]]:
    sig = inspect.signature(fn)
    try:
        hints = get_type_hints(fn)
    except Exception:
        hints = {}
    fields: dict[str, tuple[Any, Any]] = {}
    for pname, param in sig.parameters.items():
        if pname in ("self", "cls"):
            continue
        annotation = hints.get(pname, Any)
        default = param.default if param.default is not inspect.Parameter.empty else ...
        fields[pname] = (annotation, default)
    return fields


def _make_params_model(fields: dict[str, tuple[Any, Any]]) -> type[BaseModel]:
    # Field names are passed through as-is: a parameter called `list` (as in
    # things.add) is a legal pydantic field name and doesn't clash with the
    # builtin here because they're only ever kwargs to create_model.
    return create_model(  # type: ignore[call-overload]
        "Params",
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )


REGISTRY: dict[str, ToolSpec] = {}


def tool(name: str, *, write: bool = False, tags: list[str] | None = None):
    """Register a function as a rtk-api tool.

    Usage:
        @tool("things.list")
        def list_things(view: str, limit: int = 200) -> list[dict]:
            '''List Things to-dos in a given view.'''
            ...
    """

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        description = (fn.__doc__ or "").strip()
        REGISTRY[name] = ToolSpec(
            name=name, fn=fn, write=write, tags=tags or [], description=description
        )
        return fn

    return deco
