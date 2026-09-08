#!/usr/bin/env python3
"""
trash-reminder.py — trash pickup reminder for the roommate groupchat.

Runs on rtk via launchd on selected evenings. Decides from the date whether
tonight needs a provisional heads-up (2 nights before pickup) or the final
reminder (1 night before pickup), then sends one iMessage to the groupchat.

Normal week (Tue pickup): Sun 20:00 provisional, Mon 19:00 final.
Slip week  (Wed pickup after an observed holiday): Mon 20:00 provisional,
Tue 19:00 final. launchd fires Sun 20:00 + Mon 19:00/20:00 + Tue 19:00; the
script no-ops on nights that aren't due (pure date math).

Rotation: Me -> Niranjan -> Andrew -> Daniel (round-robin, advances only on
the final send). Between the two heads-ups the script scans the groupchat
for skip commands:
    "bot skip me"      — anyone may skip themselves
    "bot skip <name>"  — only Noah (is_from_me) may skip someone else
Skips apply to this week's resolution only and clear after the final send.

Pickup schedule: weekly Tuesday (WM Irvine, carts out by 6am), delayed one
day when the Tuesday falls on or follows an observed holiday (New Year's,
Memorial Day, Independence Day, Labor Day, Thanksgiving, Christmas).

Sending is via osascript addressed to the group *chat* (matched at send time
by participant phone numbers — never a hardcoded chat guid, so it survives
chat.db guid churn). Reading skips needs Full Disk Access to chat.db; the
first send/request to Messages.app pops an Automation TCC prompt — approve
both over Screen Sharing. SSH sessions have FDA implicitly, so "works over
ssh" proves nothing about launchd.

No PII in this repo: phone numbers live in ~/.config/trash-reminder/
config.json on rtk (chmod 600), never here.

Usage:
    trash-reminder.py [--dry-run] [--probe] [--config PATH] [--state PATH]
    --dry-run   decide + read skips, print what would send, send nothing
    --probe     read-only: resolve config, list the matched chat + recent
                senders (triggers the Automation prompt on first use —
                approve it now so Sunday's send is smooth)
"""

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
DB_PATH = Path.home() / "Library" / "Messages" / "chat.db"
APPLE_EPOCH_OFFSET = 978307200
DEFAULT_CONFIG = Path.home() / ".config" / "trash-reminder" / "config.json"
DEFAULT_STATE = Path.home() / ".local" / "state" / "trash-reminder" / "state.json"

# Tuesday (Python weekday(): Mon=0 .. Sun=6)
PICKUP_WEEKDAY = 1

# Rotation order (keys into config["people"]).
ORDER = ["noah", "niranjan", "andrew", "daniel"]

# First-name aliases accepted in "bot skip <name>".
ALIASES = {
    "noah": "noah",
    "niranjan": "niranjan",
    "navnir": "niranjan",
    "andrew": "andrew",
    "daniel": "daniel",
}

# Skip commands match at the START of a message (leading whitespace ok), so
# multiline texts like "bot skip me\nim gone for the week" work. Anything
# after the name is ignored.
SKIP_RE = re.compile(r"bot\s+skip\s+([a-z]+)", re.IGNORECASE)

# osascript finds the group chat by participant handles at send time and
# sends to it. Recipient/text travel as argv — never interpolated.
_SEND_SCRIPT = """
on run argv
    set theText to item 1 of argv
    set n1 to item 2 of argv
    set n2 to item 3 of argv
    set n3 to item 4 of argv
    tell application "Messages"
        repeat with c in every chat
            try
                set hs to handle of every participant of c
                if hs contains n1 and hs contains n2 and hs contains n3 then
                    send theText to c
                    return "sent:" & (id of c)
                end if
            end try
        end repeat
        error "trash-reminder: group chat not found"
    end tell
end run
"""

_PROBE_SCRIPT = """
on run argv
    set n1 to item 1 of argv
    set n2 to item 2 of argv
    set n3 to item 3 of argv
    tell application "Messages"
        repeat with c in every chat
            try
                set hs to handle of every participant of c
                if hs contains n1 and hs contains n2 and hs contains n3 then
                    return "match:" & (id of c) & " handles=" & (hs as string)
                end if
            end try
        end repeat
        error "trash-reminder: group chat not found"
    end tell
end run
"""


# ---- config / state ------------------------------------------------------

