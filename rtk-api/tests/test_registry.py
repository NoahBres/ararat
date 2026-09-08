from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from rtk_api.app import create_app
from rtk_api.registry import REGISTRY, tool


def test_fake_tool_appears_in_listing_and_is_callable(monkeypatch):
    monkeypatch.setenv("RTK_API_BEARER_TOKEN", "test-token")

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


def test_spec_call_validates_and_forwards_kwargs():
    from pydantic import ValidationError

    from rtk_api.registry import ToolSpec

    def greet(name: str, times: int = 1, list: str | None = None) -> dict:
        return {"name": name, "times": times, "list": list}

    spec = ToolSpec(name="fake.greet2", fn=greet)
    assert spec.call({"name": "Noah"}) == {"name": "Noah", "times": 1, "list": None}
    assert spec.call({"name": "Noah", "times": 2, "list": "L"}) == {
        "name": "Noah",
        "times": 2,
        "list": "L",
    }
    with pytest.raises(ValidationError):
        spec.call({"name": ["nope"]})
    with pytest.raises(ValidationError):
        spec.call({"name": "Noah", "extra": 1})
    with pytest.raises(ValidationError):
        spec.call({})
    # the model is built once and reused
    assert spec.params_model() is spec.params_model()
    # and the advertised schema is derived from the same model
    assert set(spec.params_schema()["properties"]) == {"name", "times", "list"}


def test_unknown_tool_returns_404(monkeypatch):
    monkeypatch.setenv("RTK_API_BEARER_TOKEN", "test-token")
    client = TestClient(create_app())
    resp = client.post(
        "/v1/does/notexist",
        headers={"Authorization": "Bearer test-token"},
    )
    assert resp.status_code == 404
