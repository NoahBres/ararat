"""Shared fixture: a minimal sqlite db mimicking the parts of chat.db our
queries touch (message, handle, chat, chat_message_join, chat_handle_join,
attachment, message_attachment_join). Kept in its own module (not conftest.py)
so it's opt-in via explicit import, matching the pattern of the existing
Things tests which monkeypatch instead of using shared fixtures.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

APPLE_EPOCH_OFFSET = 978307200


def dt_to_apple_ns(dt: datetime) -> int:
    return int((dt.timestamp() - APPLE_EPOCH_OFFSET) * 1_000_000_000)


SCHEMA = """
CREATE TABLE message (
    ROWID INTEGER PRIMARY KEY,
    guid TEXT,
    text TEXT,
    attributedBody BLOB,
    handle_id INTEGER,
    date INTEGER,
    is_from_me INTEGER,
    is_read INTEGER
);
CREATE TABLE handle (
    ROWID INTEGER PRIMARY KEY,
    id TEXT
);
CREATE TABLE chat (
    ROWID INTEGER PRIMARY KEY,
    guid TEXT,
    display_name TEXT,
    chat_identifier TEXT,
    style INTEGER
);
CREATE TABLE chat_message_join (
    chat_id INTEGER,
    message_id INTEGER
);
CREATE TABLE chat_handle_join (
    chat_id INTEGER,
    handle_id INTEGER
);
CREATE TABLE attachment (
    ROWID INTEGER PRIMARY KEY,
    mime_type TEXT
);
CREATE TABLE message_attachment_join (
    message_id INTEGER,
    attachment_id INTEGER
);
"""


def build_chat_db(db_path: Path) -> None:
    """Create a small realistic fixture:
    - handle 1: +15551234567 ("Kirill"-ish contact, 1:1 chat 100)
    - handle 2: friend@example.com (1:1 chat 101)
    - handle 3 + 4: group chat 102 (style=43)
    - a handful of messages, one with an attachment, one unread, one
      with attributedBody-only text, all recent (within a few days).
    """
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    now = datetime.now(UTC)

    def ns(days_ago: float) -> int:
        return dt_to_apple_ns(now - timedelta(days=days_ago))

    conn.executemany(
        "INSERT INTO handle (ROWID, id) VALUES (?, ?)",
        [
            (1, "+15551234567"),
            (2, "friend@example.com"),
            (3, "+15559990001"),
            (4, "+15559990002"),
        ],
    )

    conn.executemany(
        "INSERT INTO chat (ROWID, guid, display_name, chat_identifier, style) VALUES (?, ?, ?, ?, ?)",
        [
            (100, "chat-guid-100", None, "+15551234567", 45),
            (101, "chat-guid-101", None, "friend@example.com", 45),
            (102, "chat-guid-102", "Weekend Trip", "chat123456789", 43),
        ],
    )
    conn.executemany(
        "INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (?, ?)",
        [(100, 1), (101, 2), (102, 3), (102, 4)],
    )

    messages = [
        # rowid, guid, text, attributedBody, handle_id, days_ago, is_from_me, is_read
        (1, "m1", "hey are we still on for saturday?", None, 1, 3, 0, 1),
        (2, "m2", "yep see you then", None, None, 3, 1, 1),
        (3, "m3", "unread ping", None, 1, 0.1, 0, 0),
        (4, "m4", None, b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01+here is the address 123 Main St", 2, 2, 0, 1),
        (5, "m5", "photo incoming", None, 2, 1, 0, 1),
        (6, "m6", "group logistics for the trip", None, 3, 5, 0, 1),
        (7, "m7", "sounds good", None, None, 5, 1, 1),
    ]
    conn.executemany(
        "INSERT INTO message (ROWID, guid, text, attributedBody, handle_id, date, is_from_me, is_read) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(rowid, guid, text, blob, handle, ns(days), from_me, read) for rowid, guid, text, blob, handle, days, from_me, read in messages],
    )

    conn.executemany(
        "INSERT INTO chat_message_join (chat_id, message_id) VALUES (?, ?)",
        [(100, 1), (100, 2), (100, 3), (101, 4), (101, 5), (102, 6), (102, 7)],
    )

    conn.execute("INSERT INTO attachment (ROWID, mime_type) VALUES (1, 'image/jpeg')")
    conn.execute("INSERT INTO message_attachment_join (message_id, attachment_id) VALUES (5, 1)")

    conn.commit()
    conn.close()
