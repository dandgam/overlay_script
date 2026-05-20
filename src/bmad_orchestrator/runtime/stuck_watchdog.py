"""Cumulative stuck-worker watchdog (NEW-33.2).

Wraps :func:`runtime.worker_spawn.tail_jsonl_events` with a periodic stuck
check. Stuck = no JSONL growth AND no new commits past ``base_sha`` AND no
worktree dirty-file growth for ``stuck_threshold_seconds``. On stuck → emits
``HUMAN_QUERY`` + ``WORKER_STUCK_TIMEOUT`` on the bus, yields a synthetic
``worker_completed`` (status=failed, exit_code=-1) so the existing
``_tail_and_emit_completion`` pipeline drains gracefully, then returns.

Why this exists — pilot 2b root cause: story 3-2-zfs's dev worker wrote a
commit + staged files, but its events.jsonl froze at 4 lines (wave-env
mismatch routed it to ``runs/default/`` instead of ``runs/2b/``). The
orchestrator's tail loop waited 30+ minutes for a terminal event that never
came. Two guard rails missed it: ``subprocess_timeout`` only covers a single
``claude -p`` invocation, and ``liveness.is_stalled`` returned False for an
empty/quiet JSONL pre-NEW-33.1.

NEW-36 extends the liveness signals with a third indicator: worktree dirty
count (number of changed/untracked files from ``git status --porcelain``). A
worker that is actively writing files to the worktree but has not yet committed
— e.g. pilot 2f story 8-1 that wrote 34 files in 30 min — will keep growing
the dirty count and is NOT stuck. The clock resets on each of the three
signals (events, commits, dirty growth) independently.

The pure ``evaluate_stuck`` helper is unit-tested; the
``tail_with_stuck_watchdog`` iterator is integration-tested with a mock bus.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from bmad_orchestrator.runtime.event_loop import EventLoop, EventType
from bmad_orchestrator.runtime.liveness import last_event_age_seconds
from bmad_orchestrator.runtime.worker_spawn import tail_jsonl_events

log = structlog.get_logger("stuck_watchdog")

DEFAULT_STUCK_TIMEOUT_SECONDS = 1800  # 30 min
DEFAULT_CHECK_INTERVAL_SECONDS = 60


@dataclass(slots=True)
class StuckCheckResult:
    stuck: bool
    reason: str
    elapsed_seconds: float
    last_event_age_seconds: float | None
    commits_seen: int
    dirty_count: int = 0  # NEW-36: current worktree dirty-file count


def evaluate_stuck(
    *,
    jsonl_path: Path,
    start_time: datetime,
    now: datetime,
    last_commit_count: int,
    current_commit_count: int,
    last_dirty_count: int = 0,       # NEW-36
    current_dirty_count: int = 0,    # NEW-36
    stuck_threshold_seconds: float,
) -> StuckCheckResult:
    """Pure stuck-decision logic (NEW-33.2 + NEW-36).

    Worker is considered stuck iff ALL THREE:
    * no new commits past ``base_sha`` since ``last_commit_count`` (no
      git-level progress), AND
    * either the latest JSONL event is older than ``stuck_threshold_seconds``
      OR no JSONL events exist and elapsed since ``start_time`` exceeds the
      threshold (NEW-33.1 reuse), AND
    * worktree dirty-file count has NOT grown since last tick (NEW-36).

    Any of the three signals — events_fresh, commits_growing, or
    worktree_growing — is sufficient to keep the worker alive.

    ``last_dirty_count`` / ``current_dirty_count`` default to 0 (backwards
    compatible with callers that don't supply the dirty counter).
    """
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    elapsed = (now - start_time).total_seconds()
    age = last_event_age_seconds(jsonl_path)

    if current_commit_count > last_commit_count:
        return StuckCheckResult(
            stuck=False,
            reason="commits_growing",
            elapsed_seconds=elapsed,
            last_event_age_seconds=age,
            commits_seen=current_commit_count,
            dirty_count=current_dirty_count,
        )

    if age is not None and age < stuck_threshold_seconds:
        return StuckCheckResult(
            stuck=False,
            reason="events_fresh",
            elapsed_seconds=elapsed,
            last_event_age_seconds=age,
            commits_seen=current_commit_count,
            dirty_count=current_dirty_count,
        )

    if age is None and elapsed < stuck_threshold_seconds:
        return StuckCheckResult(
            stuck=False,
            reason="warmup",
            elapsed_seconds=elapsed,
            last_event_age_seconds=age,
            commits_seen=current_commit_count,
            dirty_count=current_dirty_count,
        )

    # NEW-36: worktree dirty count growing → worker is actively writing files.
    if current_dirty_count > last_dirty_count:
        return StuckCheckResult(
            stuck=False,
            reason="worktree_growing",
            elapsed_seconds=elapsed,
            last_event_age_seconds=age,
            commits_seen=current_commit_count,
            dirty_count=current_dirty_count,
        )

    reason = "events_stale" if age is not None else "no_events"
    return StuckCheckResult(
        stuck=True,
        reason=reason,
        elapsed_seconds=elapsed,
        last_event_age_seconds=age,
        commits_seen=current_commit_count,
        dirty_count=current_dirty_count,
    )


CommitCounter = Callable[[], Awaitable[int]]
DirtyCounter = Callable[[], Awaitable[int]]  # NEW-36


async def tail_with_stuck_watchdog(
    jsonl_path: Path,
    bus: EventLoop,
    *,
    story_id: str,
    worktree: str,
    commit_counter: CommitCounter,
    dirty_counter: DirtyCounter | None = None,  # NEW-36
    stuck_threshold_seconds: float = DEFAULT_STUCK_TIMEOUT_SECONDS,
    check_interval_seconds: float = DEFAULT_CHECK_INTERVAL_SECONDS,
    now_factory: Callable[[], datetime] = lambda: datetime.now(UTC),
    inner_factory: Callable[[Path], AsyncIterator[dict[str, Any]]] = tail_jsonl_events,
) -> AsyncIterator[dict[str, Any]]:
    """Yield events from worker JSONL; break early on cumulative stuck.

    On stuck the watchdog emits ``HUMAN_QUERY`` + ``WORKER_STUCK_TIMEOUT`` on
    the bus and synthesises a terminal ``worker_completed`` event so the
    caller's existing terminal-handling logic runs unchanged.

    ``dirty_counter`` (NEW-36) — optional async callable that returns the
    number of changed/untracked files in the worktree (``git status
    --porcelain`` line count). When supplied, growing dirty count resets the
    stuck clock exactly like a new commit does — a worker writing many files
    without committing (e.g. pilot 2f story 8-1: 34 files in 30 min) is
    *not* stuck.

    ``inner_factory`` is injected for tests — production callers leave the
    default :func:`tail_jsonl_events`.
    """
    start_time = now_factory()
    last_commit_count = 0
    last_dirty_count = 0  # NEW-36
    inner = inner_factory(jsonl_path).__aiter__()

    async def _next_event() -> dict[str, Any] | None:
        try:
            return await inner.__anext__()
        except StopAsyncIteration:
            return None

    pending: asyncio.Task[dict[str, Any] | None] = asyncio.create_task(_next_event())

    try:
      while True:
        try:
            ev = await asyncio.wait_for(
                asyncio.shield(pending), timeout=check_interval_seconds
            )
        except StopAsyncIteration:
            return
        except TimeoutError:
            try:
                current_commits = await commit_counter()
            except Exception as exc:
                log.warning(
                    "stuck_watchdog_commit_count_failed",
                    story_id=story_id,
                    error=str(exc),
                )
                current_commits = last_commit_count
            # NEW-36: query worktree dirty count if a counter was provided.
            current_dirty = last_dirty_count
            if dirty_counter is not None:
                try:
                    current_dirty = await dirty_counter()
                except Exception as exc:
                    log.warning(
                        "stuck_watchdog_dirty_count_failed",
                        story_id=story_id,
                        error=str(exc),
                    )
            result = evaluate_stuck(
                jsonl_path=jsonl_path,
                start_time=start_time,
                now=now_factory(),
                last_commit_count=last_commit_count,
                current_commit_count=current_commits,
                last_dirty_count=last_dirty_count,
                current_dirty_count=current_dirty,
                stuck_threshold_seconds=stuck_threshold_seconds,
            )
            if not result.stuck:
                if current_commits > last_commit_count:
                    last_commit_count = current_commits
                    start_time = now_factory()  # reset clock on commit progress
                elif current_dirty > last_dirty_count:
                    last_dirty_count = current_dirty
                    start_time = now_factory()  # NEW-36: reset clock on dirty growth
                continue
            pending.cancel()
            log.warning(
                "stuck_watchdog_tripped",
                story_id=story_id,
                worktree=worktree,
                reason=result.reason,
                elapsed_seconds=result.elapsed_seconds,
                last_event_age_seconds=result.last_event_age_seconds,
                commits_seen=result.commits_seen,
                dirty_count=result.dirty_count,
            )
            stuck_payload = {
                "story_id": story_id,
                "worktree": worktree,
                "jsonl": str(jsonl_path),
                "elapsed_seconds": result.elapsed_seconds,
                "last_event_age_seconds": result.last_event_age_seconds,
                "commits_seen": result.commits_seen,
                "dirty_count": result.dirty_count,
                "reason": result.reason,
            }
            await bus.emit(EventType.WORKER_STUCK_TIMEOUT, **stuck_payload)
            await bus.emit(
                EventType.HUMAN_QUERY,
                source="stuck_watchdog",
                story_id=story_id,
                worktree=worktree,
                question=(
                    f"Worker {story_id} stuck for "
                    f"{int(result.elapsed_seconds)}s without JSONL growth, "
                    f"new commits, or worktree file changes — "
                    f"investigate / retry / abort?"
                ),
                context=stuck_payload,
            )
            yield {
                "event_type": "worker_completed",
                "ts": now_factory().isoformat(timespec="seconds"),
                "exit_code": -1,
                "status": "stuck_timeout",
                "story_id": story_id,
                "source": "stuck_watchdog",
                "stuck_reason": result.reason,
            }
            return
        else:
            if ev is None:
                return
            yield ev
            if ev.get("event_type") in {"worker_completed", "worker_halt_file"}:
                return
            pending = asyncio.create_task(_next_event())
    finally:
        if not pending.done():
            pending.cancel()
            with contextlib.suppress(BaseException):
                await pending
        aclose = getattr(inner, "aclose", None)
        if aclose is not None:
            with contextlib.suppress(BaseException):
                await aclose()


__all__ = [
    "DEFAULT_CHECK_INTERVAL_SECONDS",
    "DEFAULT_STUCK_TIMEOUT_SECONDS",
    "CommitCounter",
    "DirtyCounter",
    "StuckCheckResult",
    "evaluate_stuck",
    "tail_with_stuck_watchdog",
]
