"""Append-only audit log JSONL (spec §9 — observability for safety events).

Default destination: `<target_project>/_bmad-output/runs/audit.events.jsonl`.

`record_audit` is sync (used inside PreToolUse hooks where async overhead is
undesirable) — it does a single `path.write_text` append via stdlib `open(..., 'a')`.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bmad_orchestrator.agent.tools._common import runs_dir


def audit_log_path() -> Path:
    """Resolve the audit log path.

    `BMAD_AUDIT_LOG` env var override → tests use it to isolate writes.
    """
    override = os.environ.get("BMAD_AUDIT_LOG")
    if override:
        return Path(override)
    return runs_dir() / "audit.events.jsonl"


def record_audit(event_type: str, **payload: Any) -> dict[str, Any]:
    """Append a single audit entry. Returns the entry as dict for callers/tests."""
    entry: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "event_type": event_type,
        **payload,
    }
    path = audit_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    return entry


__all__ = ["audit_log_path", "record_audit"]
