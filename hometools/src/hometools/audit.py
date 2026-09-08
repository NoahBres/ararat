"""Append-only audit log for write tools.

Writes one JSON line per call to ~/.local/state/hometools/audit.log:
{"ts": "...", "tool": "things.add", "principal": "bearer", "args": {...}}
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

AUDIT_LOG_PATH = Path.home() / ".local" / "state" / "hometools" / "audit.log"


def audit_log(tool: str, principal: str | None, args: dict[str, Any]) -> None:
    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(UTC).isoformat(),
        "tool": tool,
        "principal": principal,
        "args": args,
    }
    with AUDIT_LOG_PATH.open("a") as f:
        f.write(json.dumps(entry, default=str) + "\n")
