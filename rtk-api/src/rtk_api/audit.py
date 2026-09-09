"""Append-only audit log for write tools.

Writes one JSON line per call to ~/.local/state/rtk-api/audit.log:
{"ts": "...", "event": "write", "tool": "things.add", "principal": "bearer", "args": {...}}

`event` distinguishes what got logged: "write" for a write tool being called,
"approval.auto_granted" for a call that matched a client's `require_approval`
and was let through by the stub in `approvals.py`. A single call can produce
both lines.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

AUDIT_LOG_PATH = Path.home() / ".local" / "state" / "rtk-api" / "audit.log"


def audit_log(tool: str, principal: str | None, args: dict[str, Any], event: str = "write") -> None:
    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(UTC).isoformat(),
        "event": event,
        "tool": tool,
        "principal": principal,
        "args": args,
    }
    with AUDIT_LOG_PATH.open("a") as f:
        f.write(json.dumps(entry, default=str) + "\n")
