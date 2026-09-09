"""Fairbridge: read and write *one* iMessage group chat, and nothing else.

The scoping is the point. `imessage.send` can reach any allowlisted handle
and is gated behind the (unbuilt) approval queue; these tools can only ever
touch the single group chat whose members are exactly
`FAIRBRIDGE_PARTICIPANTS`, so `fairbridge.send` takes a `text` and no
recipient at all. There is no parameter an agent could pass to redirect a
message somewhere else -- the destination lives in rtk's env file, not in
the request.

Three things follow from that and are worth knowing before editing:

1. **The chat is identified by participants, never by guid.** Modern
   iMessage group-chat guids are device-local: this same chat has one guid
   on Noah's laptop and a different one on rtk.
   `imessage_db.find_chat_by_participants` resolves the guid fresh from the
   local chat.db on every call, requiring an exact participant-set match --
   chat.db contains near-miss groups with the same people plus one more.

2. **`FAIRBRIDGE_PARTICIPANTS` is not defaulted in code.** It's three real
   people's phone numbers and this repo is public; it lives only in
   ~/.config/rtk-api/env on rtk. Unset means the tools refuse.

3. **Sending uses `chat id`, not `participant`.** Messages' AppleScript
   dictionary makes `text chat id "..."` parse as `text of (chat id "...")`
   and fail with -1728; the bare `chat id "..."` form is what works, with
   the guid passed through argv rather than interpolated into the script.
"""

from __future__ import annotations

import subprocess
import time
from datetime import UTC, datetime

from rtk_api.config import get_settings
from rtk_api.lib import contacts, imessage_db
from rtk_api.registry import tool

MAX_SEND_TEXT_LEN = 2000
_SEND_POLL_TIMEOUT_S = 3.0
_SEND_POLL_INTERVAL_S = 0.3

#: `chat id theChatId` rather than `text chat id ...` -- see module docstring.
_SEND_SCRIPT = """
on run argv
    set theChatId to item 1 of argv
    set theText to item 2 of argv
    tell application "Messages"
        send theText to chat id theChatId
    end tell
end run
"""


def _resolve_chat() -> dict:
    """The Fairbridge chat as it exists in *this* machine's chat.db.

    Raises rather than returning None: every caller needs the chat, and the
    two failure modes (unconfigured vs. not found) want different fixes, so
    they get different messages.
    """
    settings = get_settings()
    participants = settings.fairbridge_participants
    if not participants:
        raise PermissionError(
            "FAIRBRIDGE_PARTICIPANTS is not set, so there is no Fairbridge chat to "
            "address; set it (comma-separated phone numbers/emails) in "
            "~/.config/rtk-api/env on rtk and restart the service"
        )

    normalized = [contacts.normalize_identifier(p) for p in participants]
    chat = imessage_db.find_chat_by_participants(normalized)
    if chat is None:
        raise ValueError(
            "no group chat found whose participants are exactly the configured "
            f"{len(normalized)} FAIRBRIDGE_PARTICIPANTS; the chat may not exist on "
            "this machine, or a member's number may be stored in a different form"
        )
    return chat


@tool("fairbridge.info")
def info() -> dict:
    """Which chat the fairbridge tools are pointed at right now: its
    participants, message count, and last activity. Read-only, and the way to
    confirm targeting before sending -- fairbridge.send takes no recipient,
    so this is the only place to check where it would go."""
    return _resolve_chat()


@tool("fairbridge.read")
def read(limit: int = 30, days: int = 30) -> list[dict]:
    """Recent messages in the Fairbridge group chat, newest first, with
    sender names resolved from Contacts. Scoped to that one chat only --
    there is no parameter for reading any other conversation."""
    chat = _resolve_chat()
    rows = imessage_db.messages_in_chat(chat["chat_guid"], days=days, limit=limit)
    return contacts.annotate_senders(rows)


@tool("fairbridge.send", write=True)
def send(text: str) -> dict:
    """Send a message to the Fairbridge group chat. Takes no recipient: the
    destination is fixed to the chat whose members are exactly
    FAIRBRIDGE_PARTICIPANTS. Caps `text` at 2000 characters. Sends via osascript (chat id and text
    passed as argv, never interpolated into the script), then polls chat.db
    for ~3s to confirm the message actually landed in that chat."""
    if not text.strip():
        raise ValueError("text is empty")

    if len(text) > MAX_SEND_TEXT_LEN:
        raise ValueError(f"text exceeds max length of {MAX_SEND_TEXT_LEN} characters")

    chat = _resolve_chat()

    # Captured *before* the send, not after: Messages usually writes the
    # chat.db row during the osascript call, so a timestamp taken afterwards
    # sits past the row's own date and the `m.date >= since` filter excludes
    # the very message we're trying to confirm.
    sent_at = datetime.now(UTC)
    result = subprocess.run(
        ["osascript", "-e", _SEND_SCRIPT, chat["chat_guid"], text],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(f"osascript send failed: {result.stderr.strip()}")

    confirmed_rowid = None
    deadline = time.monotonic() + _SEND_POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        found = imessage_db.find_recent_outbound_in_chat(chat["chat_guid"], text, since=sent_at)
        if found is not None:
            confirmed_rowid = found["rowid"]
            break
        time.sleep(_SEND_POLL_INTERVAL_S)

    return {
        "sent": "confirmed" if confirmed_rowid is not None else "unconfirmed",
        "chat_name": chat["chat_name"],
        "participants": chat["participants"],
        "rowid": confirmed_rowid,
    }
