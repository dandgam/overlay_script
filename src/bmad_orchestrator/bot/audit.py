"""Telegram-specific audit log writer (spec §15.5, §15.6).

Append-only JSONL (perms 0o600). По умолчанию записывает только `redacted` —
оригинал НЕ хранится (FS1 B7 fix). Для forensics-debug включить env
`BMAD_AUDIT_KEEP_ORIGINAL=1` + установить `BMAD_AUDIT_HMAC_KEY` (hex/utf8) —
тогда оригиналы пишутся в отдельный файл `telegram-original.jsonl` (0o600) с
HMAC-SHA256 поверх (redacted ‖ "\\0" ‖ original) для tamper-detection.

Default path: `<target_project>/_bmad-output/runs/telegram.jsonl`.
Override через `BMAD_TELEGRAM_AUDIT_LOG` для тестов.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def telegram_audit_path() -> Path:
    """Resolve telegram audit log path. `BMAD_TELEGRAM_AUDIT_LOG` env override."""
    override = os.environ.get("BMAD_TELEGRAM_AUDIT_LOG")
    if override:
        return Path(override)
    # Lazy import — `_common` lives under agent.tools whose __init__ imports
    # back into safety, so a top-level import would create a cycle when this
    # module is loaded before agent.safety has fully initialized.
    from bmad_orchestrator.agent.tools._common import runs_dir
    return runs_dir() / "telegram.jsonl"


def telegram_original_path() -> Path:
    """Sibling path used only when KEEP_ORIGINAL=1 + HMAC_KEY (forensics-only)."""
    primary = telegram_audit_path()
    return primary.with_name("telegram-original.jsonl")


def _open_secure_append(path: Path) -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    fd = os.open(str(path), flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
    except (OSError, AttributeError):
        pass
    return fd


def _keep_original_enabled() -> bool:
    return os.environ.get("BMAD_AUDIT_KEEP_ORIGINAL") == "1"


def _hmac_key() -> bytes | None:
    raw = os.environ.get("BMAD_AUDIT_HMAC_KEY")
    if not raw:
        return None
    try:
        return bytes.fromhex(raw)
    except ValueError:
        return raw.encode("utf-8")


def _hmac_digest(redacted: str | None, original: str | None) -> str | None:
    key = _hmac_key()
    if key is None:
        return None
    msg = (redacted or "").encode("utf-8") + b"\x00" + (original or "").encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def record_telegram_event(
    *,
    direction: str,
    chat_id: int | None,
    message_type: str,
    original: str | None = None,
    redacted: str | None = None,
    pii_categories: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one telegram audit entry.

    Default: `original` is dropped before write (B7). `redacted` is always
    written. Set `BMAD_AUDIT_KEEP_ORIGINAL=1` AND `BMAD_AUDIT_HMAC_KEY` to also
    write `original` to a separate `telegram-original.jsonl` with HMAC-SHA256.
    """
    ts = datetime.now(UTC).isoformat(timespec="seconds")
    entry: dict[str, Any] = {
        "ts": ts,
        "direction": direction,
        "chat_id": chat_id,
        "message_type": message_type,
        "pii": pii_categories or [],
    }
    if redacted is not None:
        entry["redacted"] = redacted
    if extra:
        entry["extra"] = extra

    path = telegram_audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = _open_secure_append(path)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    if original is not None and _keep_original_enabled():
        digest = _hmac_digest(redacted, original)
        if digest is None:
            warnings.warn(
                "BMAD_AUDIT_KEEP_ORIGINAL=1 but BMAD_AUDIT_HMAC_KEY not set — "
                "original NOT written (set HMAC key to enable forensics log).",
                stacklevel=2,
            )
        else:
            orig_path = telegram_original_path()
            orig_entry: dict[str, Any] = {
                "ts": ts,
                "direction": direction,
                "chat_id": chat_id,
                "message_type": message_type,
                "redacted": redacted,
                "original": original,
                "hmac_sha256": digest,
            }
            fd2 = _open_secure_append(orig_path)
            with os.fdopen(fd2, "a", encoding="utf-8") as f:
                f.write(json.dumps(orig_entry, ensure_ascii=False, default=str) + "\n")

    return entry


__all__ = [
    "record_telegram_event",
    "telegram_audit_path",
    "telegram_original_path",
]
