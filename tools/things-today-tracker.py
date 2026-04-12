#!/usr/bin/env python3
"""
things-today-tracker.py

Tracks how long each task has been sitting in Things 3 "Today".
Flags tasks that have been there for FLAG_THRESHOLD_DAYS or more,
then sends a Telegram message if any are found.

Usage:
    python3 tools/things-today-tracker.py [--threshold DAYS] [--dry-run]
"""

import json
import os
import subprocess
import sys
import argparse
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DATA_FILE = REPO_ROOT / "private-data" / "things-today-tracker.json"
TELEGRAM_ENV_FILE = Path.home() / ".claude" / "channels" / "telegram" / ".env"
CHAT_ID = "227506906"
DEFAULT_THRESHOLD = 10


def get_bot_token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if token:
        return token
    if TELEGRAM_ENV_FILE.exists():
        for line in TELEGRAM_ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                return line.split("=", 1)[1]
    raise RuntimeError("TELEGRAM_BOT_TOKEN not found")


def send_telegram(text: str):
    token = get_bot_token()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text}).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def get_today_tasks() -> list[dict]:
    result = subprocess.run(
        ["uvx", "things-cli", "--json", "today"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def load_tracker() -> dict:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    return {}


def save_tracker(tracker: dict):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(tracker, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                        help="Days before a task is flagged (default: 10)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print output without sending Telegram message")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    tasks = get_today_tasks()
    current_uuids = {t["uuid"] for t in tasks}
    task_by_uuid = {t["uuid"]: t for t in tasks}

    tracker = load_tracker()

    # Add new tasks, preserve existing first_seen timestamps
    updated_tracker = {}
    for uuid in current_uuids:
        if uuid in tracker:
            updated_tracker[uuid] = tracker[uuid]
        else:
            updated_tracker[uuid] = {
                "title": task_by_uuid[uuid]["title"],
                "first_seen": now_iso,
            }
        # Always keep title fresh
        updated_tracker[uuid]["title"] = task_by_uuid[uuid]["title"]

    save_tracker(updated_tracker)

    # Find flagged tasks
    flagged = []
    for uuid, info in updated_tracker.items():
        first_seen = datetime.fromisoformat(info["first_seen"])
        days = (now - first_seen).days
        if days >= args.threshold:
            flagged.append({
                "uuid": uuid,
                "title": info["title"],
                "days_in_today": days,
                "first_seen": info["first_seen"],
            })

    flagged.sort(key=lambda x: x["days_in_today"], reverse=True)

    if not flagged:
        print("No flagged tasks.")
        return

    lines = [f"Heads up — you've got {len(flagged)} task(s) that have been sitting in Today for {args.threshold}+ days:\n"]
    for t in flagged:
        d = t["days_in_today"]
        lines.append(f"• {t['title']} ({d} day{'s' if d != 1 else ''})")
    message = "\n".join(lines)

    print(message)

    if not args.dry_run:
        send_telegram(message)
        print("\nTelegram message sent.")


if __name__ == "__main__":
    main()
