"""Tool registry: a single decorator that both REST and MCP surfaces read
from. Tools are plain Python functions with type hints and docstrings;
`hometools/tools/__init__.py` imports every module under `tools/` so adding
a new file there is enough to register new tools -- no edits to app.py.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, get_type_hints

from pydantic import TypeAdapter


@dataclass
class ToolSpec:
    name: str  # e.g. "things.list" -- REST route is /v1/things/list
    fn: Callable[..., Any]
    write: bool = False
    tags: list[str] = field(default_factory=list)
    description: str = ""

    @property
    def mcp_name(self) -> str:
        """MCP tool names can't contain '.', so 'things.list' -> 'things_list'."""
        return self.name.replace(".", "_")

    def params_schema(self) -> dict[str, Any]:
        """Build a JSON schema for this tool's kwargs by treating the
        function signature as a synthetic pydantic model.
        """
        sig = inspect.signature(self.fn)
        try:
            hints = get_type_hints(self.fn)
        except Exception:
            hints = {}
        fields: dict[str, Any] = {}
        for pname, param in sig.parameters.items():
            if pname in ("self", "cls"):
                continue
            annotation = hints.get(pname, Any)
            default = param.default if param.default is not inspect.Parameter.empty else ...
            fields[pname] = (annotation, default)
        if not fields:
            return {"type": "object", "properties": {}}
        adapter = TypeAdapter(_make_params_model(fields))
        return adapter.json_schema()

    def call(self, kwargs: dict[str, Any]) -> Any:
        return self.fn(**kwargs)


def _make_params_model(fields: dict[str, tuple[Any, Any]]):
    from pydantic import create_model

    return create_model(  # type: ignore[call-overload]
        "Params",
        **fields,
    )


REGISTRY: dict[str, ToolSpec] = {}


def tool(name: str, *, write: bool = False, tags: list[str] | None = None):
    """Register a function as a hometools tool.

    Usage:
        @tool("things.list")
        def list_things(view: str, limit: int = 200) -> list[dict]:
            '''List Things to-dos in a given view.'''
            ...
    """

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        description = (fn.__doc__ or "").strip()
        REGISTRY[name] = ToolSpec(name=name, fn=fn, write=write, tags=tags or [], description=description)
        return fn

    return deco
