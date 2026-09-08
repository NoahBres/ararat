"""FastAPI app: /health, /v1/tools, /v1/{tool}/{action}, and (if
HOMETOOLS_MCP_SECRET is set) an MCP mount at /{secret}/mcp.

REST is the primary deliverable for Phase 1; the MCP mount is best-effort
and guarded so the server runs fine without it configured.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import uuid as uuid_lib
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from hometools import __version__

# Import side effect: populates hometools.registry.REGISTRY.
from hometools import tools as _tools  # noqa: F401
from hometools.audit import audit_log
from hometools.auth import AuthMiddleware
from hometools.config import get_settings
from hometools.registry import REGISTRY


class _JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "principal", "tool", "duration_ms", "path", "ip"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, default=str)


def _configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonLogFormatter())
    root = logging.getLogger("hometools")
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    root.propagate = False


access_logger = logging.getLogger("hometools.access")


def _build_mcp_app(settings):
    """Best-effort fastmcp mount. Returns (asgi_app, lifespan) or (None,
    None) if HOMETOOLS_MCP_SECRET isn't set or fastmcp construction fails.
    """
    if not settings.hometools_mcp_secret:
        return None, None
    try:
        from fastmcp import FastMCP

        mcp = FastMCP("hometools")
        for spec in REGISTRY.values():
            mcp.tool(spec.fn, name=spec.mcp_name, description=spec.description or None)
        mcp_asgi_app = mcp.http_app(path="/mcp")
        return mcp_asgi_app, mcp_asgi_app.lifespan
    except Exception:
        logging.getLogger("hometools").exception("failed to build MCP app; continuing without MCP mount")
        return None, None


def create_app() -> FastAPI:
    _configure_logging()
    settings = get_settings()

    mcp_asgi_app, mcp_lifespan = _build_mcp_app(settings)

    app = FastAPI(title="hometools", version=__version__, lifespan=mcp_lifespan)
    app.add_middleware(AuthMiddleware, settings=settings)

    if mcp_asgi_app is not None:
        app.mount(f"/{settings.hometools_mcp_secret}", mcp_asgi_app)

    @app.middleware("http")
    async def _log_requests(request: Request, call_next):
        request_id = uuid_lib.uuid4().hex[:12]
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        principal = getattr(request.state, "principal", None)
        access_logger.info(
            "request",
            extra={
                "request_id": request_id,
                "principal": principal,
                "path": request.url.path,
                "duration_ms": duration_ms,
            },
        )
        response.headers["X-Request-Id"] = request_id
        return response

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True, "version": __version__}

    @app.get("/v1/tools")
    async def list_tools() -> dict:
        return {
            "ok": True,
            "result": [
                {
                    "name": spec.name,
                    "description": spec.description,
                    "write": spec.write,
                    "tags": spec.tags,
                    "params_schema": spec.params_schema(),
                }
                for spec in REGISTRY.values()
            ],
        }

    @app.post("/v1/{tool_name}/{action}")
    async def call_tool(tool_name: str, action: str, request: Request) -> JSONResponse:
        full_name = f"{tool_name}.{action}"
        spec = REGISTRY.get(full_name)
        if spec is None:
            return JSONResponse({"ok": False, "error": f"unknown tool {full_name!r}"}, status_code=404)

        body_bytes = await request.body()
        kwargs: dict[str, Any] = {}
        if body_bytes:
            try:
                parsed = json.loads(body_bytes)
            except json.JSONDecodeError as exc:
                return JSONResponse({"ok": False, "error": f"invalid JSON body: {exc}"}, status_code=400)
            if not isinstance(parsed, dict):
                return JSONResponse({"ok": False, "error": "JSON body must be an object of kwargs"}, status_code=400)
            kwargs = parsed

        principal = getattr(request.state, "principal", None)

        if spec.write:
            audit_log(full_name, principal, kwargs)

        try:
            result = spec.call(kwargs)
        except ValidationError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        except TypeError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        except Exception as exc:
            logging.getLogger("hometools").exception("tool call failed", extra={"tool": full_name})
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

        return JSONResponse({"ok": True, "result": result})

    return app


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(prog="hometools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Run the hometools HTTP server")
    serve_parser.add_argument("--host", default=None)
    serve_parser.add_argument("--port", type=int, default=None)

    args = parser.parse_args()

    if args.command == "serve":
        import uvicorn

        settings = get_settings()
        host = args.host or settings.hometools_host
        port = args.port or settings.hometools_port
        uvicorn.run("hometools.app:app", host=host, port=port, log_config=None)


if __name__ == "__main__":
    main()
