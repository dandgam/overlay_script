"""Heartbeat / liveness primitives (spec §9.2 «liveness-before-kill»).

Используется PreToolUse hook'ом (S4) для:
- Запрета `kill -9` на dead PID (бессмысленно, может быть recycled).
- Запрета `kill` на orchestrator self PID.

Дополнительно — heartbeat-watcher для worker-процессов: считает worker «висящим»
если последний JSONL event старше `stall_seconds_threshold` (default 600s).

Зависимости: psutil опционален. Без него используется `os.kill(pid, 0)` fallback.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bmad_orchestrator.agent.tools._common import jsonl_tail

DEFAULT_STALL_THRESHOLD_SECONDS = 600


def is_alive(pid: int) -> bool:
    """True если процесс существует (signal 0 probe).

    PID <= 0 — всегда False (sentinel для mock workers).
    """
    if pid <= 0:
        return False
    try:
        import psutil
    except ImportError:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    return bool(psutil.pid_exists(pid))


def safe_to_kill(pid: int, self_pid: int | None = None) -> tuple[bool, str]:
    """`(allowed, reason)`. Если allowed=False — caller должен отказаться от kill.

    Запрещаем: dead PID (recycled risk), self PID (suicide), pid<=0 (sentinel).
    """
    self_pid = self_pid if self_pid is not None else os.getpid()
    if pid <= 0:
        return False, "pid_sentinel"
    if pid == self_pid:
        return False, "self_pid"
    if not is_alive(pid):
        return False, "not_alive"
    return True, "ok"


def last_event_age_seconds(jsonl_path: Path) -> float | None:
    """Возраст последнего event'а в секундах или None если файла/event'ов нет."""
    events = jsonl_tail(jsonl_path, 1)
    if not events:
        return None
    ts = events[-1].get("ts")
    if not isinstance(ts, str):
        return None
    try:
        emitted = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if emitted.tzinfo is None:
        emitted = emitted.replace(tzinfo=UTC)
    return (datetime.now(UTC) - emitted).total_seconds()


def is_stalled(
    jsonl_path: Path, threshold_seconds: int = DEFAULT_STALL_THRESHOLD_SECONDS
) -> bool:
    """True если последний event старше threshold (или вообще нет event'ов >0s)."""
    age = last_event_age_seconds(jsonl_path)
    if age is None:
        return False
    return age >= float(threshold_seconds)


def heartbeat_summary(
    pid: int, jsonl_path: Path, threshold_seconds: int = DEFAULT_STALL_THRESHOLD_SECONDS
) -> dict[str, Any]:
    """Объединённая сводка для get_worker_status tool / TUI dashboard."""
    age = last_event_age_seconds(jsonl_path)
    return {
        "pid": pid,
        "alive": is_alive(pid),
        "last_event_age_seconds": age,
        "stalled": (age is not None and age >= float(threshold_seconds)),
        "threshold_seconds": threshold_seconds,
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def stall_threshold_from_minutes(minutes: int) -> timedelta:
    """Helper для TUI/CLI surface."""
    return timedelta(minutes=minutes)


__all__ = [
    "DEFAULT_STALL_THRESHOLD_SECONDS",
    "heartbeat_summary",
    "is_alive",
    "is_stalled",
    "last_event_age_seconds",
    "safe_to_kill",
    "stall_threshold_from_minutes",
]
