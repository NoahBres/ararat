from __future__ import annotations

from fastapi.testclient import TestClient

from hometools.app import create_app


def _client(monkeypatch, **env):
    monkeypatch.setenv("HOMETOOLS_BEARER_TOKEN", env.get("bearer", "test-token"))
    if "mcp_secret" in env:
        monkeypatch.setenv("HOMETOOLS_MCP_SECRET", env["mcp_secret"])
    else:
        monkeypatch.delenv("HOMETOOLS_MCP_SECRET", raising=False)
    return TestClient(create_app())


def test_health_is_unauthenticated(monkeypatch):
    client = _client(monkeypatch)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_bearer_ok(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post(
        "/v1/system/ping",
        headers={"Authorization": "Bearer test-token"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "result": {"pong": True}}


def test_missing_auth_rejected(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/v1/system/ping")
    assert resp.status_code == 401


def test_wrong_bearer_rejected(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post(
        "/v1/system/ping",
        headers={"Authorization": "Bearer nope"},
    )
    assert resp.status_code == 401


def test_garbage_jwt_rejected(monkeypatch):
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "example.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_AUD", "some-aud")
    client = _client(monkeypatch)
    resp = client.post(
        "/v1/system/ping",
        headers={"Cf-Access-Jwt-Assertion": "not.a.jwt"},
    )
    assert resp.status_code == 401


def test_secret_path_ok(monkeypatch):
    monkeypatch.setenv("HOMETOOLS_BEARER_TOKEN", "test-token")
    monkeypatch.setenv("HOMETOOLS_MCP_SECRET", "s3cret")
    # The MCP sub-app needs its lifespan (session manager task group) to have
    # started, which only happens inside a `with TestClient(...)` context.
    with TestClient(create_app()) as client:
        resp = client.get("/s3cret/mcp")
        # No bearer/JWT provided, but the secret path should bypass auth and
        # be handled by the mounted MCP app -- the key assertion is that it
        # is NOT rejected by our auth middleware with a 401.
        assert resp.status_code != 401
