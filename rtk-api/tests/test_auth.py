from __future__ import annotations

import json

from fastapi.testclient import TestClient

from rtk_api.app import create_app


def _client(monkeypatch, **env):
    monkeypatch.setenv("RTK_API_BEARER_TOKEN", env.get("bearer", "test-token"))
    if "mcp_secret" in env:
        monkeypatch.setenv("RTK_API_MCP_SECRET", env["mcp_secret"])
    else:
        monkeypatch.delenv("RTK_API_MCP_SECRET", raising=False)
    if "clients" in env:
        monkeypatch.setenv("RTK_API_CLIENTS", json.dumps(env["clients"]))
    else:
        monkeypatch.delenv("RTK_API_CLIENTS", raising=False)
    return TestClient(create_app())


#: A client scoped the way `instinct` will be: all of Things, iMessage reads,
#: and `imessage.send` reserved for the not-yet-built approval queue.
INSTINCT = {
    "instinct": {
        "token": "instinct-token",
        "allow": [
            "things.*",
            "imessage.chats",
            "imessage.recent",
            "imessage.with_contact",
            "imessage.search",
            "imessage.unread",
        ],
        "require_approval": ["imessage.send"],
    }
}


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
    monkeypatch.setenv("RTK_API_BEARER_TOKEN", "test-token")
    monkeypatch.setenv("RTK_API_MCP_SECRET", "s3cret")
    # The MCP sub-app needs its lifespan (session manager task group) to have
    # started, which only happens inside a `with TestClient(...)` context.
    with TestClient(create_app()) as client:
        resp = client.get("/s3cret/mcp")
        # No bearer/JWT provided, but the secret path should bypass auth and
        # be handled by the mounted MCP app -- the key assertion is that it
        # is NOT rejected by our auth middleware with a 401.
        assert resp.status_code != 401


def test_client_token_header_authenticates(monkeypatch):
    client = _client(monkeypatch, clients=INSTINCT)
    resp = client.post(
        "/v1/things/list",
        headers={"X-Rtk-Client-Token": "instinct-token"},
        json={"view": "today"},
    )
    assert resp.status_code != 401
    assert resp.status_code != 403


def test_client_token_via_authorization_bearer(monkeypatch):
    """Cloudflare Access may or may not forward `Authorization`; both header
    spellings must resolve to the same principal."""
    client = _client(monkeypatch, clients=INSTINCT)
    resp = client.post(
        "/v1/things/list",
        headers={"Authorization": "Bearer instinct-token"},
        json={"view": "today"},
    )
    assert resp.status_code not in (401, 403)


def test_client_denied_outside_allowlist(monkeypatch):
    client = _client(monkeypatch, clients=INSTINCT)
    resp = client.post(
        "/v1/system/ping",
        headers={"X-Rtk-Client-Token": "instinct-token"},
    )
    assert resp.status_code == 403
    assert "instinct" in resp.json()["error"]


def test_client_denied_for_approval_gated_tool(monkeypatch):
    client = _client(monkeypatch, clients=INSTINCT)
    resp = client.post(
        "/v1/imessage/send",
        headers={"X-Rtk-Client-Token": "instinct-token"},
        json={"to": "+15555550123", "text": "hi"},
    )
    assert resp.status_code == 403
    assert "approval" in resp.json()["error"]


def test_unknown_tool_outside_grant_is_403_not_404(monkeypatch):
    """A scoped client must not be able to enumerate the registry by
    probing for 404s."""
    client = _client(monkeypatch, clients=INSTINCT)
    resp = client.post(
        "/v1/secret/thing",
        headers={"X-Rtk-Client-Token": "instinct-token"},
    )
    assert resp.status_code == 403


def test_tools_listing_is_filtered_for_client(monkeypatch):
    client = _client(monkeypatch, clients=INSTINCT)
    resp = client.get("/v1/tools", headers={"X-Rtk-Client-Token": "instinct-token"})
    assert resp.status_code == 200
    names = {tool["name"] for tool in resp.json()["result"]}
    assert "things.add" in names
    assert "imessage.search" in names
    assert not any(name.startswith("system.") for name in names)
    assert "imessage.send" not in names


def test_owner_bearer_still_sees_everything(monkeypatch):
    client = _client(monkeypatch, clients=INSTINCT)
    resp = client.get("/v1/tools", headers={"Authorization": "Bearer test-token"})
    names = {tool["name"] for tool in resp.json()["result"]}
    assert "system.ping" in names
    assert "imessage.send" in names


def test_wrong_client_token_rejected(monkeypatch):
    client = _client(monkeypatch, clients=INSTINCT)
    resp = client.post(
        "/v1/things/list",
        headers={"X-Rtk-Client-Token": "not-the-token"},
        json={"view": "today"},
    )
    assert resp.status_code == 401


def test_malformed_clients_env_fails_closed(monkeypatch):
    """A bad edit to the env file must not lock the owner out."""
    monkeypatch.setenv("RTK_API_BEARER_TOKEN", "test-token")
    monkeypatch.delenv("RTK_API_MCP_SECRET", raising=False)
    monkeypatch.setenv("RTK_API_CLIENTS", "{not json")
    client = TestClient(create_app())
    assert client.post("/v1/system/ping", headers={"X-Rtk-Client-Token": "x"}).status_code == 401
    assert (
        client.post("/v1/system/ping", headers={"Authorization": "Bearer test-token"}).status_code
        == 200
    )


def test_empty_token_never_authenticates(monkeypatch):
    """A client configured with a blank token must not be matchable -- an
    empty presented token comparing equal to an empty configured one would
    authenticate a client with no credential at all."""
    client = _client(monkeypatch, clients={"broken": {"token": "", "allow": ["*"]}})
    for headers in ({"X-Rtk-Client-Token": ""}, {"Authorization": "Bearer "}):
        assert client.post("/v1/system/ping", headers=headers).status_code == 401


def test_each_client_gets_its_own_scopes(monkeypatch):
    """With more than one client configured, a token must resolve to *its*
    principal, not whichever entry the match loop visited last."""
    client = _client(
        monkeypatch,
        clients={
            "reader": {"token": "reader-token", "allow": ["things.list"]},
            "pinger": {"token": "pinger-token", "allow": ["system.ping"]},
        },
    )
    ping_as_pinger = client.post("/v1/system/ping", headers={"X-Rtk-Client-Token": "pinger-token"})
    assert ping_as_pinger.status_code == 200

    ping_as_reader = client.post("/v1/system/ping", headers={"X-Rtk-Client-Token": "reader-token"})
    assert ping_as_reader.status_code == 403
    assert "reader" in ping_as_reader.json()["error"]
