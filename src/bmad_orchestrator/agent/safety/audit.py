"""Append-only audit log JSONL (spec §9 — observability for safety events).

Default destination: `<target_project>/_bmad-output/runs/audit.events.jsonl`.

`record_audit` is sync (used inside PreToolUse hooks where async overhead is
undesirable). All entries are written via `os.open(..., O_CREAT|O_APPEND, 0o600)`
to keep tokens / paths / arguments off other-user-readable disk (FS1 B6).

Every string value in the payload is scrubbed of known secret patterns before
write — defense-in-depth in case a hook forgets to redact (FS1 B6).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bmad_orchestrator.agent.safety.secret_patterns import scrub_secrets_generic


def _scrub_secrets(text: str) -> str:
    """Audit-log scrubber — single ``[REDACTED:SECRET]`` placeholder preserved
    for FS1 contract compatibility."""
    return scrub_secrets_generic(text)


def _scrub_value(value: Any) -> Any:
    if isinstance(value, str):
        return _scrub_secrets(value)
    if isinstance(value, dict):
        return {k: _scrub_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_scrub_value(v) for v in value)
    return value


def audit_log_path() -> Path:
    """Resolve the audit log path. `BMAD_AUDIT_LOG` env override → tests use it."""
    override = os.environ.get("BMAD_AUDIT_LOG")
    if override:
        return Path(override)
    # Lazy import — `_common` lives under agent.tools whose __init__ imports
    # back into safety, so a top-level import here would create a cycle.
    from bmad_orchestrator.agent.tools._common import runs_dir
    return runs_dir() / "audit.events.jsonl"


def _open_secure_append(path: Path) -> int:
    """Open `path` for append, mode 0o600. Tightens existing files via fchmod."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    fd = os.open(str(path), flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
    except (OSError, AttributeError):
        pass
    return fd


def record_audit(event_type: str, **payload: Any) -> dict[str, Any]:
    """Append a single audit entry. Returns the entry dict for callers/tests."""
    scrubbed = {k: _scrub_value(v) for k, v in payload.items()}
    entry: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "event_type": event_type,
        **scrubbed,
    }
    path = audit_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = _open_secure_append(path)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    return entry


__all__ = ["audit_log_path", "record_audit"]
