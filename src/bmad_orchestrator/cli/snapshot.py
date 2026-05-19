"""Sync helpers that populate :class:`DashboardSnapshot` from live sources.

Sources, all best-effort:
  - ``state.db``               → status / wave / project (latest active session) +
                                  daily-scope spend
  - ``sprint-status.yaml``     → progress (done/total) + ready_next + done_recent
  - ``runs/<wave>/*.events.jsonl`` → active workers + tail events

Each source is isolated: missing file, malformed line, or sqlite error degrades
that one source to empty defaults — the renderer still draws a frame.

`sqlite3` (sync, read-only URI) is used instead of `aiosqlite` so the helpers
can be called from typer commands without spinning an event loop. This avoids
clashing with `run_live` which has its own refresh loop.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bmad_orchestrator.agent.tools._common import (
    is_pid_alive,
    jsonl_tail,
    read_sprint_status_yaml,
    runs_dir,
)
from bmad_orchestrator.config import Settings
from bmad_orchestrator.runtime.bmad_format import parse_sprint_status_bmad

logger = logging.getLogger(__name__)

# state.db CHECK constraint allows running/paused/stopped/error. Dashboard
# vocabulary is running/paused/halted/idle — `stopped` maps to idle so a
# cleanly-ended wave doesn't surface as a red `halted`.
_DB_STATUS_TO_DASHBOARD: dict[str, str] = {
    "running": "running",
    "paused": "paused",
    "error": "halted",
    "stopped": "idle",
}

_WORKER_LIFECYCLE_TERMINAL: frozenset[str] = frozenset(
    {"worker_completed", "worker_halt_file"}
)


@dataclass(slots=True)
class SessionRow:
    """Latest agent_session row, with status mapped to the dashboard vocab."""

    id: int
    target_project: str
    wave: str
    status: str
    started_at: str


@dataclass(slots=True)
class SprintProgress:
    done: int = 0
    total: int = 0
    ready_next: list[str] = field(default_factory=list)
    done_recent: list[str] = field(default_factory=list)


# ── state.db ────────────────────────────────────────────────────────────────


def _open_ro(db_path: Path) -> sqlite3.Connection:
    """Open `db_path` read-only via URI. Caller is responsible for closing."""
    uri = f"file:{db_path}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=5.0)


def read_latest_session(db_path: Path) -> SessionRow | None:
    """Return the most recent running/paused session, or None.

    Returns None when the DB file is absent or no active session exists. Errors
    are swallowed at DEBUG level — a freshly initialized orchestrator legitimately
    has no rows.
    """
    if not db_path.exists():
        return None
    try:
        with _open_ro(db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                """
                SELECT id, target_project, wave, status, started_at
                  FROM agent_session
                 WHERE status IN ('running', 'paused')
                 ORDER BY started_at DESC
                 LIMIT 1
                """
            )
            row = cur.fetchone()
    except sqlite3.Error as exc:
        logger.debug("snapshot.state_db.session_read_failed: %s", exc)
        return None
    if row is None:
        return None
    raw_status = str(row["status"])
    return SessionRow(
        id=int(row["id"]),
        target_project=str(row["target_project"]),
        wave=str(row["wave"]),
        status=_DB_STATUS_TO_DASHBOARD.get(raw_status, "idle"),
        started_at=str(row["started_at"]),
    )


def read_session_budget(db_path: Path, session_id: int) -> float:
    """Daily-scope spend for `session_id`. 0.0 on miss/error.

    Why scope='day' specifically: budget_guard tracks the same spend at three
    scopes (story, batch, day) — summing all rows would triple-count. Day is
    the broadest and matches `settings.budget.daily_limit_usd` (the cap shown
    in the header).
    """
    if not db_path.exists():
        return 0.0
    try:
        with _open_ro(db_path) as conn:
            cur = conn.execute(
                """
                SELECT COALESCE(SUM(spent_usd), 0.0) AS total
                  FROM budget_tracker
                 WHERE session_id = ? AND scope = 'day'
                """,
                (session_id,),
            )
            row = cur.fetchone()
    except sqlite3.Error as exc:
        logger.debug("snapshot.state_db.budget_read_failed: %s", exc)
        return 0.0
    return float(row[0]) if row else 0.0


def derive_budget_level(spent_usd: float, cap_usd: float) -> str:
    """ok / alarm / halt by spent/cap ratio (alarm ≥ 0.8, halt ≥ 1.0)."""
    if cap_usd <= 0:
        return "ok"
    ratio = spent_usd / cap_usd
    if ratio >= 1.0:
        return "halt"
    if ratio >= 0.8:
        return "alarm"
    return "ok"


# ── sprint-status.yaml ──────────────────────────────────────────────────────


def read_sprint_progress(settings: Settings) -> SprintProgress:
    """Aggregate progress + ready/done lists from sprint-status.yaml.

    Empty result when the YAML is missing or malformed.
    """
    try:
        raw = read_sprint_status_yaml(settings)
    except Exception as exc:
        logger.debug("snapshot.sprint_status.read_failed: %s", exc)
        return SprintProgress()
    if not raw:
        return SprintProgress()
    normalized = parse_sprint_status_bmad(raw)
    total = 0
    done = 0
    ready_next: list[str] = []
    done_recent: list[str] = []
    for epic in normalized.get("epics", {}).values():
        for sid, status in epic.get("stories", {}).items():
            total += 1
            if status == "done":
                done += 1
                done_recent.append(sid)
            elif status == "ready-for-dev":
                ready_next.append(sid)
    return SprintProgress(
        done=done,
        total=total,
        ready_next=ready_next[:10],
        done_recent=done_recent[-5:],
    )


# ── events.jsonl ────────────────────────────────────────────────────────────


def _read_jsonl_all(path: Path) -> list[dict[str, Any]]:
    """Read every JSON object from `path`, skipping malformed lines."""
    out: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.debug("snapshot.events.read_failed path=%s: %s", path, exc)
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _worktree_from_jsonl_path(path: Path) -> str:
    """`<worktree>.events.jsonl` → `<worktree>`."""
    name = path.name
    if name.endswith(".events.jsonl"):
        return name[: -len(".events.jsonl")]
    return path.stem


def read_active_workers(settings: Settings, wave: str) -> list[dict[str, Any]]:
    """Workers whose last lifecycle event is `worker_spawned`.

    A worker is "active" if `worker_spawned` appears later in the file than any
    `worker_completed` / `worker_halt_file`. Dict shape matches what
    `tui._workers_panel` reads (worktree / story_id / stage / state /
    tokens_used / cost_usd). Stage and counters are placeholders for v1 — the
    events don't carry them and the renderer tolerates string fallbacks.
    """
    wave_dir = runs_dir(settings) / wave
    if not wave_dir.exists():
        return []
    workers: list[dict[str, Any]] = []
    for jsonl_path in sorted(wave_dir.glob("*.events.jsonl")):
        events = _read_jsonl_all(jsonl_path)
        last_spawned: dict[str, Any] | None = None
        terminated = False
        for ev in events:
            etype = ev.get("event_type")
            if etype == "worker_spawned":
                last_spawned = ev
                terminated = False
            elif etype in _WORKER_LIFECYCLE_TERMINAL:
                terminated = True
        if last_spawned is None or terminated:
            continue
        pid = last_spawned.get("pid")
        state = "active"
        if isinstance(pid, int) and pid > 0 and not is_pid_alive(pid):
            state = "halted"
        workers.append(
            {
                "worktree": _worktree_from_jsonl_path(jsonl_path),
                "story_id": str(last_spawned.get("story_id", "?")),
                "stage": "dev",
                "state": state,
                "tokens_used": 0,
                "cost_usd": 0.0,
            }
        )
    return workers


def _format_event(ev: dict[str, Any], source: str) -> str:
    etype = str(ev.get("event_type", "?"))
    story = ev.get("story_id")
    if etype == "stdout_line":
        text = str(ev.get("text", "")).strip()
        return f"[{source}] {text[:80]}" if text else f"[{source}] stdout"
    label = etype
    if story:
        label = f"{label} {story}"
    return f"[{source}] {label}"


def read_events_tail(
    settings: Settings, wave: str, limit: int = 15
) -> list[str]:
    """Tail last N events across all worker JSONL files in `wave`.

    Returns dashboard-ready strings (already prefixed with worktree). Events
    have no per-event timestamp in v1, so ordering = insertion order across
    workers (sorted by worktree filename) → adequate for live-tail use.
    """
    wave_dir = runs_dir(settings) / wave
    if not wave_dir.exists():
        return []
    lines: list[str] = []
    for jsonl_path in sorted(wave_dir.glob("*.events.jsonl")):
        source = _worktree_from_jsonl_path(jsonl_path)
        for ev in jsonl_tail(jsonl_path, limit):
            lines.append(_format_event(ev, source))
    return lines[-limit:]


# ── agent_thinking ──────────────────────────────────────────────────────────


def derive_agent_thinking(status: str, workers: list[dict[str, Any]]) -> str:
    """Human-readable phase description rendered in the `agent` panel."""
    from bmad_orchestrator.cli.i18n import t

    if status == "running":
        if workers:
            return f"работают {len(workers)} worker(ов)"
        return "пилотирую wave"
    if status == "paused":
        return "пауза — дожидаюсь workers"
    if status == "halted":
        return "остановлен (см. логи)"
    return t("agent.idle")


__all__ = [
    "SessionRow",
    "SprintProgress",
    "derive_agent_thinking",
    "derive_budget_level",
    "read_active_workers",
    "read_events_tail",
    "read_latest_session",
    "read_session_budget",
    "read_sprint_progress",
]
