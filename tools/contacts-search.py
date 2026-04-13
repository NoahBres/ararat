#!/usr/bin/env python3
"""
Fuzzy search contacts across all AddressBook databases.

Usage:
  contacts-search.py "kirill"         # fuzzy name match
  contacts-search.py "kirill" --exact # exact substring match only
  contacts-search.py "kirill" --top 5 # show top N matches (default: 5)
"""

import sqlite3
import glob
import sys
import argparse
import re
from pathlib import Path
from difflib import SequenceMatcher

AB_GLOB = str(Path.home() / "Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb")
AB_MAIN = str(Path.home() / "Library/Application Support/AddressBook/AddressBook-v22.abcddb")


def load_all_contacts() -> list[dict]:
    dbs = glob.glob(AB_GLOB) + [AB_MAIN]
    contacts = []
    seen = set()

    for db_path in dbs:
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            rows = conn.execute("""
                SELECT
                    r.ZFIRSTNAME, r.ZLASTNAME, r.ZORGANIZATION,
                    p.ZFULLNUMBER,
                    e.ZADDRESS
                FROM ZABCDRECORD r
                LEFT JOIN ZABCDPHONENUMBER p ON p.ZOWNER = r.Z_PK
                LEFT JOIN ZABCDEMAILADDRESS e ON e.ZOWNER = r.Z_PK
                WHERE r.ZFIRSTNAME IS NOT NULL OR r.ZLASTNAME IS NOT NULL OR r.ZORGANIZATION IS NOT NULL
            """).fetchall()
            conn.close()
            for fname, lname, org, phone, email in rows:
                full_name = " ".join(filter(None, [fname, lname])) or org or ""
                if not full_name:
                    continue
                key = (full_name.lower(), phone, email)
                if key in seen:
                    continue
                seen.add(key)
                contacts.append({
                    "name": full_name,
                    "phone": phone,
                    "email": email,
                })
        except Exception:
            pass

    # Fallback: query Contacts.app via osascript for iCloud contacts not in local SQLite
    if not contacts:
        try:
            import subprocess
            script = '''
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
end tell'''
            result = subprocess.run(["osascript", "-e", script],
                                    capture_output=True, text=True, timeout=15)
            for line in result.stdout.splitlines():
                parts = line.strip().split("|")
                if len(parts) != 3:
                    continue
                name, kind, value = parts
                name = name.strip()
                if not name or name == " ":
                    continue
                key = (name.lower(), value if kind == "phone" else None,
                       value if kind == "email" else None)
                if key not in seen:
                    seen.add(key)
                    contacts.append({
                        "name": name,
                        "phone": value if kind == "phone" else None,
                        "email": value if kind == "email" else None,
                    })
        except Exception:
            pass

    return contacts


def fuzzy_score(query: str, name: str) -> float:
    q = query.lower()
    n = name.lower()
    # Exact substring match gets full score
    if q in n:
        return 1.0
    # Word-level prefix match (e.g. "kir" matches "Kirill")
    for word in n.split():
        if word.startswith(q):
            return 0.95
    # Sequence similarity
    return SequenceMatcher(None, q, n).ratio()


def search(query: str, top: int, exact: bool) -> list[tuple[float, dict]]:
    contacts = load_all_contacts()
    scored = []
    for c in contacts:
        score = fuzzy_score(query, c["name"])
        if exact and score < 1.0:
            continue
        scored.append((score, c))

    scored.sort(key=lambda x: x[0], reverse=True)
    # Filter out very low scores unless exact
    threshold = 0.5 if not exact else 0.0
    return [(s, c) for s, c in scored if s >= threshold][:top]


def main():
    parser = argparse.ArgumentParser(description="Fuzzy contact search")
    parser.add_argument("query", help="Name to search for")
    parser.add_argument("--exact", action="store_true", help="Substring match only, no fuzzy")
    parser.add_argument("--top", type=int, default=5, help="Max results (default: 5)")
    args = parser.parse_args()

    results = search(args.query, args.top, args.exact)

    if not results:
        print(f"No contacts found matching '{args.query}'.")
        return

    for score, c in results:
        parts = [c["name"]]
        if c["phone"]:
            parts.append(c["phone"])
        if c["email"]:
            parts.append(c["email"])
        print(f"  [{score:.2f}] {' | '.join(parts)}")


if __name__ == "__main__":
    main()
