from __future__ import annotations

from datetime import datetime

import pytest

from hometools.tools import things as things_tools

# --------------------------------------------------------------------------
# build_things_url (pure, no subprocess/DB involved)
# --------------------------------------------------------------------------


def test_build_url_basic():
    url = things_tools.build_things_url("add", {"title": "buy milk"})
    assert url == "things:///add?title=buy%20milk"


def test_build_url_skips_none_values():
    url = things_tools.build_things_url("add", {"title": "x", "notes": None})
    assert url == "things:///add?title=x"


def test_build_url_encodes_booleans():
    url = things_tools.build_things_url(
        "update", {"id": "abc", "completed": True, "canceled": False}
    )
    assert url == "things:///update?id=abc&completed=true&canceled=false"


def test_build_url_encodes_special_characters():
    url = things_tools.build_things_url("add", {"title": "a & b / c", "notes": "line1\nline2"})
    assert "title=a%20%26%20b%20%2F%20c" in url
    assert "notes=line1%0Aline2" in url


def test_build_url_no_params_has_no_query_string():
    url = things_tools.build_things_url("json", {})
    assert url == "things:///json"


# --------------------------------------------------------------------------
# things.add
# --------------------------------------------------------------------------


def test_add_builds_expected_url_and_confirms(monkeypatch):
    captured = {}

    def fake_open_url(url):
        captured["url"] = url

    monkeypatch.setattr(things_tools, "_open_url", fake_open_url)
    monkeypatch.setattr(
        things_tools.things_lib,
        "tasks",
        lambda **kwargs: [
            {
                "uuid": "new-uuid",
                "title": "buy milk",
                "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        ],
    )

    result = things_tools.add(
        "buy milk",
        notes="2%",
        when="today",
        tags=["errand", "home"],
        checklist=["item1", "item2"],
    )

    assert result == {"uuid": "new-uuid", "title": "buy milk", "confirmed": True}
    url = captured["url"]
    assert url.startswith("things:///add?")
    assert "title=buy%20milk" in url
    assert "notes=2%25" in url
    assert "when=today" in url
    assert "tags=errand%2Chome" in url
    assert "checklist-items=item1%0Aitem2" in url


def test_add_unconfirmed_when_poll_finds_nothing(monkeypatch):
    monkeypatch.setattr(things_tools, "_open_url", lambda url: None)
    monkeypatch.setattr(things_tools.things_lib, "tasks", lambda **kwargs: [])
    monkeypatch.setattr(things_tools, "_poll_for_match", lambda *a, **k: None)

    result = things_tools.add("buy milk")
    assert result == {"uuid": None, "title": "buy milk", "confirmed": False}


# --------------------------------------------------------------------------
# things.update / things.complete
# --------------------------------------------------------------------------


def test_update_requires_auth_token(monkeypatch):
    monkeypatch.delenv("THINGS_AUTH_TOKEN", raising=False)
    with pytest.raises(ValueError, match="THINGS_AUTH_TOKEN"):
        things_tools.update("abc123", title="new title")


def test_update_builds_expected_url_and_rereads(monkeypatch):
    monkeypatch.setenv("THINGS_AUTH_TOKEN", "secret-token")
    monkeypatch.setattr(things_tools.time, "sleep", lambda s: None)
    captured = {}

    def fake_open_url(url):
        captured["url"] = url

    monkeypatch.setattr(things_tools, "_open_url", fake_open_url)
    monkeypatch.setattr(
        things_tools.things_lib,
        "get",
        lambda uuid: {"uuid": uuid, "type": "to-do", "title": "renamed", "status": "incomplete"},
    )

    result = things_tools.update("abc123", title="renamed", completed=True, add_tags=["urgent"])

    url = captured["url"]
    assert url.startswith("things:///update?")
    assert "auth-token=secret-token" in url
    assert "id=abc123" in url
    assert "title=renamed" in url
    assert "completed=true" in url
    assert "add-tags=urgent" in url
    assert result == {"uuid": "abc123", "type": "to-do", "title": "renamed", "status": "incomplete"}


def test_complete_delegates_to_update(monkeypatch):
    called = {}

    def fake_update(uuid, **kwargs):
        called["uuid"] = uuid
        called["kwargs"] = kwargs
        return {"uuid": uuid, "status": "completed"}

    monkeypatch.setattr(things_tools, "update", fake_update)
    result = things_tools.complete("abc123")
    assert called == {"uuid": "abc123", "kwargs": {"completed": True}}
    assert result == {"uuid": "abc123", "status": "completed"}


# --------------------------------------------------------------------------
# things.add_project
# --------------------------------------------------------------------------


def test_add_project_builds_expected_url_and_confirms(monkeypatch):
    captured = {}

    def fake_open_url(url):
        captured["url"] = url

    monkeypatch.setattr(things_tools, "_open_url", fake_open_url)
    monkeypatch.setattr(
        things_tools.things_lib,
        "tasks",
        lambda **kwargs: [
            {
                "uuid": "proj-uuid",
                "title": "Q4 Planning",
                "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        ],
    )

    result = things_tools.add_project(
        "Q4 Planning",
        area="Work",
        todos=["kickoff", "budget"],
    )

    assert result == {"uuid": "proj-uuid", "title": "Q4 Planning", "confirmed": True}
    url = captured["url"]
    assert url.startswith("things:///add-project?")
    assert "title=Q4%20Planning" in url
    assert "area=Work" in url
    assert "to-dos=kickoff%0Abudget" in url


# --------------------------------------------------------------------------
# things.batch
# --------------------------------------------------------------------------


def test_batch_requires_auth_token(monkeypatch):
    monkeypatch.delenv("THINGS_AUTH_TOKEN", raising=False)
    with pytest.raises(ValueError, match="THINGS_AUTH_TOKEN"):
        things_tools.batch([{"type": "to-do", "attributes": {"title": "x"}}])


def test_batch_rejects_empty_or_malformed_commands(monkeypatch):
    monkeypatch.setenv("THINGS_AUTH_TOKEN", "secret-token")
    with pytest.raises(ValueError):
        things_tools.batch([])
    with pytest.raises(ValueError):
        things_tools.batch([{"type": "to-do"}])
    with pytest.raises(ValueError):
        things_tools.batch("not-a-list")  # type: ignore[arg-type]


def test_batch_builds_expected_url(monkeypatch):
    monkeypatch.setenv("THINGS_AUTH_TOKEN", "secret-token")
    captured = {}
    monkeypatch.setattr(things_tools, "_open_url", lambda url: captured.__setitem__("url", url))

    commands = [{"type": "to-do", "attributes": {"title": "buy milk"}}]
    result = things_tools.batch(commands)

    assert result == {"submitted": 1}
    url = captured["url"]
    assert url.startswith("things:///json?auth-token=secret-token&data=")


# --------------------------------------------------------------------------
# poll/confirm logic
# --------------------------------------------------------------------------


def test_poll_for_match_finds_recent_item(monkeypatch):
    monkeypatch.setattr(things_tools.time, "sleep", lambda s: None)
    now = datetime.now()
    item = {"title": "buy milk", "created": now.strftime("%Y-%m-%d %H:%M:%S")}

    result = things_tools._poll_for_match(
        lambda: [item], "buy milk", now, timeout=1.0, interval=0.01
    )
    assert result == item


def test_poll_for_match_ignores_stale_item(monkeypatch):
    monkeypatch.setattr(things_tools.time, "sleep", lambda s: None)
    old = datetime(2000, 1, 1)
    now = datetime.now()
    item = {"title": "buy milk", "created": old.strftime("%Y-%m-%d %H:%M:%S")}

    result = things_tools._poll_for_match(
        lambda: [item], "buy milk", now, timeout=0.05, interval=0.01
    )
    assert result is None


def test_poll_for_match_times_out_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(things_tools.time, "sleep", lambda s: None)
    result = things_tools._poll_for_match(
        lambda: [], "nope", datetime.now(), timeout=0.05, interval=0.01
    )
    assert result is None
