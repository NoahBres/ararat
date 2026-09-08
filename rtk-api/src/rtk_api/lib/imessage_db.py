"""iMessage chat.db reading + attributedBody decoding.

This is the canonical copy of the logic originally written as a standalone
script in ``tools/imessage-query.py``. That script's docstring now points
here -- don't fork-and-diverge; fix bugs in this module.

Opens ``~/Library/Messages/chat.db`` read-only (``?mode=ro``, ``uri=True``)
so it can never corrupt Messages.app's database. Reading this file requires
Full Disk Access for whatever Python interpreter is running this process --
see ``notes/plans/rtk-api.md`` section 6.1. When FDA is missing, every
public function here raises `ImessageAccessError` with a message describing
exactly what to grant.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

DB_PATH = Path.home() / "Library" / "Messages" / "chat.db"
APPLE_EPOCH_OFFSET = 978307200  # seconds between 1970-01-01 and 2001-01-01
PACIFIC = ZoneInfo("America/Los_Angeles")

_FDA_HELP = (
    "Could not open/read ~/Library/Messages/chat.db. This almost always means "
    "Full Disk Access hasn't been granted to rtk-api. Grant it on rtk in "
    "System Settings > Privacy & Security > Full Disk Access -- add "
    "~/Applications/rtk-api.app (the launcher the launchd agent runs through), "
    "then restart-rtk-api. See notes/plans/rtk-api.md section 6.1."
)


class ImessageAccessError(RuntimeError):
    """Raised when chat.db can't be opened or queried -- see `_FDA_HELP`."""


# ---- attributedBody decoding (ported from tools/imessage-query.py) --------

_NOISE = {
    "streamtyped",
    "NSAttributedString",
    "NSString",
    "NSMutableString",
    "NSColor",
    "NSFont",
    "NSObject",
    "NSMutableDictionary",
    "NSDictionary",
    "NSArray",
    "NSMutableArray",
    "NSParagraphStyle",
    "NSMutableParagraphStyle",
    "NSOriginalFont",
    "NSUnderline",
    "NSStrikethrough",
}
_BPLIST_TAIL = re.compile(r"\s*\[[\da-f]+c\]bplist.*$", re.DOTALL)
_CLASS_ONLY = re.compile(r"^[A-Z][A-Za-z0-9]+$")

_DATA_DETECTOR_EXTRACTORS = [
    (
        re.compile(r"T([\d\+\-\(\) ]{3,})\[PhoneNumber", re.I),
        lambda m: f"[phone: {m.group(1).strip()}]",
    ),
    (re.compile(r"U([A-Za-z0-9\-]+)TDate"), lambda m: f"[date: {m.group(1)}]"),
    (re.compile(r"(https?://[^\s\x00-\x1f]{8,})"), lambda m: m.group(1)),
    (re.compile(r"at_[0-9a-f_-]+sticker[^\s]*", re.I), lambda m: "[sticker]"),
    (re.compile(r"[A-Za-z]?at_[0-9a-f_\-]{8,}"), lambda m: "[attachment]"),
    (
        re.compile(
            r"(\d+\s+\w[\w\s]{2,30}(?:street|st|ave|blvd|dr|rd|ln|way|ct|pl|cir)\b[^,\n]{0,40})",
            re.I,
        ),
        lambda m: m.group(1).strip(),
    ),
]

_BINARY_MARKERS = (
    "Z$class",
    "WNSValue",
    "XNSObject",
    "$null",
    "bplist",
    "X$version",
    "X$archiver",
    "T$top",
)


def _clean(s: str) -> str:
    """Strip NSArchiver artifacts from a decoded string."""
    s = _BPLIST_TAIL.sub("", s)
    s = re.sub(r"\s*\|\s*$", "", s)
    s = re.sub(r"^\+[^\s]", "", s)  # leading NSArchiver object-ref marker
    return s.strip()


def _try_decode_binary_fragment(s: str) -> str | None:
    """Try to extract a human-readable value from an NSKeyedArchiver fragment."""
    for pattern, extractor in _DATA_DETECTOR_EXTRACTORS:
        m = pattern.search(s)
        if m:
            return extractor(m)
    return None


def decode_attributed_body(blob: bytes) -> str:
    """Extract plain text from NSArchiver-encoded attributedBody."""
    if not blob:
        return ""
    try:
        text = blob.decode("utf-8", errors="ignore")
    except Exception:
        text = blob.decode("latin-1", errors="ignore")

    chunks = re.findall(r"[ -~ -￿\n\r\t]{4,}", text)
    candidates = []
    for c in chunks:
        s = c.strip()
        if len(s) <= 3 or s in _NOISE:
            continue
        if "__kIM" in s or s.startswith("__k"):
            continue
        if _CLASS_ONLY.match(s):
            continue
        if any(x in s for x in _BINARY_MARKERS):
            decoded = _try_decode_binary_fragment(s)
            if decoded:
                candidates.append(decoded)
            continue
        if re.search(r"at_[0-9a-f]{8,}", s, re.I):
            decoded = _try_decode_binary_fragment(s)
            candidates.append(decoded or "[attachment]")
            continue
        cleaned = _clean(s)
        if cleaned and len(cleaned) > 3:
            candidates.append(cleaned)

    if not candidates:
        return ""
    return max(candidates, key=len)


