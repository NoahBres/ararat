from __future__ import annotations

import subprocess

import pytest
from conftest_imessage import build_chat_db

from rtk_api.lib import imessage_db
from rtk_api.tools import imessage as imessage_tools


@pytest.fixture()
def chat_db(tmp_path, monkeypatch):
    db_path = tmp_path / "chat.db"
    build_chat_db(db_path)
    monkeypatch.setattr(imessage_db, "DB_PATH", db_path)
    return db_path


@pytest.fixture()
def no_contacts(monkeypatch):
    """Avoid hitting the real AddressBook/Contacts.app during read-tool
    tests that don't care about name resolution."""
    monkeypatch.setattr(imessage_tools.contacts, "_load_all_contacts", lambda: [])


def test_chats_tool(chat_db, no_contacts):
    result = imessage_tools.chats(limit=50)
    assert {c["chat_id"] for c in result} == {"chat-guid-100", "chat-guid-101", "chat-guid-102"}


def test_recent_tool_enriches_sender_name(chat_db, monkeypatch):
    monkeypatch.setattr(
        imessage_tools.contacts,
        "_load_all_contacts",
        lambda: [{"name": "Kirill", "phone": "+15551234567", "email": None}],
    )
    result = imessage_tools.recent(limit=50, days=30)
    inbound = next(r for r in result if r["sender"] == "+15551234567")
    assert inbound["sender_name"] == "Kirill"
    outbound = next(r for r in result if r["is_from_me"])
    assert outbound["sender_name"] is None


def test_with_contact_resolves_via_contacts(chat_db, monkeypatch):
    monkeypatch.setattr(
        imessage_tools.contacts,
        "resolve",
        lambda q: [{"name": "Kirill", "identifiers": ["+15551234567"]}],
    )
    result = imessage_tools.with_contact("kirill", days=30)
    texts = {r["text"] for r in result}
    assert "hey are we still on for saturday?" in texts
    assert "group logistics for the trip" not in texts


def test_search_tool(chat_db, no_contacts):
    result = imessage_tools.search("logistics", days=30)
    assert len(result) == 1


def test_unread_tool(chat_db, no_contacts):
    result = imessage_tools.unread()
    assert len(result) == 1
    assert result[0]["text"] == "unread ping"


# ---- imessage.send guards --------------------------------------------------


def test_send_refuses_when_disabled(monkeypatch):
    monkeypatch.delenv("IMESSAGE_WRITE_ENABLED", raising=False)
    with pytest.raises(PermissionError, match="disabled"):
        imessage_tools.send(to="+15551234567", text="hi")


def test_send_refuses_when_not_in_allowlist(monkeypatch):
    monkeypatch.setenv("IMESSAGE_WRITE_ENABLED", "true")
    monkeypatch.setenv("IMESSAGE_WRITE_ALLOWLIST", "+19998887777")
    monkeypatch.setattr(imessage_tools, "_resolve_send_target", lambda to: "+15551234567")
    with pytest.raises(PermissionError, match="not in IMESSAGE_WRITE_ALLOWLIST"):
        imessage_tools.send(to="+15551234567", text="hi")


def test_send_rejects_contact_name(monkeypatch):
    """No fuzzy matching on the send path: a name must be resolved to an
    identifier by the caller first, even if it would match a contact."""
    monkeypatch.setenv("IMESSAGE_WRITE_ENABLED", "true")
    monkeypatch.setenv("IMESSAGE_WRITE_ALLOWLIST", "+15551234567")

    def boom(*a, **k):
        raise AssertionError("contacts.resolve must not be consulted by imessage.send")

    monkeypatch.setattr(imessage_tools.contacts, "resolve", boom)
    with pytest.raises(ValueError, match="resolve the contact name first"):
        imessage_tools.send(to="Kirill", text="hi")


