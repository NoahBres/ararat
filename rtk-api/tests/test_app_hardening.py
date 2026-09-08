"""Security-hardening tests for the REST layer: secret redaction in logs,
argument validation, error-detail containment, and body-size limits."""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from rtk_api.app import MAX_BODY_BYTES, _JsonLogFormatter, create_app
from rtk_api.lib.imessage_db import ImessageAccessError
from rtk_api.registry import REGISTRY, tool

OWNER = {"Authorization": "Bearer test-token"}


@pytest.fixture()
def rtk_log(caplog, monkeypatch):
    """`_configure_logging` (run by `create_app`) sets propagate=False on the
    `rtk_api` logger and replaces its handler list, so caplog's root handler
    never sees its records. Wrap it so caplog's handler is attached to the
    `rtk_api` logger *after* it has been configured."""
    import rtk_api.app as app_module

    root = logging.getLogger("rtk_api")
    original = app_module._configure_logging

    def configure_and_capture() -> None:
        original()
        root.addHandler(caplog.handler)

    monkeypatch.setattr(app_module, "_configure_logging", configure_and_capture)
    try:
        yield caplog
    finally:
        root.removeHandler(caplog.handler)


def _rendered(records) -> list[str]:
    """What would actually be written to the log file."""
    fmt = _JsonLogFormatter()
    return [fmt.format(r) for r in records]


