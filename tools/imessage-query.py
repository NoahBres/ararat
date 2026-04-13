#!/usr/bin/env python3
"""
Query iMessages from ~/Library/Messages/chat.db.

Usage:
  imessage-query.py "kirill"              # all messages from/to Kirill
  imessage-query.py "kirill" "address"    # filter by keyword in message text
  imessage-query.py --days 30 "kirill"    # look back further (default: 90)
  imessage-query.py --limit 50 "kirill"   # more results (default: 20)

Requires Full Disk Access for Terminal in System Settings > Privacy & Security.
"""

import sqlite3
import sys
import argparse
import re
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path.home() / "Library/Messages/chat.db"
APPLE_EPOCH_OFFSET = 978307200  # seconds between 1970-01-01 and 2001-01-01

_NOISE = {
    "streamtyped", "NSAttributedString", "NSString", "NSMutableString",
    "NSColor", "NSFont", "NSObject", "NSMutableDictionary", "NSDictionary",
    "NSArray", "NSMutableArray", "NSParagraphStyle", "NSMutableParagraphStyle",
    "NSOriginalFont", "NSUnderline", "NSStrikethrough",
}
# Prefixes that signal binary/archive noise rather than message content
_NOISE_PREFIX = re.compile(r"^[+\-][^a-zA-Z0-9 \t\n]")
_BPLIST_TAIL  = re.compile(r"\s*\[[\da-f]+c\]bplist.*$", re.DOTALL)
_CLASS_ONLY   = re.compile(r"^[A-Z][A-Za-z0-9]+$")
_LEADING_REF  = re.compile(r"^\+[^\s]")  # NSArchiver object-ref prefix e.g. "+)" "+3"


def _clean(s: str) -> str:
    """Strip NSArchiver artifacts from a decoded string."""
    s = _BPLIST_TAIL.sub("", s)
    s = re.sub(r"\s*\|\s*$", "", s)
    s = re.sub(r"^\+[^\s]", "", s)       # leading NSArchiver object-ref marker
    return s.strip()


# Patterns for NSKeyedArchiver-encoded data detector objects and attachments.
_DATA_DETECTOR_EXTRACTORS = [
    # Phone number: T<digits>[PhoneNumber
    (re.compile(r"T([\d\+\-\(\) ]{3,})\[PhoneNumber", re.I), lambda m: f"[phone: {m.group(1).strip()}]"),
    # Date: U<label>TDate
    (re.compile(r"U([A-Za-z0-9\-]+)TDate"), lambda m: f"[date: {m.group(1)}]"),
    # URL
    (re.compile(r"(https?://[^\s\x00-\x1f]{8,})"), lambda m: m.group(1)),
    # Sticker attachment: filename contains "sticker"
    (re.compile(r"at_[0-9a-f_-]+sticker[^\s]*", re.I), lambda m: "[sticker]"),
    # Generic attachment by GUID pattern (at_0_UUID or at_GUID_UUID)
    (re.compile(r"[A-Za-z]?at_[0-9a-f_\-]{8,}"), lambda m: "[attachment]"),
    # Address: number + street keyword
    (re.compile(r"(\d+\s+\w[\w\s]{2,30}(?:street|st|ave|blvd|dr|rd|ln|way|ct|pl|cir)\b[^,\n]{0,40})", re.I),
     lambda m: m.group(1).strip()),
]

_BINARY_MARKERS = ("Z$class", "WNSValue", "XNSObject", "$null", "bplist",
                   "X$version", "X$archiver", "T$top")


def _try_decode_binary_fragment(s: str):
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

    chunks = re.findall(r"[ -~\u00a0-\uffff\n\r\t]{4,}", text)
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
        # Attachment/sticker GUIDs that pass the binary marker filter
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


def apple_ts_to_dt(ns: int) -> datetime:
    return datetime.fromtimestamp(ns / 1e9 + APPLE_EPOCH_OFFSET, tz=timezone.utc).astimezone()


def query(contact: str, keyword, days: int, limit: int):
    if not DB_PATH.exists():
        sys.exit(f"chat.db not found — grant Terminal Full Disk Access in System Settings.")

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT m.date, m.text, m.attributedBody, m.is_from_me, h.id AS handle_id
        FROM message m
        JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        JOIN chat c ON c.ROWID = cmj.chat_id
        LEFT JOIN handle h ON h.ROWID = m.handle_id
        WHERE
            m.date > (strftime('%s','now') - strftime('%s','2001-01-01') - :cutoff) * 1000000000
            AND (h.id LIKE :contact OR c.display_name LIKE :contact OR c.chat_identifier LIKE :contact)
        ORDER BY m.date DESC
        LIMIT :limit
    """, {
        "cutoff": days * 86400,
        "contact": f"%{contact}%",
        "limit": limit * 3,
    }).fetchall()
    conn.close()

    results = []
    for row in rows:
        text = (row["text"] or "").strip() or decode_attributed_body(row["attributedBody"] or b"")
        if not text:
            continue
        if keyword and keyword.lower() not in text.lower():
            continue
        results.append((
            apple_ts_to_dt(row["date"]),
            bool(row["is_from_me"]),
            text,
        ))
        if len(results) >= limit:
            break

    return results


def main():
    parser = argparse.ArgumentParser(description="Query iMessages")
    parser.add_argument("contact", help="Phone number or email (partial match)")
    parser.add_argument("keyword", nargs="?", help="Optional keyword filter")
    parser.add_argument("--days",  type=int, default=90, help="Look back N days (default: 90)")
    parser.add_argument("--limit", type=int, default=20, help="Max results (default: 20)")
    args = parser.parse_args()

    results = query(args.contact, args.keyword, args.days, args.limit)

    if not results:
        print(f"No messages from '{args.contact}'" +
              (f" matching '{args.keyword}'" if args.keyword else "") +
              f" in the last {args.days}d.")
        return

    current_date = None
    for ts, from_me, text in reversed(results):
        day = ts.strftime("%m-%d")
        if day != current_date:
            print(f"[{day}]")
            current_date = day
        who = "me" if from_me else "them"
        print(f"  {ts.strftime('%H:%M')} {who}: {text}")


if __name__ == "__main__":
    main()
