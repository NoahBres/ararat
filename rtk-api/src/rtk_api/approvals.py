"""The approval gate for tools a client lists under `require_approval`.

The human-in-the-loop queue this was always meant to be -- tool returns 202
with an approval_id, a Telegram bot DMs Allow/Deny, caller polls for the
decision -- is designed in `notes/NOTES.md` and not built. Until it is,
`request_approval` **grants** and records.

That is a deliberate change of failure direction. Refusing outright made
`require_approval` an unusable deny list: there was no way to get anything
approved, so listing a tool there meant "never", and the 403 it produced was
indistinguishable from a scoping mistake. Granting-and-auditing keeps the
list meaningful -- these are the calls worth being able to review after the
fact -- and leaves the seam where the real queue slots in: make this function
consult it and return its decision, and every caller keeps working.

Every gated call lands in the audit log as `approval.auto_granted` *and* as a
warning on the server log, so `require_approval` is now the answer to "which
calls do I want a record of", not "which calls are blocked".
"""

from __future__ import annotations

import logging
from typing import Any

from rtk_api.audit import audit_log

logger = logging.getLogger("rtk_api.approvals")

#: Audit-log `event` for a call auto-granted by this stub. Distinct from the
#: plain "write" event so the two can be told apart when the real queue lands
#: and starts emitting decisions of its own.
AUTO_GRANTED_EVENT = "approval.auto_granted"


def request_approval(tool: str, principal: str | None, args: dict[str, Any]) -> bool:
    """Decide whether an approval-gated call may proceed. Always True for now.

    Audits before returning, so the record exists even if the call itself
    then fails -- the useful question after the fact is "what was attempted",
    not just "what succeeded".
    """
    audit_log(tool, principal, args, event=AUTO_GRANTED_EVENT)
    logger.warning(
        "approval auto-granted (no approval queue implemented)",
        extra={"tool": tool, "principal": principal},
    )
    return True
