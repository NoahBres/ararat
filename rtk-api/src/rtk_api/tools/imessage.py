"""iMessage read tools, plus a gated `imessage.send` write tool.

Backed by `rtk_api.lib.imessage_db` (chat.db reading) and
`rtk_api.lib.contacts` (fuzzy name -> phone/email resolution, and the
reverse for annotating senders). See notes/plans/rtk-api.md section 6
for the Full Disk Access requirement (reads) and the write kill switch /
allowlist / Automation TCC prompt (send).
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

_SEND_SCRIPT = """
on run argv
    set theRecipient to item 1 of argv
    set theText to item 2 of argv
    tell application "Messages"
        set targetService to 1st account whose service type = iMessage
        set targetBuddy to participant theRecipient of targetService
        send theText to targetBuddy
    end tell
end run
"""


@tool("imessage.chats")
def chats(limit: int = 50) -> list[dict]:
    """List recent iMessage/SMS chats: chat id (guid), display name or
    participants, and last message date."""
    return imessage_db.list_chats(limit=limit)


@tool("imessage.recent")
def recent(limit: int = 50, days: int = 7) -> list[dict]:
    """Newest messages across all chats in the last `days` days."""
    return contacts.annotate_senders(imessage_db.recent_messages(limit=limit, days=days))


@tool("imessage.with_contact")
def with_contact(
    contact: str, days: int = 90, limit: int = 50, keyword: str | None = None
) -> list[dict]:
    """Messages to/from a contact. `contact` may be a fuzzy name (resolved
    via the same AddressBook/Contacts.app search as tools/contacts-search.py)
    or a phone number/email, optionally filtered by a text `keyword`."""
    matches = contacts.resolve(contact)
    identifiers: list[str] = []
    for m in matches:
        identifiers.extend(m["identifiers"])
    if not identifiers:
        identifiers = [contact]
    rows = imessage_db.messages_with_identifiers(
        identifiers, days=days, limit=limit, keyword=keyword
    )
    return contacts.annotate_senders(rows)


@tool("imessage.search")
def search(query: str, days: int = 365, limit: int = 50) -> list[dict]:
    """Search message bodies for `query` over the last `days` days."""
    return contacts.annotate_senders(imessage_db.search_messages(query, days=days, limit=limit))


@tool("imessage.unread")
def unread(limit: int = 50) -> list[dict]:
    """Unread inbound messages (is_read=0, is_from_me=0)."""
    return contacts.annotate_senders(imessage_db.unread_messages(limit=limit))


#: macOS AppleScript error for "Not authorized to send Apple events".
_TCC_DENIED_MARKERS = ("-1743", "Not authorized to send Apple events")

_AUTOMATION_HELP = (
    "Messages.app refused Apple events from rtk-api. macOS needs Automation "
    "permission for the rtk-api process to control Messages: on rtk, System "
    "Settings > Privacy & Security > Automation > rtk-api > enable Messages. "
    "If rtk-api isn't listed, or was previously denied, reset the prompt with "
    "`tccutil reset AppleEvents com.noahbres.rtk-api`, then trigger a send "
    "while watching rtk's screen (Screen Sharing) and click Allow -- the "
    "prompt is modal on that machine and times out into a denial if nobody "
    "answers. Granting this to an SSH session is a *different* TCC entry "
    "(sshd-keygen-wrapper) and does not cover the service."
)


def _resolve_send_target(to: str) -> str:
    """`to` must already be a phone number or email. Deliberately *no* fuzzy
    contact matching here: a name that fuzzy-matches the wrong person would
    send a message to them, and the allowlist check below would be against
    whoever the matcher happened to rank first. Resolve names with a read
    tool (imessage.with_contact / contacts) and pass the identifier back.
    """
    if not contacts.looks_like_identifier(to):
        raise ValueError(
            f"imessage.send requires `to` to be a phone number or email, got {to!r}; "
            "resolve the contact name first (e.g. via imessage.with_contact) and "
            "pass the exact identifier"
        )
    return contacts.normalize_identifier(to)


@tool("imessage.send", write=True)
def send(to: str, text: str) -> dict:
    """Send an iMessage. `to` must be an exact phone number (e.g.
    "+15551234567") or email -- contact names are rejected; resolve them
    first with imessage.with_contact. Refuses unless IMESSAGE_WRITE_ENABLED
    is set and the normalised recipient identifier is in
    IMESSAGE_WRITE_ALLOWLIST. Refuses group chats. Caps `text` at 2000
    characters. Sends via osascript (recipient/text passed as argv, never
    interpolated into the script), then polls chat.db for ~3s to confirm the
    outbound message landed."""
    settings = get_settings()

    if not settings.imessage_write_enabled:
        raise PermissionError(
            "iMessage sending is disabled (set IMESSAGE_WRITE_ENABLED=true to enable)"
        )

    if len(text) > MAX_SEND_TEXT_LEN:
        raise ValueError(f"text exceeds max length of {MAX_SEND_TEXT_LEN} characters")

    identifier = _resolve_send_target(to)

    allowlist = set(settings.imessage_write_allowlist)
    if identifier not in allowlist:
        raise PermissionError(f"{identifier!r} is not in IMESSAGE_WRITE_ALLOWLIST")

    if imessage_db.is_group_identifier(identifier):
        raise ValueError("refusing to send to a group chat")

    # Captured *before* the send, not after: Messages usually writes the
    # chat.db row during the osascript call, so a timestamp taken afterwards
    # sits past the row's own date and the `m.date >= since` filter excludes
    # the very message we're trying to confirm.
    sent_at = datetime.now(UTC)
    try:
        result = subprocess.run(
            ["osascript", "-e", _SEND_SCRIPT, identifier, text],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired as exc:
        # osascript blocking this long means Messages never answered --
        # almost always the modal Automation prompt sitting unanswered on
        # rtk's screen, which macOS then records as a denial.
        raise PermissionError(_AUTOMATION_HELP) from exc

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if any(marker in stderr for marker in _TCC_DENIED_MARKERS):
            raise PermissionError(_AUTOMATION_HELP)
        raise RuntimeError(f"osascript send failed: {stderr}")

    confirmed_rowid = None
    deadline = time.monotonic() + _SEND_POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        found = imessage_db.find_recent_outbound(identifier, text, since=sent_at)
        if found is not None:
            confirmed_rowid = found["rowid"]
            break
        time.sleep(_SEND_POLL_INTERVAL_S)

    return {
        "sent": "confirmed" if confirmed_rowid is not None else "unconfirmed",
        "to": identifier,
        "rowid": confirmed_rowid,
    }
