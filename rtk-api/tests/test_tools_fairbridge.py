"""Tests for the Fairbridge group-chat tools.

The thing actually worth protecting here is the scoping: `fairbridge.send`
has no recipient parameter, and the chat it resolves to must be the exact
configured participant set -- not a superset, not a stale duplicate, not
"whatever chat those numbers appear in".
"""

from __future__ import annotations

import sqlite3
import subprocess
from datetime import UTC, datetime

import pytest
from conftest_imessage import build_chat_db, dt_to_apple_ns

from rtk_api.config import get_settings
from rtk_api.lib import imessage_db
from rtk_api.registry import REGISTRY
from rtk_api.tools import fairbridge as fb

#: handles 5/6/7 in the fixture -- chats 103 (stale) and 104 (current).
FAIRBRIDGE_THREE = "+15558880001,+15558880002,+15558880003"


@pytest.fixture()
def chat_db(tmp_path, monkeypatch):
    db_path = tmp_path / "chat.db"
    build_chat_db(db_path)
    monkeypatch.setattr(imessage_db, "DB_PATH", db_path)
    return db_path


@pytest.fixture()
def no_contacts(monkeypatch):
    monkeypatch.setattr(fb.contacts, "_load_all_contacts", lambda: [])


@pytest.fixture()
def configured(monkeypatch):
    """Participants set, writes off -- the safe default state."""
    monkeypatch.setenv("FAIRBRIDGE_PARTICIPANTS", FAIRBRIDGE_THREE)
    monkeypatch.setenv("FAIRBRIDGE_WRITE_ENABLED", "false")
    get_settings.cache_clear()


# ---- chat resolution -------------------------------------------------------


def test_resolves_to_most_recently_active_duplicate(chat_db, configured):
    chat = fb._resolve_chat()
    assert chat["chat_guid"] == "chat-guid-104"
    assert chat["chat_name"] == "Fairbridge"
    assert chat["participants"] == ["+15558880001", "+15558880002", "+15558880003"]


def test_superset_chat_never_matches(chat_db, monkeypatch):
    """The same three people plus a fourth is a different conversation."""
    monkeypatch.setenv("FAIRBRIDGE_PARTICIPANTS", FAIRBRIDGE_THREE + ",+15558880004")
    get_settings.cache_clear()
    assert fb._resolve_chat()["chat_guid"] == "chat-guid-105-superset"

    monkeypatch.setenv("FAIRBRIDGE_PARTICIPANTS", FAIRBRIDGE_THREE)
    get_settings.cache_clear()
    assert fb._resolve_chat()["chat_guid"] == "chat-guid-104"


def test_participants_normalised_before_matching(chat_db, monkeypatch):
    """Numbers written how a human would write them still match chat.db's E.164."""
    monkeypatch.setenv(
        "FAIRBRIDGE_PARTICIPANTS", "+1 (555) 888-0001, +1-555-888-0002, +15558880003"
    )
    get_settings.cache_clear()
    assert fb._resolve_chat()["chat_guid"] == "chat-guid-104"


def test_unconfigured_participants_refuses(chat_db, monkeypatch):
    # Set to "" rather than deleted: get_settings() re-injects any key missing
    # from os.environ out of ~/.config/rtk-api/env, which on rtk defines this
    # one -- a delenv would be silently undone there.
    monkeypatch.setenv("FAIRBRIDGE_PARTICIPANTS", "")
    get_settings.cache_clear()
    with pytest.raises(PermissionError, match="FAIRBRIDGE_PARTICIPANTS"):
        fb._resolve_chat()


def test_no_matching_chat_raises(chat_db, monkeypatch):
    monkeypatch.setenv("FAIRBRIDGE_PARTICIPANTS", "+15550000001,+15550000002")
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="no group chat found"):
        fb._resolve_chat()


# ---- read ------------------------------------------------------------------


def test_read_returns_only_that_chat(chat_db, configured, no_contacts):
    rows = fb.read(limit=50, days=30)
    texts = {r["text"] for r in rows}
    assert "whose turn to take out the trash" in texts
    assert "mine, on it" in texts
    # The superset chat and every unrelated chat stay out.
    assert "superset chat, not fairbridge" not in texts
    assert "group logistics for the trip" not in texts
    assert "hey are we still on for saturday?" not in texts
    assert {r["chat_id"] for r in rows} == {"chat-guid-104"}


def test_read_enriches_sender_names(chat_db, configured, monkeypatch):
    monkeypatch.setattr(
        fb.contacts,
        "_load_all_contacts",
        lambda: [{"name": "Andrew", "phone": "+15558880002", "email": None}],
    )
    rows = fb.read(limit=50, days=30)
    inbound = next(r for r in rows if r["sender"] == "+15558880002")
    assert inbound["sender_name"] == "Andrew"
    assert next(r for r in rows if r["is_from_me"])["sender_name"] is None


