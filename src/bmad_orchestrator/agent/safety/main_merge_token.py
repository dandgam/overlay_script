"""One-shot signed token for autonomous main-branch merge (spec §M3).

Replaces the simple `BMAD_ALLOW_MAIN_MERGE=1` env flag with a token that:
- is generated explicitly (file write at `.claude/main-merge-token.json`)
- carries a short TTL (default 300s) — expires automatically
- is single-use — `consume_token` flips `used=true`; subsequent reads refuse

Hook PreToolUse uses `validate_token` (read-only) so multi-step `git checkout main
&& git merge ...` sequences all share the same token. The `git_merge` tool calls
`consume_token` at the end of the operation to burn it.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

from bmad_orchestrator.config import load_settings

DEFAULT_TTL_SECONDS = 300


def token_path() -> Path:
    """Resolve the token file path. `BMAD_MAIN_MERGE_TOKEN_PATH` overrides (tests)."""
    override = os.environ.get("BMAD_MAIN_MERGE_TOKEN_PATH")
    if override:
        return Path(override)
    return load_settings().orchestrator_home / ".claude" / "main-merge-token.json"


def _read_state() -> dict[str, Any] | None:
    path = token_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_state(payload: dict[str, Any]) -> None:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(str(path), flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
    except (OSError, AttributeError):
        pass
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def generate_token(ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """Write a fresh single-use main-merge token. Returns the raw token string.

    Overwrites any prior token at the same path. Caller is expected to be the
    human or a trusted orchestrator path (CLI command, audited tool).
    """
    token = secrets.token_urlsafe(32)
    state = {
        "token": token,
        "expires_at": int(time.time()) + max(1, int(ttl_seconds)),
        "used": False,
        "created_at": int(time.time()),
    }
    _write_state(state)
    return token


def validate_token(presented: str | None) -> tuple[bool, str]:
    """Read-only check. (allowed, reason).

    - file missing → (False, "no_token")
    - token mismatch → (False, "token_mismatch")
    - expired → (False, "token_expired")
    - used → (False, "token_used")
    - all green → (True, "ok")
    """
    if not presented:
        return False, "no_token"
    state = _read_state()
    if state is None:
        return False, "no_token"
    if not secrets.compare_digest(str(state.get("token", "")), presented):
        return False, "token_mismatch"
    expires_at = int(state.get("expires_at", 0))
    if time.time() > expires_at:
        return False, "token_expired"
    if state.get("used"):
        return False, "token_used"
    return True, "ok"


def consume_token(presented: str | None) -> tuple[bool, str]:
    """Validate + flip `used=true` atomically. (allowed, reason).

    On success the token file is rewritten with `used=true`; subsequent
    `validate_token`/`consume_token` calls return (False, "token_used").
    """
    ok, reason = validate_token(presented)
    if not ok:
        return False, reason
    state = _read_state() or {}
    state["used"] = True
    state["used_at"] = int(time.time())
    _write_state(state)
    return True, "ok"


def has_active_token() -> tuple[bool, str]:
    """File-presence check used by the bash hook (no presented token to match).

    Hook can't ask the agent for the raw token — it only sees the bash command
    text. So the hook permits main-touching bash commands iff some valid
    unused unexpired token file is present. Token still gets consumed by
    `git_merge` tool at the end.
    """
    state = _read_state()
    if state is None:
        return False, "no_token"
    expires_at = int(state.get("expires_at", 0))
    if time.time() > expires_at:
        return False, "token_expired"
    if state.get("used"):
        return False, "token_used"
    return True, "ok"


def revoke_token() -> bool:
    """Delete the token file. Returns True if a file was deleted."""
    path = token_path()
    if path.exists():
        path.unlink()
        return True
    return False


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "consume_token",
    "generate_token",
    "has_active_token",
    "revoke_token",
    "token_path",
    "validate_token",
]
