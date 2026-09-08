"""Basic system tools: ping, version, echo. Useful for smoke-testing the
server and MCP wiring without touching Things or iMessage.
"""

from __future__ import annotations

from rtk_api import __version__
from rtk_api.registry import tool


@tool("system.ping")
def ping() -> dict:
    """Liveness check. Returns {"pong": true}."""
    return {"pong": True}


@tool("system.version")
def version() -> dict:
    """Return the running rtk-api version."""
    return {"version": __version__}


@tool("system.echo")
def echo(text: str) -> dict:
    """Echo back the given text. Useful for testing the REST/MCP plumbing."""
    return {"text": text}
