from __future__ import annotations

from fastapi.testclient import TestClient

from hometools.app import create_app
from hometools.registry import REGISTRY, tool


def test_fake_tool_appears_in_listing_and_is_callable(monkeypatch):
    monkeypatch.setenv("HOMETOOLS_BEARER_TOKEN", "test-token")

    @tool("fake.greet")
    def greet(name: str) -> dict:
        """Say hello to name."""
        return {"greeting": f"hello {name}"}

    try:
        client = TestClient(create_app())

        listing = client.get("/v1/tools", headers={"Authorization": "Bearer test-token"})
        assert listing.status_code == 200
        names = [t["name"] for t in listing.json()["result"]]
        assert "fake.greet" in names

        resp = client.post(
            "/v1/fake/greet",
            json={"name": "Noah"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "result": {"greeting": "hello Noah"}}
    finally:
        REGISTRY.pop("fake.greet", None)


def test_unknown_tool_returns_404(monkeypatch):
    monkeypatch.setenv("HOMETOOLS_BEARER_TOKEN", "test-token")
    client = TestClient(create_app())
    resp = client.post(
        "/v1/does/notexist",
        headers={"Authorization": "Bearer test-token"},
    )
    assert resp.status_code == 404