# ---- timestamps -------------------------------------------------------


def apple_ts_to_utc(ns: int) -> datetime:
    return datetime.fromtimestamp(ns / 1e9 + APPLE_EPOCH_OFFSET, tz=UTC)


def _dt_to_apple_ns(dt: datetime) -> int:
    return int((dt.timestamp() - APPLE_EPOCH_OFFSET) * 1_000_000_000)


def _days_cutoff_ns(days: int) -> int:
    cutoff_dt = datetime.now(UTC) - timedelta(days=days)
    return _dt_to_apple_ns(cutoff_dt)


# ---- connection ---------------------------------------------------------


def _open(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path if db_path is not None else DB_PATH
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        # Cheap probe that forces sqlite to actually touch the file --
        # `connect()` alone doesn't open it, so a TCC/FDA denial or a
        # missing file only surfaces here.
        conn.execute("SELECT 1 FROM message LIMIT 1")
        return conn
    except (sqlite3.OperationalError, sqlite3.DatabaseError, PermissionError) as exc:
        raise ImessageAccessError(_FDA_HELP) from exc


# ---- message row shaping --------------------------------------------------

_MESSAGE_SELECT = """
    SELECT m.ROWID as msg_id, m.date, m.text, m.attributedBody, m.is_from_me,
           h.id as handle_id, c.ROWID as chat_rowid, c.guid as chat_guid,
           c.display_name as chat_display_name, c.chat_identifier as chat_identifier
    FROM message m
    JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
    JOIN chat c ON c.ROWID = cmj.chat_id
    LEFT JOIN handle h ON h.ROWID = m.handle_id
"""


def _attachments_for(conn: sqlite3.Connection, msg_ids: list[int]) -> dict[int, list[str]]:
    if not msg_ids:
        return {}
    placeholders = ",".join("?" for _ in msg_ids)
    rows = conn.execute(
        f"""
        SELECT maj.message_id as message_id, a.mime_type as mime_type
        FROM message_attachment_join maj
        JOIN attachment a ON a.ROWID = maj.attachment_id
        WHERE maj.message_id IN ({placeholders})
        """,
        msg_ids,
    ).fetchall()
    out: dict[int, list[str]] = {}
    for r in rows:
        out.setdefault(r["message_id"], []).append(r["mime_type"] or "unknown")
    return out


def _row_to_message(row: sqlite3.Row, attachments_by_msgid: dict[int, list[str]]) -> dict:
    text = (row["text"] or "").strip() or decode_attributed_body(row["attributedBody"] or b"")
    dt_utc = apple_ts_to_utc(row["date"])
    is_from_me = bool(row["is_from_me"])
    sender = row["handle_id"] or ("me" if is_from_me else None)
    return {
        "date_utc": dt_utc.isoformat(),
        "date_pacific": dt_utc.astimezone(PACIFIC).isoformat(),
        "sender": sender,
        "is_from_me": is_from_me,
        "text": text,
        "chat_id": row["chat_guid"] or str(row["chat_rowid"]),
        "chat_name": row["chat_display_name"] or row["chat_identifier"] or row["chat_guid"],
        "attachments": attachments_by_msgid.get(row["msg_id"], []),
    }


def _run_message_query(
    conn: sqlite3.Connection, where_sql: str, params: dict, limit: int
) -> list[dict]:
    rows = conn.execute(
        f"{_MESSAGE_SELECT} WHERE {where_sql} ORDER BY m.date DESC LIMIT :limit",
        {**params, "limit": limit},
    ).fetchall()
    msg_ids = [r["msg_id"] for r in rows]
    attachments = _attachments_for(conn, msg_ids)
    out = []
    seen: set[int] = set()
    for r in rows:
        if r["msg_id"] in seen:
            continue
        seen.add(r["msg_id"])
        out.append(_row_to_message(r, attachments))
    return out


# ---- public read API ------------------------------------------------------


def list_chats(limit: int = 50) -> list[dict]:
    """Recent chats: guid, display name/participants, last message date."""
    conn = _open()
    try:
        rows = conn.execute(
            """
            SELECT c.ROWID as chat_rowid, c.guid as chat_guid, c.display_name as chat_display_name,
                   c.chat_identifier as chat_identifier, MAX(m.date) as last_date
            FROM chat c
            JOIN chat_message_join cmj ON cmj.chat_id = c.ROWID
            JOIN message m ON m.ROWID = cmj.message_id
            GROUP BY c.ROWID
            ORDER BY last_date DESC
            LIMIT :limit
            """,
            {"limit": limit},
        ).fetchall()

        result = []
        for r in rows:
            participants = conn.execute(
                """
                SELECT h.id as id FROM chat_handle_join chj
                JOIN handle h ON h.ROWID = chj.handle_id
                WHERE chj.chat_id = :chat_id
                """,
                {"chat_id": r["chat_rowid"]},
            ).fetchall()
            last_dt = apple_ts_to_utc(r["last_date"]) if r["last_date"] is not None else None
            result.append(
                {
                    "chat_id": r["chat_guid"] or str(r["chat_rowid"]),
                    "chat_name": r["chat_display_name"] or r["chat_identifier"] or r["chat_guid"],
                    "participants": [p["id"] for p in participants],
                    "last_message_date_utc": last_dt.isoformat() if last_dt else None,
                    "last_message_date_pacific": last_dt.astimezone(PACIFIC).isoformat()
                    if last_dt
                    else None,
                }
            )
        return result
    finally:
        conn.close()


def recent_messages(limit: int = 50, days: int = 7) -> list[dict]:
    """Newest messages across all chats in the last `days` days."""
    conn = _open()
    try:
        cutoff = _days_cutoff_ns(days)
        return _run_message_query(conn, "m.date > :cutoff", {"cutoff": cutoff}, limit)
    finally:
        conn.close()


def messages_with_identifiers(
    identifiers: list[str], days: int = 90, limit: int = 50, keyword: str | None = None
) -> list[dict]:
    """Messages to/from any of `identifiers` (phone/email substrings)."""
    conn = _open()
    try:
        cutoff = _days_cutoff_ns(days)
        params: dict = {"cutoff": cutoff}
        clauses = []
        for i, ident in enumerate(identifiers):
            key = f"id{i}"
            params[key] = f"%{ident}%"
            clauses.append(f"(h.id LIKE :{key} OR c.chat_identifier LIKE :{key})")
        if not clauses:
            return []
        where_sql = f"m.date > :cutoff AND ({' OR '.join(clauses)})"
        fetch_limit = limit * 3 if keyword else limit
        rows = _run_message_query(conn, where_sql, params, fetch_limit)
        if keyword:
            kw = keyword.lower()
            rows = [r for r in rows if kw in (r["text"] or "").lower()]
        return rows[:limit]
    finally:
        conn.close()


def search_messages(query: str, days: int = 365, limit: int = 50) -> list[dict]:
    """Search message bodies (text and decoded attributedBody) for `query`."""
    conn = _open()
    try:
        cutoff = _days_cutoff_ns(days)
        where_sql = (
            "m.date > :cutoff AND (m.text LIKE :q OR CAST(m.attributedBody AS TEXT) LIKE :q)"
        )
        return _run_message_query(conn, where_sql, {"cutoff": cutoff, "q": f"%{query}%"}, limit)
    finally:
        conn.close()


def unread_messages(limit: int = 50) -> list[dict]:
    """Unread inbound messages (is_read=0, is_from_me=0)."""
    conn = _open()
    try:
        return _run_message_query(conn, "m.is_read = 0 AND m.is_from_me = 0", {}, limit)
    finally:
        conn.close()


# ---- write-path helpers ----------------------------------------------------


def is_group_identifier(identifier: str) -> bool:
    """True if `identifier` is a participant in a group chat (chat.style
    43 = group, 45 = single). Used to refuse `imessage.send` to groups."""
    conn = _open()
    try:
        row = conn.execute(
            """
            SELECT 1
            FROM chat c
            JOIN chat_handle_join chj ON chj.chat_id = c.ROWID
            JOIN handle h ON h.ROWID = chj.handle_id
            WHERE h.id = :id AND c.style = 43
            LIMIT 1
            """,
            {"id": identifier},
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def find_recent_outbound(identifier: str, text: str, since: datetime) -> dict | None:
    """Look for an outbound message to `identifier` matching `text` sent at
    or after `since`. Used to confirm `imessage.send` actually landed."""
    conn = _open()
    try:
        since_ns = _dt_to_apple_ns(since)
        # Outbound messages typically have a NULL handle_id (there's no "from"
        # handle when it's you), so we can't join on m.handle_id here -- find
        # outbound messages in any chat this identifier participates in.
        rows = conn.execute(
            """
            SELECT DISTINCT m.ROWID as rowid, m.text as text, m.attributedBody as attributedBody, m.date as date
            FROM message m
            JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
            JOIN chat_handle_join chj ON chj.chat_id = cmj.chat_id
            JOIN handle h ON h.ROWID = chj.handle_id
            WHERE h.id = :id AND m.is_from_me = 1 AND m.date >= :since_ns
            ORDER BY m.date DESC
            LIMIT 20
            """,
            {"id": identifier, "since_ns": since_ns},
        ).fetchall()
        for r in rows:
            decoded = (r["text"] or "").strip() or decode_attributed_body(
                r["attributedBody"] or b""
            )
            if decoded.strip() == text.strip():
                return {"rowid": r["rowid"]}
        return None
    finally:
        conn.close()