def load_config(path: Path) -> dict:
    """Config schema: {"numbers": {"niranjan": "+1…", "andrew": "+1…",
    "daniel": "+1…"}, "display": {"noah": "Noah", …}}."""
    if not path.exists():
        sys.exit(
            f"config not found at {path} — create it on rtk (chmod 600):\n"
            '{"numbers": {"niranjan": "+1…", "andrew": "+1…", "daniel": "+1…"}, '
            '"display": {"noah": "Noah", "niranjan": "Niranjan", '
            '"andrew": "Andrew", "daniel": "Daniel"}}'
        )
    cfg = json.loads(path.read_text())
    if set(ORDER) - {"noah"} - set(cfg.get("numbers", {})):
        sys.exit(f"config {path} missing numbers for rotation members")
    return cfg


def load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"index": 0}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


# ---- pickup schedule -----------------------------------------------------

def _nth_weekday(year: int, month: int, n: int, weekday: int) -> date:
    """nth `weekday` (Mon=0) of month; n=-1 means last."""
    if n > 0:
        d = date(year, month, 1)
        d += timedelta(days=(weekday - d.weekday()) % 7 + (n - 1) * 7)
        return d
    d = date(year, month + 1, 1) if month < 12 else date(year + 1, 1, 1)
    d -= timedelta(days=1)
    d -= timedelta(days=(d.weekday() - weekday) % 7)
    return d


def observed_holidays(year: int) -> set[date]:
    """WM-observed holidays (weekend shift: Sat->Fri, Sun->Mon)."""
    fixed = [date(year, 1, 1), date(year, 7, 4), date(year, 12, 25)]
    floating = [
        _nth_weekday(year, 5, -1, 0),   # Memorial Day: last Mon of May
        _nth_weekday(year, 9, 1, 0),    # Labor Day: 1st Mon of Sep
        _nth_weekday(year, 11, 4, 3),   # Thanksgiving: 4th Thu of Nov
    ]
    out = set()
    for h in fixed + floating:
        if h.weekday() == 5:
            out.add(h - timedelta(days=1))
        elif h.weekday() == 6:
            out.add(h + timedelta(days=1))
        else:
            out.add(h)
    return out


def pickup_for_week(tuesday: date) -> date:
    """Actual pickup for the week whose normal pickup is `tuesday` (Tue,
    or Wed when the Tue falls on or follows an observed holiday)."""
    window = {tuesday - timedelta(days=2), tuesday - timedelta(days=1), tuesday}
    holidays = observed_holidays(tuesday.year) | observed_holidays(tuesday.year + 1)
    if window & holidays:
        return tuesday + timedelta(days=1)
    return tuesday


def pickup_for_today(today: date) -> tuple[date, int]:
    """(actual pickup date, days until pickup) for the pickup this week."""
    tuesday = today + timedelta(days=(PICKUP_WEEKDAY - today.weekday()) % 7)
    pickup = pickup_for_week(tuesday)
    return pickup, (pickup - today).days


# ---- chat.db reads (read-only; needs FDA under launchd) ------------------

def _open():
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("SELECT 1 FROM message LIMIT 1")
        return conn
    except (sqlite3.OperationalError, sqlite3.DatabaseError, PermissionError) as exc:
        sys.exit(
            f"cannot read {DB_PATH} ({exc}). Grant Full Disk Access to the "
            "interpreter running this script, then retry."
        )


def apple_ts_to_dt(ns: int) -> datetime:
    return datetime.fromtimestamp(ns / 1e9 + APPLE_EPOCH_OFFSET, tz=ZoneInfo("UTC")).astimezone(PACIFIC)


