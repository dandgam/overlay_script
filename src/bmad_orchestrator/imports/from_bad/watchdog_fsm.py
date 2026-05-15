"""Watchdog FSM — stale worker detection. Адаптировано из BAD.

Original: https://github.com/stephenleo/bmad-autonomous-development/blob/main/skills/bad/references/coordinator/pattern-watchdog.md
License: MIT — Marie Stephen Leo

State machine:
1. Каждые 120s проверяем mtime activity.log worker'а
2. Если mtime > STALE_THRESHOLD (default 5 мин) → emit `STALE:<min>:<last_line>`
3. Coordinator решает [K]ill / [R]estart / [S]kip / [A]bort
4. У нас: интегрируется с feedback_dont_kill_if_alive — сначала 3-signal alive check (psutil), потом действие
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class WatchdogDecision(str, Enum):
    KILL = "K"  # Process dead OR stuck beyond recovery
    RESTART = "R"  # Restart worker (потеря progress на текущей story)
    SKIP = "S"  # Skip this story, continue with others
    ABORT = "A"  # Halt the whole batch


@dataclass
class StaleSignal:
    worker_pid: int
    log_path: Path
    stale_minutes: float
    last_line: str


def check_stale(log_path: Path, threshold_minutes: float = 5.0) -> StaleSignal | None:
    """Read mtime of activity log; emit StaleSignal if stale."""
    if not log_path.exists():
        return None
    mtime = log_path.stat().st_mtime
    age_minutes = (time.time() - mtime) / 60.0
    if age_minutes < threshold_minutes:
        return None
    last_line = ""
    try:
        with log_path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 2048))
            tail = fh.read().decode(errors="ignore").splitlines()
            if tail:
                last_line = tail[-1]
    except OSError:
        pass
    return StaleSignal(
        worker_pid=0,
        log_path=log_path,
        stale_minutes=age_minutes,
        last_line=last_line,
    )


# TODO: integrate with runtime/liveness.py is_alive() — следует БАД'овским ALIVE-checks
# плюс наш feedback_dont_kill_if_alive (3-signal: CPU + I/O + network)