@pytest.mark.parametrize(
    ("raw", "normalised"),
    [
        ("+1 (555) 123-4567", "+15551234567"),
        ("  Friend@Example.com ", "friend@example.com"),
    ],
)
def test_send_normalises_identifier_before_allowlist(monkeypatch, raw, normalised):
    monkeypatch.setenv("IMESSAGE_WRITE_ENABLED", "true")
    monkeypatch.setenv("IMESSAGE_WRITE_ALLOWLIST", normalised)
    monkeypatch.setattr(imessage_tools.imessage_db, "is_group_identifier", lambda ident: False)
    monkeypatch.setattr(
        imessage_tools.imessage_db, "find_recent_outbound", lambda *a, **k: {"rowid": 1}
    )
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(imessage_tools.subprocess, "run", fake_run)

    result = imessage_tools.send(to=raw, text="hi")
    assert result["to"] == normalised
    assert captured["argv"][-2] == normalised


def test_send_refuses_text_too_long(monkeypatch):
    monkeypatch.setenv("IMESSAGE_WRITE_ENABLED", "true")
    monkeypatch.setenv("IMESSAGE_WRITE_ALLOWLIST", "+15551234567")
    with pytest.raises(ValueError, match="exceeds max length"):
        imessage_tools.send(to="+15551234567", text="x" * 2001)


def test_send_refuses_group_chat(monkeypatch):
    monkeypatch.setenv("IMESSAGE_WRITE_ENABLED", "true")
    monkeypatch.setenv("IMESSAGE_WRITE_ALLOWLIST", "+15551234567")
    monkeypatch.setattr(imessage_tools, "_resolve_send_target", lambda to: "+15551234567")
    monkeypatch.setattr(imessage_tools.imessage_db, "is_group_identifier", lambda ident: True)
    with pytest.raises(ValueError, match="group chat"):
        imessage_tools.send(to="+15551234567", text="hi")


def test_send_allowed_calls_osascript_with_argv_and_confirms(monkeypatch):
    monkeypatch.setenv("IMESSAGE_WRITE_ENABLED", "true")
    monkeypatch.setenv("IMESSAGE_WRITE_ALLOWLIST", "+15551234567")
    monkeypatch.setattr(imessage_tools, "_resolve_send_target", lambda to: "+15551234567")
    monkeypatch.setattr(imessage_tools.imessage_db, "is_group_identifier", lambda ident: False)
    monkeypatch.setattr(
        imessage_tools.imessage_db, "find_recent_outbound", lambda *a, **k: {"rowid": 42}
    )

    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(imessage_tools.subprocess, "run", fake_run)

    result = imessage_tools.send(to="+15551234567", text="running late, be there in 10")

    assert captured["argv"][0] == "osascript"
    assert captured["argv"][-2:] == ["+15551234567", "running late, be there in 10"]
    # never string-interpolated into the script itself
    assert "running late" not in captured["argv"][2]
    assert result == {"sent": "confirmed", "to": "+15551234567", "rowid": 42}


def test_send_unconfirmed_when_no_matching_outbound_row(monkeypatch):
    monkeypatch.setenv("IMESSAGE_WRITE_ENABLED", "true")
    monkeypatch.setenv("IMESSAGE_WRITE_ALLOWLIST", "+15551234567")
    monkeypatch.setattr(imessage_tools, "_resolve_send_target", lambda to: "+15551234567")
    monkeypatch.setattr(imessage_tools.imessage_db, "is_group_identifier", lambda ident: False)
    monkeypatch.setattr(imessage_tools.imessage_db, "find_recent_outbound", lambda *a, **k: None)
    monkeypatch.setattr(imessage_tools, "_SEND_POLL_TIMEOUT_S", 0.05)
    monkeypatch.setattr(imessage_tools, "_SEND_POLL_INTERVAL_S", 0.01)

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(imessage_tools.subprocess, "run", fake_run)

    result = imessage_tools.send(to="+15551234567", text="hi")
    assert result == {"sent": "unconfirmed", "to": "+15551234567", "rowid": None}


def test_send_raises_on_osascript_failure(monkeypatch):
    monkeypatch.setenv("IMESSAGE_WRITE_ENABLED", "true")
    monkeypatch.setenv("IMESSAGE_WRITE_ALLOWLIST", "+15551234567")
    monkeypatch.setattr(imessage_tools, "_resolve_send_target", lambda to: "+15551234567")
    monkeypatch.setattr(imessage_tools.imessage_db, "is_group_identifier", lambda ident: False)

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, returncode=1, stdout="", stderr="not authorized")

    monkeypatch.setattr(imessage_tools.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="osascript send failed"):
        imessage_tools.send(to="+15551234567", text="hi")