def recent_chat_messages(numbers: list[str], since: datetime, limit: int = 200) -> list[dict]:
    """Inbound messages in the roommate groupchat since `since`.

    Matches the chat by participant handles (same rule as the send script),
    so guid churn can't desync reads from writes.
    """
    conn = _open()
    try:
        chats = conn.execute("""
            SELECT c.ROWID as rid, c.guid FROM chat c WHERE c.style = 43
        """).fetchall()
        chat_rid = None
        for ch in chats:
            parts = {
                r["id"]
                for r in conn.execute(
                    """SELECT h.id as id FROM chat_handle_join chj
                       JOIN handle h ON h.ROWID = chj.handle_id
                       WHERE chj.chat_id = :rid""",
                    {"rid": ch["rid"]},
                ).fetchall()
            }
            if all(any(n in (p or "") for p in parts) for n in numbers):
                chat_rid = ch["rid"]
                break
        if chat_rid is None:
            sys.exit("trash-reminder: group chat not found in chat.db")
        since_ns = int((since.timestamp() - APPLE_EPOCH_OFFSET) * 1_000_000_000)
        rows = conn.execute(
            """SELECT m.date as date, m.text as text, m.is_from_me as me,
                      h.id as sender
               FROM message m
               JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
               LEFT JOIN handle h ON h.ROWID = m.handle_id
               WHERE cmj.chat_id = :rid AND m.date >= :since
                 AND m.text IS NOT NULL
               ORDER BY m.date ASC LIMIT :limit""",
            {"rid": chat_rid, "since": since_ns, "limit": limit},
        ).fetchall()
        out = []
        for r in rows:
            sender = "me" if r["me"] else r["sender"]  # outbound == Noah
            out.append({"date": apple_ts_to_dt(r["date"]), "sender": sender,
                        "text": r["text"]})
        return out
    finally:
        conn.close()


def find_recent_outbound(numbers: list[str], text: str, since: datetime) -> bool:
    """Confirm our send landed (outbound row in the groupchat matching text).

    Outbound group sends often land with text=NULL and the body only in
    attributedBody, so match against both (same CAST trick as search).
    """
    import time

    # The blob holds raw UTF-8, so only a contiguous ASCII run from the text
    # can match it with LIKE (an emoji in the middle breaks contiguity).
    ascii_runs = re.findall(r"[\x20-\x7e]{10,}", text)
    snippet = max(ascii_runs, key=len).strip() if ascii_runs else text[:60]
    snippet = snippet.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    deadline = time.monotonic() + 5.0
    since_ns = int((since.timestamp() - APPLE_EPOCH_OFFSET) * 1_000_000_000)
    while time.monotonic() < deadline:
        conn = _open()
        try:
            row = conn.execute(
                """SELECT 1
                   FROM message m
                   JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
                   JOIN chat c ON c.ROWID = cmj.chat_id
                   JOIN chat_handle_join chj ON chj.chat_id = c.ROWID
                   JOIN handle h ON h.ROWID = chj.handle_id
                    WHERE h.id LIKE :n AND m.is_from_me = 1
                     AND m.date >= :since
                     AND (m.text LIKE :q ESCAPE '\\' OR CAST(m.attributedBody AS TEXT) LIKE :q ESCAPE '\\')
                    LIMIT 1""",
                {"n": f"%{numbers[0]}%", "since": since_ns,
                 "q": f"%{snippet}%", "escape": "\\"},
            ).fetchone()
            if row is not None:
                return True
        finally:
            conn.close()
        time.sleep(0.5)
    return False


# ---- skip parsing --------------------------------------------------------

def parse_skips(
    messages: list[dict], numbers: dict
) -> tuple[set[str], list[str]]:
    """Returns (skipped people, human-readable log lines).

    Commands match at the start of a message, so "bot skip me\\nim gone for
    the week" works; trailing text is ignored. Anyone may skip themselves
    ("bot skip me"); only Noah (outbound messages, is_from_me) may skip
    someone else ("bot skip <name>").
    """
    handle_to_person = {num: person for person, num in numbers.items()}
    skipped: set[str] = set()
    log: list[str] = []
    for m in messages:
        if m["sender"] == "me":
            sender = "noah"
        else:
            sender = next(
                (person for handle, person in handle_to_person.items()
                 if handle and handle in (m["sender"] or "")),
                None,
            )
        if sender is None:
            continue
        match = SKIP_RE.match((m["text"] or "").lstrip())
        if match:
            target = match.group(1).lower()
            if target == "me":
                skipped.add(sender)
                log.append(f"skip: {sender} skipped themselves")
            elif target in ALIASES:
                person = ALIASES[target]
                if person == sender:
                    skipped.add(person)
                    log.append(f"skip: {sender} skipped themselves (by name)")
                elif sender == "noah":
                    skipped.add(person)
                    log.append(f"skip: {sender} skipped {person}")
                else:
                    log.append(f"ignored: {sender} tried to skip {person} (Noah only)")
    return skipped, log


def resolve_final(index: int, skipped: set[str]) -> tuple[str, int]:
    """First non-skipped person from `index`; returns (person, position)."""
    for step in range(len(ORDER)):
        pos = (index + step) % len(ORDER)
        if ORDER[pos] not in skipped:
            return ORDER[pos], pos
    return ORDER[index], index  # everyone skipped: fall back to rotation