def _owner_client(monkeypatch, **env) -> TestClient:
    monkeypatch.setenv("RTK_API_BEARER_TOKEN", "test-token")
    monkeypatch.delenv("RTK_API_MCP_SECRET", raising=False)
    monkeypatch.delenv("RTK_API_CLIENTS", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return TestClient(create_app())


# ---- 1. MCP secret never reaches the log ----------------------------------


def test_mcp_secret_redacted_from_access_and_auth_logs(monkeypatch, rtk_log):
    secret = "s3cret-capability-token"
    monkeypatch.setenv("RTK_API_BEARER_TOKEN", "test-token")
    monkeypatch.setenv("RTK_API_MCP_SECRET", secret)
    with TestClient(create_app()) as client:
        assert client.get(f"/{secret}/mcp").status_code != 401
        assert client.get(f"/{secret}x").status_code == 401  # near-miss probe

    lines = _rendered(rtk_log.records)
    assert lines, "expected access/auth log records"
    assert not any(secret in line for line in lines), lines
    # both the served MCP request and the 401 were logged, redacted
    assert any('"/<mcp-secret>/mcp"' in line for line in lines), lines
    assert any('"/<mcp-secret>x"' in line for line in lines), lines


def test_redact_path_is_noop_without_secret(monkeypatch):
    from rtk_api.auth import redact_path
    from rtk_api.config import get_settings

    monkeypatch.delenv("RTK_API_MCP_SECRET", raising=False)
    assert redact_path("/v1/system/ping", get_settings()) == "/v1/system/ping"


# ---- 2. Tool arguments are validated against the advertised schema --------


def test_wrong_type_argument_is_400(monkeypatch):
    client = _owner_client(monkeypatch)
    resp = client.post("/v1/system/echo", headers=OWNER, json={"text": ["not", "a", "string"]})
    assert resp.status_code == 400
    assert resp.json()["ok"] is False
    assert "text" in resp.json()["error"]
    assert "valid string" in resp.json()["error"]


def test_unknown_kwarg_is_400(monkeypatch):
    client = _owner_client(monkeypatch)
    resp = client.post("/v1/system/echo", headers=OWNER, json={"text": "hi", "bogus": 1})
    assert resp.status_code == 400
    assert "bogus" in resp.json()["error"]
    assert "Extra inputs" in resp.json()["error"]


def test_unknown_kwarg_on_no_arg_tool_is_400(monkeypatch):
    client = _owner_client(monkeypatch)
    resp = client.post("/v1/system/ping", headers=OWNER, json={"bogus": 1})
    assert resp.status_code == 400


def test_missing_required_argument_is_400(monkeypatch):
    client = _owner_client(monkeypatch)
    resp = client.post("/v1/system/echo", headers=OWNER, json={})
    assert resp.status_code == 400
    assert "text" in resp.json()["error"]


def test_bad_literal_view_is_400(monkeypatch):
    from rtk_api.tools import things as things_tools

    monkeypatch.setattr(things_tools.things_lib, "today", lambda: [])
    client = _owner_client(monkeypatch)
    resp = client.post("/v1/things/list", headers=OWNER, json={"view": "not-a-view"})
    assert resp.status_code == 400
    assert "view" in resp.json()["error"]


def test_valid_call_still_works(monkeypatch):
    client = _owner_client(monkeypatch)
    resp = client.post("/v1/system/echo", headers=OWNER, json={"text": "hi"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "result": {"text": "hi"}}


def test_validation_preserves_defaults_none_and_builtin_shadowing_names(monkeypatch):
    """`things.add` has a parameter literally called `list`; the synthetic
    model must accept the name as-is and hand defaults / explicit None back
    to the function unchanged."""
    seen: dict = {}

    @tool("fake.shadow")
    def shadow(title: str, tags: list[str] | None = None, list: str | None = None) -> dict:
        seen.update(title=title, tags=tags, list=list)
        return {"ok": True}

    try:
        client = _owner_client(monkeypatch)
        resp = client.post("/v1/fake/shadow", headers=OWNER, json={"title": "x", "list": None})
        assert resp.status_code == 200
        assert seen == {"title": "x", "tags": None, "list": None}

        resp = client.post(
            "/v1/fake/shadow", headers=OWNER, json={"title": "x", "list": "Inbox", "tags": ["a"]}
        )
        assert resp.status_code == 200
        assert seen == {"title": "x", "tags": ["a"], "list": "Inbox"}

        resp = client.post("/v1/fake/shadow", headers=OWNER, json={"title": "x", "list": 5})
        assert resp.status_code == 400
    finally:
        REGISTRY.pop("fake.shadow", None)


# ---- 3. Internal errors don't leak detail -------------------------------


def test_internal_error_is_generic_with_request_id(monkeypatch, rtk_log):
    @tool("fake.boom")
    def boom() -> dict:
        raise RuntimeError("/Users/noah/secret/path: sqlite3 said something private")

    try:
        client = _owner_client(monkeypatch)
        resp = client.post("/v1/fake/boom", headers=OWNER)
        assert resp.status_code == 500
        body = resp.json()
        assert body["ok"] is False
        assert body["error"] == "internal error"
        assert "secret/path" not in resp.text
        assert body["request_id"] == resp.headers["X-Request-Id"]
        # ...but the full detail is in the server log, keyed by request id
        failure = next(r for r in rtk_log.records if r.getMessage() == "tool call failed")
        assert failure.request_id == body["request_id"]
        assert "secret/path" in "".join(_rendered([failure])) or "secret/path" in str(
            failure.exc_info
        )
    finally:
        REGISTRY.pop("fake.boom", None)


def test_imessage_access_error_reaches_caller_as_503(monkeypatch):
    @tool("fake.fda")
    def fda() -> dict:
        raise ImessageAccessError("Could not open chat.db -- grant Full Disk Access")

    try:
        client = _owner_client(monkeypatch)
        resp = client.post("/v1/fake/fda", headers=OWNER)
        assert resp.status_code == 503
        assert "Full Disk Access" in resp.json()["error"]
    finally:
        REGISTRY.pop("fake.fda", None)


def test_permission_error_reaches_caller_as_403(monkeypatch):
    monkeypatch.delenv("IMESSAGE_WRITE_ENABLED", raising=False)
    client = _owner_client(monkeypatch)
    resp = client.post("/v1/imessage/send", headers=OWNER, json={"to": "+15551234567", "text": "x"})
    assert resp.status_code == 403
    assert "IMESSAGE_WRITE_ENABLED" in resp.json()["error"]


def test_value_error_still_400_with_message(monkeypatch):
    monkeypatch.setenv("IMESSAGE_WRITE_ENABLED", "true")
    client = _owner_client(monkeypatch)
    resp = client.post("/v1/imessage/send", headers=OWNER, json={"to": "Kirill", "text": "x"})
    assert resp.status_code == 400
    assert "resolve the contact name first" in resp.json()["error"]


# ---- 4. Request body limit ------------------------------------------------


def test_oversized_content_length_is_413_before_parsing(monkeypatch):
    client = _owner_client(monkeypatch)
    payload = json.dumps({"text": "x" * (MAX_BODY_BYTES + 1)})
    resp = client.post("/v1/system/echo", headers=OWNER, content=payload)
    assert resp.status_code == 413
    assert resp.json()["ok"] is False


def test_oversized_chunked_body_is_413(monkeypatch):
    """No Content-Length (chunked upload): the guard on the actual read
    must still trip."""
    client = _owner_client(monkeypatch)
    chunk = b"x" * 65536

    def body():
        yield b'{"text": "'
        for _ in range(MAX_BODY_BYTES // len(chunk) + 1):
            yield chunk
        yield b'"}'

    resp = client.post(
        "/v1/system/echo",
        headers={**OWNER, "Transfer-Encoding": "chunked"},
        content=body(),
    )
    assert resp.status_code == 413


def test_body_at_limit_is_accepted(monkeypatch):
    client = _owner_client(monkeypatch)
    prefix, suffix = b'{"text": "', b'"}'
    payload = prefix + b"x" * (MAX_BODY_BYTES - len(prefix) - len(suffix)) + suffix
    assert len(payload) == MAX_BODY_BYTES
    resp = client.post("/v1/system/echo", headers=OWNER, content=payload)
    assert resp.status_code == 200