def test_info_reports_target_and_write_state(chat_db, configured, no_contacts):
    result = fb.info()
    assert result["chat_guid"] == "chat-guid-104"
    assert result["write_enabled"] is False
    assert result["message_count"] == 2


# ---- send ------------------------------------------------------------------


def test_send_refuses_when_disabled(chat_db, configured, monkeypatch):
    called = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: called.append(a))
    with pytest.raises(PermissionError, match="FAIRBRIDGE_WRITE_ENABLED"):
        fb.send("hello")
    assert called == []


def test_send_takes_no_recipient_parameter():
    """The whole point of this tool: an agent cannot redirect it."""
    schema = REGISTRY["fairbridge.send"].params_schema()
    assert set(schema["properties"]) == {"text"}


def _enable_writes(monkeypatch):
    monkeypatch.setenv("FAIRBRIDGE_WRITE_ENABLED", "true")
    get_settings.cache_clear()


def test_send_targets_the_resolved_chat_guid(chat_db, configured, monkeypatch):
    _enable_writes(monkeypatch)
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = fb.send("dinner at 7?")

    # argv order is [osascript, -e, script, chat_guid, text] -- the guid is
    # passed as an argument, never interpolated into the script source.
    assert seen["argv"][-2:] == ["chat-guid-104", "dinner at 7?"]
    assert "dinner at 7?" not in seen["argv"][2]
    assert result["sent"] == "unconfirmed"  # nothing wrote to the fixture db
    assert result["chat_name"] == "Fairbridge"


def test_send_confirms_via_the_real_chat_db_query(chat_db, configured, monkeypatch):
    """End-to-end confirmation against the actual sqlite query, not a stub.

    Regression guard for the timestamp ordering: Messages writes the chat.db
    row *during* the osascript call, so if `sent_at` were captured after
    `subprocess.run` returned it would sit past the row's own date and
    `m.date >= since` would filter out the message we just sent -- a
    successful send reported as "unconfirmed" forever. The fake send below
    inserts the row mid-call, exactly as Messages does.
    """
    _enable_writes(monkeypatch)

    def fake_run(argv, **kwargs):
        conn = sqlite3.connect(chat_db)
        conn.execute(
            "INSERT INTO message (ROWID, guid, text, handle_id, date, is_from_me, is_read) "
            "VALUES (900, 'm900', ?, NULL, ?, 1, 1)",
            (argv[-1], dt_to_apple_ns(datetime.now(UTC))),
        )
        conn.execute("INSERT INTO chat_message_join (chat_id, message_id) VALUES (104, 900)")
        conn.commit()
        conn.close()
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = fb.send("on my way")
    assert result["sent"] == "confirmed"
    assert result["rowid"] == 900


def test_send_ignores_an_outbound_message_in_a_different_chat(chat_db, configured, monkeypatch):
    """Same text, same moment, wrong chat -- confirmation must not claim it."""
    _enable_writes(monkeypatch)

    def fake_run(argv, **kwargs):
        conn = sqlite3.connect(chat_db)
        conn.execute(
            "INSERT INTO message (ROWID, guid, text, handle_id, date, is_from_me, is_read) "
            "VALUES (901, 'm901', ?, NULL, ?, 1, 1)",
            (argv[-1], dt_to_apple_ns(datetime.now(UTC))),
        )
        # chat 105 is the near-miss superset, not the Fairbridge chat.
        conn.execute("INSERT INTO chat_message_join (chat_id, message_id) VALUES (105, 901)")
        conn.commit()
        conn.close()
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert fb.send("on my way")["sent"] == "unconfirmed"


def test_send_rejects_empty_and_overlong_text(chat_db, configured, monkeypatch):
    _enable_writes(monkeypatch)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("should not send"))
    with pytest.raises(ValueError, match="empty"):
        fb.send("   ")
    with pytest.raises(ValueError, match="max length"):
        fb.send("x" * (fb.MAX_SEND_TEXT_LEN + 1))


def test_send_raises_when_osascript_fails(chat_db, configured, monkeypatch):
    _enable_writes(monkeypatch)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **k: subprocess.CompletedProcess(argv, 1, "", "Messages got an error"),
    )
    with pytest.raises(RuntimeError, match="Messages got an error"):
        fb.send("nope")


def test_send_is_registered_as_a_write_tool():
    assert REGISTRY["fairbridge.send"].write is True
    assert REGISTRY["fairbridge.read"].write is False
    assert REGISTRY["fairbridge.info"].write is False
