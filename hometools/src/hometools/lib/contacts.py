"""Contact name resolution, ported from ``tools/contacts-search.py`` (fuzzy
AddressBook search with a Contacts.app/osascript fallback for iCloud-only
contacts). That script's docstring now points here -- don't fork-and-diverge;
fix bugs in this module.

Used by `hometools.tools.imessage` to turn a fuzzy contact name into
phone/email identifiers (for reads and, gated, for `imessage.send`), and to
do the reverse lookup (identifier -> display name) when annotating message
senders.
"""

from __future__ import annotations

import glob
import re
import sqlite3
import subprocess
from difflib import SequenceMatcher
from pathlib import Path

AB_GLOB = str(Path.home() / "Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb")
AB_MAIN = str(Path.home() / "Library/Application Support/AddressBook/AddressBook-v22.abcddb")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_NON_PHONE_CHARS_RE = re.compile(r"[^\d+]")


def normalize_phone(raw: str) -> str:
    """Strip everything but digits and a leading '+' from a phone number."""
    return _NON_PHONE_CHARS_RE.sub("", raw)


def looks_like_identifier(value: str) -> bool:
    """True if `value` already looks like an email or phone number, so no
    fuzzy contact-name lookup is needed."""
    value = value.strip()
    if _EMAIL_RE.match(value):
        return True
    digits = normalize_phone(value).lstrip("+")
    return len(digits) >= 7 and digits.isdigit()


def normalize_identifier(value: str) -> str:
    """Normalize an already-identifier-shaped value: lowercase emails,
    digits-only (plus leading +) for phone numbers."""
    value = value.strip()
    if _EMAIL_RE.match(value):
        return value.lower()
    return normalize_phone(value)


def _load_all_contacts() -> list[dict]:
    """Load {"name", "phone", "email"} rows from every local AddressBook
    sqlite database, falling back to Contacts.app via osascript for
    iCloud-only contacts when the local databases yield nothing."""
    dbs = glob.glob(AB_GLOB) + [AB_MAIN]
    contacts: list[dict] = []
    seen: set[tuple] = set()

    for db_path in dbs:
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            rows = conn.execute(
                """
                SELECT
                    r.ZFIRSTNAME, r.ZLASTNAME, r.ZORGANIZATION,
                    p.ZFULLNUMBER,
                    e.ZADDRESS
                FROM ZABCDRECORD r
                LEFT JOIN ZABCDPHONENUMBER p ON p.ZOWNER = r.Z_PK
                LEFT JOIN ZABCDEMAILADDRESS e ON e.ZOWNER = r.Z_PK
                WHERE r.ZFIRSTNAME IS NOT NULL OR r.ZLASTNAME IS NOT NULL OR r.ZORGANIZATION IS NOT NULL
                """
            ).fetchall()
            conn.close()
            for fname, lname, org, phone, email in rows:
                full_name = " ".join(filter(None, [fname, lname])) or org or ""
                if not full_name:
                    continue
                key = (full_name.lower(), phone, email)
                if key in seen:
                    continue
                seen.add(key)
                contacts.append({"name": full_name, "phone": phone, "email": email})
        except Exception:
            pass

    if not contacts:
        try:
            script = """
tell application "Contacts"
    set out to ""
    repeat with p in every person
        set fn to (first name of p) & " " & (last name of p)
        set phones to phones of p
        set emails to emails of p
        repeat with ph in phones
            set out to out & fn & "|phone|" & (value of ph) & "\\n"
        end repeat
        repeat with em in emails
            set out to out & fn & "|email|" & (value of em) & "\\n"
        end repeat
    end repeat
    return out
end tell"""
            result = subprocess.run(
                ["osascript", "-e", script], capture_output=True, text=True, timeout=15
            )
            for line in result.stdout.splitlines():
                parts = line.strip().split("|")
                if len(parts) != 3:
                    continue
                name, kind, value = parts
                name = name.strip()
                if not name or name == " ":
                    continue
                key = (name.lower(), value if kind == "phone" else None, value if kind == "email" else None)
                if key not in seen:
                    seen.add(key)
                    contacts.append(
                        {
                            "name": name,
                            "phone": value if kind == "phone" else None,
                            "email": value if kind == "email" else None,
                        }
                    )
        except Exception:
            pass

    return contacts


def fuzzy_score(query: str, name: str) -> float:
    q = query.lower()
    n = name.lower()
    if q in n:
        return 1.0
    for word in n.split():
        if word.startswith(q):
            return 0.95
    return SequenceMatcher(None, q, n).ratio()


def resolve(name_or_identifier: str) -> list[dict]:
    """Resolve a name or identifier to contact matches.

    If `name_or_identifier` already looks like a phone number or email, it's
    returned normalized without doing a contacts lookup:
        [{"name": None, "identifiers": [normalized]}]

    Otherwise performs the fuzzy AddressBook/Contacts.app search from
    `tools/contacts-search.py` and groups matches by contact name, best
    score first:
        [{"name": "Kirill Something", "identifiers": ["+1555...", "k@x.com"]}, ...]
    """
    value = name_or_identifier.strip()
    if not value:
        return []
    if looks_like_identifier(value):
        return [{"name": None, "identifiers": [normalize_identifier(value)]}]

    contacts = _load_all_contacts()
    scored: list[tuple[float, dict]] = []
    for c in contacts:
        score = fuzzy_score(value, c["name"])
        if score >= 0.5:
            scored.append((score, c))
    scored.sort(key=lambda x: x[0], reverse=True)

    order: list[str] = []
    grouped: dict[str, set[str]] = {}
    for _, c in scored:
        if c["name"] not in grouped:
            grouped[c["name"]] = set()
            order.append(c["name"])
        if c["phone"]:
            grouped[c["name"]].add(normalize_phone(c["phone"]))
        if c["email"]:
            grouped[c["name"]].add(c["email"].lower())

    return [{"name": name, "identifiers": sorted(grouped[name])} for name in order if grouped[name]]


def lookup_name(identifier: str) -> str | None:
    """Reverse lookup: phone/email identifier -> best-guess contact name, or
    None if not found. Used to annotate iMessage senders with a name."""
    norm = normalize_identifier(identifier)
    for c in _load_all_contacts():
        if c["phone"] and normalize_phone(c["phone"]) == norm:
            return c["name"]
        if c["email"] and c["email"].lower() == norm:
            return c["name"]
    return None
