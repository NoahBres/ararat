"""FastAPI app: /health, /v1/help, /v1/tools, /v1/{tool}/{action}, and (if
RTK_API_MCP_SECRET is set) an MCP mount at /{secret}/mcp.

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
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from rtk_api import __version__

# Import side effect: populates rtk_api.registry.REGISTRY.
from rtk_api import tools as _tools  # noqa: F401
from rtk_api.approvals import request_approval
from rtk_api.audit import audit_log
from rtk_api.auth import AuthMiddleware, Principal, redact_path
from rtk_api.config import get_settings
from rtk_api.lib.imessage_db import ImessageAccessError
from rtk_api.registry import REGISTRY, ToolSpec

#: Largest request body a tool call will accept. Tool kwargs are small (the
#: biggest legitimate payload is a things.batch command list); anything past
#: this is a mistake or an attempt to exhaust memory, and gets a 413 before
#: we parse it.
MAX_BODY_BYTES = 1 * 1024 * 1024


class BodyTooLarge(Exception):
    pass


async def _read_body_limited(request: Request, limit: int = MAX_BODY_BYTES) -> bytes:
    """Read the request body, refusing early. Checks Content-Length when the
    client sent one, and also counts the bytes actually received so a
    chunked or lying client can't slip past the header check.
    """
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > limit:
                raise BodyTooLarge
        except ValueError:
            pass  # malformed header; fall through to counting the real bytes
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise BodyTooLarge
        chunks.append(chunk)
    return b"".join(chunks)


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
    root = logging.getLogger("rtk_api")
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    root.propagate = False


access_logger = logging.getLogger("rtk_api.access")


def _build_mcp_app(settings):
    """Best-effort fastmcp mount. Returns (asgi_app, lifespan) or (None,
    None) if RTK_API_MCP_SECRET isn't set or fastmcp construction fails.
    """
    if not settings.rtk_api_mcp_secret:
        return None, None
    try:
        from fastmcp import FastMCP

        mcp = FastMCP("rtk_api")
        for spec in REGISTRY.values():
            mcp.tool(spec.fn, name=spec.mcp_name, description=spec.description or None)
        mcp_asgi_app = mcp.http_app(path="/mcp")
        return mcp_asgi_app, mcp_asgi_app.lifespan
    except Exception:
        logging.getLogger("rtk_api").exception(
            "failed to build MCP app; continuing without MCP mount"
        )
        return None, None


def _callable_tools(principal: Principal | None) -> list[ToolSpec]:
    """Tools this principal may actually invoke right now: unscoped (owner /
    mcp) sees everything, a scoped client sees what `allow` grants. Shared by
    /v1/tools and /v1/help so the two surfaces can never drift apart.

    `require_approval` no longer subtracts from this list -- those tools are
    callable, just audited (see `approvals.py`), so hiding them would
    misrepresent what the caller can do.
    """
    return [spec for spec in REGISTRY.values() if principal is None or principal.permits(spec.name)]


def _audited_tools(principal: Principal | None) -> list[ToolSpec]:
    """Tools in this principal's grant that match `require_approval`. They are
    callable, but every call is recorded -- worth saying plainly rather than
    letting the caller assume nobody is looking.
    """
    if principal is None:
        return []
    return [
        spec
        for spec in REGISTRY.values()
        if principal.permits(spec.name) and principal.needs_approval(spec.name)
    ]


def _example_value(schema: dict, name: str) -> Any:
    kind = schema.get("type")
    if kind == "string":
        return f"<{name}>"
    if kind == "integer":
        return 0
    if kind == "number":
        return 0
    if kind == "boolean":
        return True
    if kind == "array":
        return []
    if kind == "object":
        return {}
    return f"<{name}>"


def _example_body(spec: ToolSpec) -> dict:
    schema = spec.params_schema()
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    return {
        pname: _example_value(pschema, pname)
        for pname, pschema in properties.items()
        if pname in required
    }


def _render_help_markdown(principal: Principal | None, *, base_url: str) -> str:
    """The bootstrap document: who you are, how to call things, and exactly
    what you're allowed to call -- scoped to this principal's grant, so a
    client with a narrow `allow` list never sees tools (or their schemas)
    outside it. Regenerated per-request from the live registry, so it can
    never drift out of sync with what /v1/tools or a real call would do.
    """
    name = principal.name if principal else "owner"
    kind = principal.kind if principal else "unscoped"
    scoped = principal is not None and principal.allow != ("*",)

    lines: list[str] = []
    lines.append("# rtk-api")
    lines.append("")
    lines.append(
        "Private personal API exposing tools (Things 3, iMessage, ...) over HTTP. "
        "This document is generated for **you specifically** -- it only lists what "
        f"principal `{name}` (auth kind: `{kind}`) is currently allowed to call."
    )
    lines.append("")
    lines.append("## Calling a tool")
    lines.append("")
    lines.append(
        "Every tool `foo.bar` is `POST " + base_url + "/v1/foo/bar` with a JSON object "
        "body of keyword arguments. Every response is one of:"
    )
    lines.append("")
    lines.append("```json")
    lines.append('{"ok": true, "result": <anything>}')
    lines.append('{"ok": false, "error": "<message>"}')
    lines.append("```")
    lines.append("")
    lines.append(
        "Send the same auth headers you used to fetch this document on every call -- "
        "there is no session, each request is authenticated independently."
    )
    lines.append("")

    callable_tools = sorted(_callable_tools(principal), key=lambda s: s.name)
    audited_tools = sorted(_audited_tools(principal), key=lambda s: s.name)

    if not callable_tools:
        lines.append(
            "**You currently have no callable tools.** Either your grant is empty or "
            "misconfigured -- check with the owner."
        )
        lines.append("")

    for spec in callable_tools:
        route = "/v1/" + spec.name.replace(".", "/")
        lines.append(f"### `{spec.name}`{'  _(write)_' if spec.write else ''}")
        lines.append("")
        if spec.description:
            lines.append(spec.description)
            lines.append("")
        lines.append(f"`POST {base_url}{route}`")
        lines.append("")
        lines.append("```sh")
        lines.append(
            f"curl -s {base_url}{route} \\\n"
            '  -H "<your auth headers>" -H "content-type: application/json" \\\n'
            f"  -d '{json.dumps(_example_body(spec))}'"
        )
        lines.append("```")
        lines.append("")

    if audited_tools:
        lines.append("## Audited")
        lines.append("")
        lines.append(
            "These are callable like anything else above, but they match a "
            "`require_approval` rule, so every call is written to the server's audit "
            "log with its arguments and reviewed after the fact. Nothing blocks them "
            "and nothing prompts -- treat them as actions someone will read back."
        )
        lines.append("")
        for spec in audited_tools:
            lines.append(f"- `{spec.name}`")
        lines.append("")

    lines.append("## Machine-readable schema")
    lines.append("")
    lines.append(
        f"`GET {base_url}/v1/tools` (same auth) returns this same list -- scoped the same "
        "way -- as JSON with full parameter schemas, if you want to validate arguments "
        "before calling rather than reading prose."
    )
    lines.append("")
    lines.append(
        "This document itself is content-negotiated: `Accept: text/markdown` (the "
        "default) returns this prose; `Accept: application/json` wraps it as "
        '`{"ok": true, "result": "<this text>"}`.'
    )
    lines.append("")

    if scoped:
        lines.append(
            "**Scope note:** you are a scoped client. Tools outside your grant are "
            "invisible here and on `/v1/tools`, and calling one directly returns 403 "
            'rather than 404 -- so a 403 on an unlisted tool means "not yours", not '
            '"typo".'
        )
        lines.append("")

    return "\n".join(lines)


def create_app() -> FastAPI:
    _configure_logging()
    settings = get_settings()

    mcp_asgi_app, mcp_lifespan = _build_mcp_app(settings)

    app = FastAPI(title="rtk-api", version=__version__, lifespan=mcp_lifespan)
    app.add_middleware(AuthMiddleware, settings=settings)

    if mcp_asgi_app is not None:
        app.mount(f"/{settings.rtk_api_mcp_secret}", mcp_asgi_app)

    @app.middleware("http")
    async def _log_requests(request: Request, call_next):
        request_id = uuid_lib.uuid4().hex[:12]
        # Exposed so handlers can echo it in error responses -- the caller
        # gets a handle to correlate with the server log without the server
        # having to leak exception detail.
        request.state.request_id = request_id
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        principal = getattr(request.state, "principal", None)
        access_logger.info(
            "request",
            extra={
                "request_id": request_id,
                "principal": principal.name if principal else None,
                "path": redact_path(request.url.path, settings),
                "duration_ms": duration_ms,
            },
        )
        response.headers["X-Request-Id"] = request_id
        return response

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True, "version": __version__}

    @app.get("/v1/tools")
    async def list_tools(request: Request) -> dict:
        principal = getattr(request.state, "principal", None)
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
                for spec in _callable_tools(principal)
            ],
        }

    @app.get("/v1/help")
    async def help_doc(request: Request):
        principal = getattr(request.state, "principal", None)
        markdown = _render_help_markdown(principal, base_url=str(request.base_url).rstrip("/"))
        accept = request.headers.get("accept", "")
        if "application/json" in accept and "text/markdown" not in accept:
            return JSONResponse({"ok": True, "result": markdown})
        return PlainTextResponse(markdown, media_type="text/markdown")

    @app.post("/v1/{tool_name}/{action}")
    async def call_tool(tool_name: str, action: str, request: Request) -> JSONResponse:
        full_name = f"{tool_name}.{action}"
        principal = getattr(request.state, "principal", None)

        # Authorize before the 404 so a scoped client can't enumerate which
        # tools exist outside its grant.
        #
        # `require_approval` no longer refuses here. It routes through
        # `approvals.request_approval` further down -- after the body is
        # parsed, so the audit record carries the actual arguments. Still
        # requires `allow`: approval is a second question asked of calls that
        # are already permitted, not a way around scoping.
        if principal is not None and not principal.permits(full_name):
            return JSONResponse(
                {"ok": False, "error": f"forbidden: {principal.name} may not call {full_name!r}"},
                status_code=403,
            )

        spec = REGISTRY.get(full_name)
        if spec is None:
            return JSONResponse(
                {"ok": False, "error": f"unknown tool {full_name!r}"}, status_code=404
            )

        try:
            body_bytes = await _read_body_limited(request)
        except BodyTooLarge:
            return JSONResponse(
                {"ok": False, "error": f"request body exceeds {MAX_BODY_BYTES} bytes"},
                status_code=413,
            )
        kwargs: dict[str, Any] = {}
        if body_bytes:
            try:
                parsed = json.loads(body_bytes)
            except json.JSONDecodeError as exc:
                return JSONResponse(
                    {"ok": False, "error": f"invalid JSON body: {exc}"}, status_code=400
                )
            if not isinstance(parsed, dict):
                return JSONResponse(
                    {"ok": False, "error": "JSON body must be an object of kwargs"}, status_code=400
                )
            kwargs = parsed

        principal_name = principal.name if principal else None

        if principal is not None and principal.needs_approval(full_name):
            if not request_approval(full_name, principal_name, kwargs):
                return JSONResponse(
                    {"ok": False, "error": f"{full_name!r} was not approved"},
                    status_code=403,
                )

        if spec.write:
            audit_log(full_name, principal_name, kwargs)

        try:
            # Tools are sync and may block on disk / subprocesses / TCC prompts;
            # run them in the threadpool so the event loop (and /health) stay live.
            result = await run_in_threadpool(spec.call, kwargs)
        except ValidationError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        except TypeError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        except ImessageAccessError as exc:
            # Operational, not secret: the message is the FDA how-to, which
            # is exactly what the caller needs to see to get unstuck.
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)
        except PermissionError as exc:
            # Tool-level refusals (kill switch off, recipient not allowlisted)
            # are deliberate messages for the caller, not internal state.
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=403)
        except Exception:
            request_id = getattr(request.state, "request_id", None)
            logging.getLogger("rtk_api").exception(
                "tool call failed", extra={"tool": full_name, "request_id": request_id}
            )
            # Never echo str(exc): tracebacks / paths / env detail stay in the
            # server log, keyed by request_id for correlation.
            return JSONResponse(
                {"ok": False, "error": "internal error", "request_id": request_id},
                status_code=500,
            )

        return JSONResponse({"ok": True, "result": result})

    return app


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(prog="rtk-api")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Run the rtk-api HTTP server")
    serve_parser.add_argument("--host", default=None)
    serve_parser.add_argument("--port", type=int, default=None)

    args = parser.parse_args()

    if args.command == "serve":
        import uvicorn

        settings = get_settings()
        host = args.host or settings.rtk_api_host
        port = args.port or settings.rtk_api_port
        uvicorn.run("rtk_api.app:app", host=host, port=port, log_config=None)


if __name__ == "__main__":
    main()
