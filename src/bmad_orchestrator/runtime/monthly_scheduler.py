"""Asyncio monthly scheduler — emits MONTHLY_REVIEW_SCHEDULED on 1st of month at 10:00 UTC.

Runs as a background task inside the main orchestrator event loop.
Persists last_emit timestamp to state_db key to survive restarts.

Also exposed via CLI: ``bmad-orchestrator self-learning cron-emit`` for
systemd timer / crontab production deployments (Q5 decision: both paths).

See spec/spec_self_learning_loop.md §3.2 + Q5.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import structlog

from bmad_orchestrator.runtime.event_loop import EventLoop, EventType

log = structlog.get_logger("monthly_scheduler")

# Key used to persist last_emit in the kv table (if available).
_LAST_EMIT_KEY = "self_learning.monthly_scheduler.last_emit"

# Check interval: once per hour is cheap enough and fast enough to not miss
# the window on the 1st.
_CHECK_INTERVAL_SECONDS = 3600


def _should_emit(now: datetime, last_emit: datetime | None) -> bool:
    """True if we're on the 1st of the month at or after 10:00 UTC,
    and we haven't already emitted this month.
    """
    if now.day != 1:
        return False
    if now.hour < 10:
        return False
    if last_emit is None:
        return True
    # Already emitted this month?
    return last_emit.year != now.year or last_emit.month != now.month


async def _persist_last_emit(state_db_path: Path, ts: str) -> None:
    """Best-effort: persist last_emit timestamp to SQLite kv table."""
    try:
        import aiosqlite

        async with aiosqlite.connect(state_db_path) as db:
            await db.execute(
                """CREATE TABLE IF NOT EXISTS kv_store (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                )"""
            )
            await db.execute(
                "INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)",
                (_LAST_EMIT_KEY, ts),
            )
            await db.commit()
    except Exception as exc:
        log.warning("monthly_scheduler_persist_failed", error=str(exc))


async def _load_last_emit(state_db_path: Path) -> datetime | None:
    """Best-effort: load last_emit from SQLite kv table."""
    try:
        import aiosqlite

        async with aiosqlite.connect(state_db_path) as db:
            async with db.execute(
                "SELECT value FROM kv_store WHERE key = ?",
                (_LAST_EMIT_KEY,),
            ) as cursor:
                row = await cursor.fetchone()
        if row:
            return datetime.fromisoformat(row[0])
    except Exception as exc:
        log.debug("monthly_scheduler_load_last_emit_failed", error=str(exc))
    return None


async def run_monthly_scheduler(
    bus: EventLoop,
    state_db_path: Path,
    check_interval: float = _CHECK_INTERVAL_SECONDS,
) -> None:
    """Background task: check once per hour, emit on 1st of month at 10:00 UTC.

    Persists last_emit to state_db to survive restarts. Cancel-safe.
    """
    log.info("monthly_scheduler_started", check_interval_s=check_interval)
    last_emit = await _load_last_emit(state_db_path)

    while True:
        try:
            await asyncio.sleep(check_interval)
        except asyncio.CancelledError:
            log.info("monthly_scheduler_cancelled")
            return

        now = datetime.now(UTC)
        if _should_emit(now, last_emit):
            log.info("monthly_review_emitting", ts=now.isoformat())
            await bus.emit(
                EventType.MONTHLY_REVIEW_SCHEDULED,
                source="monthly_scheduler",
                triggered_at=now.isoformat(),
            )
            last_emit = now
            await _persist_last_emit(state_db_path, now.isoformat())


def start_monthly_scheduler(
    bus: EventLoop,
    state_db_path: Path,
    check_interval: float = _CHECK_INTERVAL_SECONDS,
) -> asyncio.Task[None]:
    """Spawn the monthly scheduler as a background asyncio.Task."""
    return asyncio.create_task(
        run_monthly_scheduler(bus, state_db_path, check_interval),
        name="monthly_review_scheduler",
    )


__all__ = [
    "_should_emit",
    "run_monthly_scheduler",
    "start_monthly_scheduler",
]
