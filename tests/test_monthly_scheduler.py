"""Tests for runtime/monthly_scheduler — M3."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from bmad_orchestrator.runtime.event_loop import EventLoop, EventType
from bmad_orchestrator.runtime.monthly_scheduler import (
    _should_emit,
    run_monthly_scheduler,
    start_monthly_scheduler,
)


def test_should_emit_on_first_of_month_after_10() -> None:
    now = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    assert _should_emit(now, last_emit=None) is True


def test_should_not_emit_before_10_on_first() -> None:
    now = datetime(2026, 6, 1, 9, 59, 0, tzinfo=UTC)
    assert _should_emit(now, last_emit=None) is False


def test_should_not_emit_on_other_day() -> None:
    now = datetime(2026, 6, 15, 10, 0, 0, tzinfo=UTC)
    assert _should_emit(now, last_emit=None) is False


def test_should_not_emit_if_already_emitted_this_month() -> None:
    now = datetime(2026, 6, 1, 11, 0, 0, tzinfo=UTC)
    last_emit = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    assert _should_emit(now, last_emit=last_emit) is False


def test_should_emit_if_last_emit_was_previous_month() -> None:
    now = datetime(2026, 7, 1, 10, 0, 0, tzinfo=UTC)
    last_emit = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    assert _should_emit(now, last_emit=last_emit) is True


def test_should_emit_if_last_emit_was_previous_year() -> None:
    now = datetime(2027, 1, 1, 10, 0, 0, tzinfo=UTC)
    last_emit = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    assert _should_emit(now, last_emit=last_emit) is True


def test_scheduler_emits_on_first_of_month(tmp_path: Path) -> None:
    """The scheduler emits to the bus queue; verify by reading from the queue."""

    async def _inner() -> None:
        from datetime import UTC, datetime

        bus = EventLoop()

        with patch(
            "bmad_orchestrator.runtime.monthly_scheduler.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
            mock_dt.fromisoformat = datetime.fromisoformat
            task = asyncio.create_task(
                run_monthly_scheduler(bus, tmp_path / "state.db", check_interval=0.001)
            )
            # Wait long enough for at least one check cycle
            await asyncio.sleep(0.02)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # The scheduler should have put MONTHLY_REVIEW_SCHEDULED on the bus queue
        event = await bus.next(timeout=0.1)
        assert event is not None
        assert event.type == EventType.MONTHLY_REVIEW_SCHEDULED

    asyncio.run(_inner())


def test_start_monthly_scheduler_returns_task(tmp_path: Path) -> None:
    async def _test() -> None:
        bus = EventLoop()
        task = start_monthly_scheduler(bus, tmp_path / "state.db", check_interval=60.0)
        assert isinstance(task, asyncio.Task)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_test())
