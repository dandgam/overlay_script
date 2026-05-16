"""Telegram-specific audit log writer (spec §15.5, §15.6).

Append-only JSONL для всех bot-interactions: incoming/outgoing/voice/callback.
Хранит ОБА варианта сообщения (original на диске + redacted что ушло) — per spec.

Default path: `<target_project>/_bmad-output/runs/telegram.jsonl`.
Override через `BMAD_TELEGRAM_AUDIT_LOG` для тестов.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bmad_orchestrator.agent.tools._common import runs_dir


def telegram_audit_path() -> Path:
    """Resolve telegram audit log path. `BMAD_TELEGRAM_AUDIT_LOG` env override."""
    override = os.environ.get("BMAD_TELEGRAM_AUDIT_LOG")
    if override:
        return Path(override)
    return runs_dir() / "telegram.jsonl"


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

    direction: "in" (user → bot) | "out" (bot → user)
    message_type: "text" | "voice" | "command" | "callback" | "denied"
    original / redacted: full text variants. Both stored — original локально (file
        not forwarded anywhere), redacted = что фактически ушло.
    pii_categories: список detected/redacted categories ([] = no PII).
    """
    entry: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "direction": direction,
        "chat_id": chat_id,
        "message_type": message_type,
        "pii": pii_categories or [],
    }
    if original is not None:
        entry["original"] = original
    if redacted is not None:
        entry["redacted"] = redacted
    if extra:
        entry["extra"] = extra

    path = telegram_audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    return entry


__all__ = ["record_telegram_event", "telegram_audit_path"]