# ---- send ----------------------------------------------------------------

def send_text(text: str, numbers: list[str], dry_run: bool) -> str:
    if dry_run:
        return "dry-run (not sent)"
    result = subprocess.run(
        ["osascript", "-e", _SEND_SCRIPT, text, *numbers],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        sys.exit(f"osascript send failed: {result.stderr.strip()}")
    return result.stdout.strip()


# ---- main ----------------------------------------------------------------

PROVISIONAL_TPL = (
    "[BOT] 🗑️ Trash heads-up: {name} is up for {day} pickup. "
    "Reply 'bot skip me' to pass (Noah can 'bot skip <name>')."
)
FINAL_TPL = "[BOT] 🗑️ Trash reminder: {name}, you're up for {day} pickup!{skipped}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Roommate trash reminder")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--probe", action="store_true",
                        help="read-only: verify chat targeting, send nothing")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    args = parser.parse_args()

    cfg = load_config(args.config)
    numbers: dict = cfg["numbers"]
    display: dict = cfg.get("display", {p: p.title() for p in ORDER})
    ordered_numbers = [numbers[p] for p in ORDER if p != "noah"]

    if args.probe:
        result = subprocess.run(
            ["osascript", "-e", _PROBE_SCRIPT, *ordered_numbers],
            capture_output=True, text=True, timeout=30,
        )
        print((result.stdout or "").strip() or result.stderr.strip())
        sys.exit(0 if result.returncode == 0 else 1)

    today = datetime.now(PACIFIC).date()
    pickup, days_until = pickup_for_today(today)
    day_label = pickup.strftime("%A")
    state = load_state(args.state)
    pickup_key = pickup.isoformat()

    if days_until == 2:
        # Provisional heads-up. No rotation advance; just announce the peek
        # and record when the skip window opened.
        if state.get("provisional_for") == pickup_key:
            print(f"{today}: provisional already sent for {pickup_key}, no-op")
            return
        person = ORDER[state.get("index", 0) % len(ORDER)]
        text = PROVISIONAL_TPL.format(name=display[person], day=day_label)
        status = send_text(text, ordered_numbers, args.dry_run)
        print(f"{today}: provisional -> {person}: {text} [{status}]")
        if not args.dry_run:
            state["provisional_for"] = pickup_key
            state["provisional_at"] = datetime.now(PACIFIC).isoformat()
            save_state(args.state, state)
    elif days_until == 1:
        # Final reminder: collect skips since the provisional, resolve, send,
        # advance rotation past the final person, clear the window.
        if state.get("final_for") == pickup_key:
            print(f"{today}: final already sent for {pickup_key}, no-op")
            return
        if state.get("provisional_at"):
            since = datetime.fromisoformat(state["provisional_at"])
        else:
            since = datetime.now(PACIFIC) - timedelta(days=4)
        messages = [] if args.dry_run else recent_chat_messages(ordered_numbers, since)
        if args.dry_run:
            print(f"{today}: dry-run, skipping chat.db read (launchd needs FDA)")
            skipped, skiplog = set(), []
        else:
            skipped, skiplog = parse_skips(messages, numbers)
        for line in skiplog:
            print(f"{today}: {line}")
        person, pos = resolve_final(state.get("index", 0) % len(ORDER), skipped)
        skipped_note = (
            f" (skipped: {', '.join(sorted(skipped - {person}))})"
            if skipped - {person} else ""
        )
        text = FINAL_TPL.format(name=display[person], day=day_label,
                                skipped=skipped_note)
        sent_at = datetime.now(ZoneInfo("UTC"))  # before send: race-free floor
        status = send_text(text, ordered_numbers, args.dry_run)
        print(f"{today}: final -> {person}: {text} [{status}]")
        if not args.dry_run:
            confirmed = find_recent_outbound(ordered_numbers, text, sent_at)
            print(f"{today}: outbound {'confirmed' if confirmed else 'UNCONFIRMED'}")
            state["final_for"] = pickup_key
            state["index"] = (pos + 1) % len(ORDER)
            state.pop("provisional_for", None)
            state.pop("provisional_at", None)
            save_state(args.state, state)
    else:
        print(f"{today}: pickup {pickup} is {days_until}d away, no-op")


if __name__ == "__main__":
    main()
