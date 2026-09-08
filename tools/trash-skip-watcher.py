#!/usr/bin/env python3
"""
trash-skip-watcher.py — instant acks for "bot skip" commands.

Companion to tools/trash-reminder.py. Runs every 5 minutes via launchd
(StartInterval 300) and scans the roommate groupchat for new messages since
the last run. Each new "bot skip …" command gets an immediate [BOT] reply;
the weekly reminder still owns rotation and the final resolution.

Only watches during the active window (1-2 nights before pickup, same math
as the reminder). Outside the window it advances its cursor silently — skips
only count once the week's window opens on Sunday.

Same deal as the reminder: chat.db reads need Full Disk Access under
launchd, and sends go through the same participant-matched osascript. No
PII here — numbers live in ~/.config/trash-reminder/config.json on rtk.

State: ~/.local/state/trash-reminder/watch.json
    {"last_seen_ns": <apple ns>, "acked": [<message rowids>]}
First run initializes the cursor to now so history is never replayed.

Usage:
    trash-skip-watcher.py [--dry-run] [--config PATH] [--state PATH]
"""

import argparse
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
HERE = Path(__file__).parent
DEFAULT_CONFIG = Path.home() / ".config" / "trash-reminder" / "config.json"
DEFAULT_STATE = Path.home() / ".local" / "state" / "trash-reminder" / "watch.json"


def load_shared():
    """Import trash-reminder.py by path (hyphenated name isn't importable)."""
    path = HERE / "trash-reminder.py"
    if not path.exists():
        sys.exit(f"shared module not found: {path}")
    spec = importlib.util.spec_from_file_location("trash_reminder", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tr = load_shared()

ACK_OK = "[BOT] Acknowledged. {name} is skipped for {day} pickup."
ACK_DENIED = "[BOT] Only Noah can skip someone else."


def resolve_sender(sender, numbers):
    if sender == "me":
        return "noah"
    return next(
        (person for handle, person in
         ({num: p for p, num in numbers.items()}).items()
         if handle and handle in (sender or "")),
        None,
    )


def decide_acks(messages, numbers, display, day_label, already_acked):
    """Pure decision step (unit-testable): [(rowid, ack_text)] for new
    skip commands. Same validity rules as the reminder's parse_skips."""
    out = []
    seen = set()
    for m in messages:
        if m["rowid"] in already_acked or m["rowid"] in seen:
            continue
        seen.add(m["rowid"])
        match = tr.SKIP_RE.match((m["text"] or "").lstrip())
        if not match:
            continue
        sender = resolve_sender(m["sender"], numbers)
        if sender is None:
            continue
        target = match.group(1).lower()
        person = sender if target == "me" else tr.ALIASES.get(target)
        if person is None:
            continue  # "bot skip <unknown>" — ignore silently
        if person == sender or sender == "noah":
            out.append((m["rowid"], ACK_OK.format(name=display[person],
                                                  day=day_label)))
        else:
            out.append((m["rowid"], ACK_DENIED))
    return out


def load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"last_seen_ns": None, "acked": []}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Ack bot-skip commands")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    args = parser.parse_args()

    cfg = tr.load_config(args.config)
    numbers: dict = cfg["numbers"]
    display: dict = cfg.get("display", {p: p.title() for p in tr.ORDER})
    ordered_numbers = [numbers[p] for p in tr.ORDER if p != "noah"]
    today = datetime.now(PACIFIC).date()
    state = load_state(args.state)

    if state.get("last_seen_ns") is None:
        state["last_seen_ns"] = tr.apple_ns_now()
        if not args.dry_run:
            save_state(args.state, state)
        print(f"{today}: cursor initialized, no replay")
        return

    pickup, days_until = tr.pickup_for_today(today)
    messages = tr.new_messages_with_ids(ordered_numbers, state["last_seen_ns"])
    if messages:
        latest = max(m["date"] for m in messages)
        state["last_seen_ns"] = int(
            (latest.timestamp() - tr.APPLE_EPOCH_OFFSET) * 1_000_000_000)
    acked = set(state.get("acked", []))

    if days_until not in (1, 2):
        print(f"{today}: outside window (pickup {pickup} in {days_until}d), "
              f"scanned {len(messages)}, no acks")
        if not args.dry_run:
            save_state(args.state, state)
        return

    day_label = pickup.strftime("%A")
    acks = decide_acks(messages, numbers, display, day_label, acked)
    for rowid, text in acks:
        status = tr.send_text(text, ordered_numbers, args.dry_run)
        print(f"{today}: ack rowid={rowid}: {text} [{status}]")
        acked.add(rowid)
    if not acks:
        print(f"{today}: scanned {len(messages)}, nothing to ack")
    if not args.dry_run:
        state["acked"] = sorted(acked)[-200:]
        save_state(args.state, state)


if __name__ == "__main__":
    main()
