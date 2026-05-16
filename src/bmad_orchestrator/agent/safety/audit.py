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
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_-]{40,}"),
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{36}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{82}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"Bearer\s+[A-Za-z0-9_.\-+/=]{20,}", re.IGNORECASE),
    re.compile(r"https?://[^\s/:@]+:[^\s/@]+@[^\s/]+"),
)
_REDACTION = "[REDACTED:SECRET]"


def _scrub_secrets(text: str) -> str:
    for pat in _SECRET_PATTERNS:
        text = pat.sub(_REDACTION, text)
    return text


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
