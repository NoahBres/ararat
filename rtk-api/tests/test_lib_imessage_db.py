from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest_imessage import build_chat_db

from rtk_api.lib import imessage_db
from rtk_api.lib.imessage_db import decode_attributed_body


@pytest.fixture()
def chat_db(tmp_path, monkeypatch):
    db_path = tmp_path / "chat.db"
    build_chat_db(db_path)
    monkeypatch.setattr(imessage_db, "DB_PATH", db_path)
    return db_path


def test_decode_attributed_body_extracts_readable_text():
    blob = (
        b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01+"
        b"NSAttributedString\x00\x84\x01+"
        b"NSString\x01\x84\x01+"
        b"Hello from the test suite\x86"
    )
    # Known quirk inherited from tools/imessage-query.py: the leading "+"
    # byte plus the char after it (an NSArchiver object-ref marker in real
    # data) gets stripped by `_clean`, so the first letter of the payload
    # that immediately follows a "+" is eaten too.
    assert imessage_db.decode_attributed_body(blob) == "ello from the test suite"


def test_decode_attributed_body_empty_blob_returns_empty_string():
    assert imessage_db.decode_attributed_body(b"") == ""


def test_decode_attributed_body_pure_noise_returns_empty_string():
    blob = b"\x04\x0bstreamtyped\x81\xe8\x03NSObject\x84NSDictionary\x84"
    assert imessage_db.decode_attributed_body(blob) == ""


def test_open_missing_db_raises_clear_fda_error(tmp_path, monkeypatch):
    monkeypatch.setattr(imessage_db, "DB_PATH", tmp_path / "does-not-exist.db")
    with pytest.raises(imessage_db.ImessageAccessError, match="Full Disk Access"):
        imessage_db.recent_messages()


def test_recent_messages_newest_first(chat_db):
    rows = imessage_db.recent_messages(limit=50, days=30)
    dates = [r["date_utc"] for r in rows]
    assert dates == sorted(dates, reverse=True)
    # every fixture message except m8, which is 400 days old
    assert len(rows) == 10


def test_recent_messages_days_filter_excludes_old(chat_db):
    rows = imessage_db.recent_messages(limit=50, days=2)
    texts = {r["text"] for r in rows}
    # the group-chat messages are 5 days old and should be excluded
    assert "sounds good" not in texts
    assert "group logistics for the trip" not in texts
    # the unread ping (0.1 days old) should be included
    assert "unread ping" in texts


def test_recent_messages_decodes_attributed_body(chat_db):
    rows = imessage_db.recent_messages(limit=50, days=30)
    decoded = [r["text"] for r in rows if "Main St" in (r["text"] or "")]
    assert decoded == ["ere is the address 123 Main St"]


def test_recent_messages_includes_attachments(chat_db):
    rows = imessage_db.recent_messages(limit=50, days=30)
    photo_row = next(r for r in rows if r["text"] == "photo incoming")
    assert photo_row["attachments"] == ["image/jpeg"]
    other_row = next(r for r in rows if r["text"] == "yep see you then")
    assert other_row["attachments"] == []


def test_recent_messages_sender_and_is_from_me(chat_db):
    rows = imessage_db.recent_messages(limit=50, days=30)
    outbound = next(r for r in rows if r["text"] == "yep see you then")
    assert outbound["is_from_me"] is True
    assert outbound["sender"] == "me"
    inbound = next(r for r in rows if r["text"] == "hey are we still on for saturday?")
    assert inbound["is_from_me"] is False
    assert inbound["sender"] == "+15551234567"


def test_list_chats_returns_participants_and_last_date(chat_db):
    chats = imessage_db.list_chats(limit=50)
    by_id = {c["chat_id"]: c for c in chats}
    assert by_id["chat-guid-100"]["participants"] == ["+15551234567"]
    group = by_id["chat-guid-102"]
    assert group["chat_name"] == "Weekend Trip"
    assert set(group["participants"]) == {"+15559990001", "+15559990002"}


def test_messages_with_identifiers_filters_to_contact(chat_db):
    rows = imessage_db.messages_with_identifiers(["+15551234567"], days=30, limit=50)
    texts = {r["text"] for r in rows}
    assert texts == {"hey are we still on for saturday?", "yep see you then", "unread ping"}


def test_messages_with_identifiers_keyword_filter(chat_db):
    rows = imessage_db.messages_with_identifiers(
        ["+15551234567"], days=30, limit=50, keyword="saturday"
    )
    assert len(rows) == 1
    assert "saturday" in rows[0]["text"]


def test_search_messages_matches_text(chat_db):
    rows = imessage_db.search_messages("logistics", days=30, limit=50)
    assert len(rows) == 1
    assert rows[0]["text"] == "group logistics for the trip"


def test_unread_messages(chat_db):
    rows = imessage_db.unread_messages()
    assert len(rows) == 1
    assert rows[0]["text"] == "unread ping"


def test_is_group_identifier(chat_db):
    assert imessage_db.is_group_identifier("+15559990001") is True
    assert imessage_db.is_group_identifier("+15551234567") is False
    assert imessage_db.is_group_identifier("+10000000000") is False


def test_find_recent_outbound_matches_text(chat_db):
    since = datetime.now(UTC) - timedelta(days=4)
    found = imessage_db.find_recent_outbound("+15551234567", "yep see you then", since=since)
    assert found is not None
    assert found["rowid"] == 2


def test_find_recent_outbound_no_match_returns_none(chat_db):
    since = datetime.now(UTC) - timedelta(days=4)
    found = imessage_db.find_recent_outbound("+15551234567", "not a real message", since=since)
    assert found is None


def test_decode_attributed_body_strips_whitespace_valued_length_byte():
    """The byte after the "+" marker is a length, not text -- including when
    its value happens to be whitespace.

    A 10-character message encodes its length as 0x0A. Stripping the marker
    with `^\\+[^\\s]` refused to match that, leaving "+\\n" glued to the front
    of every such message -- visible in read output, and enough to make
    `find_recent_outbound_in_chat` miss its own just-sent message and report
    a successful send as "unconfirmed". Bytes below are a real outbound row.
    """
    blob = bytes.fromhex(
        "040b73747265616d747970656481e803840140848484124e5341747472696275746564"
        "537472696e67008484084e534f626a656374008592848484084e53537472696e670194"
        "84012b0a5b424f545d2074657374"
    )
    assert decode_attributed_body(blob) == "[BOT] test"
