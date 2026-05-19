"""Tests for NEW-33.2 stuck-worker watchdog."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from bmad_orchestrator.runtime.event_loop import EventLoop, EventType
from bmad_orchestrator.runtime.stuck_watchdog import (
    StuckCheckResult,
    evaluate_stuck,
    tail_with_stuck_watchdog,
)

# ── pure evaluate_stuck ─────────────────────────────────────────────────────


def test_evaluate_stuck_fresh_events_not_stuck(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    fresh = datetime.now(UTC).isoformat(timespec="seconds")
    p.write_text(f'{{"event_type":"x","ts":"{fresh}"}}\n', encoding="utf-8")
    r = evaluate_stuck(
        jsonl_path=p,
        start_time=datetime.now(UTC) - timedelta(hours=1),
        now=datetime.now(UTC),
        last_commit_count=0,
        current_commit_count=0,
        stuck_threshold_seconds=600,
    )
    assert r.stuck is False
    assert r.reason == "events_fresh"


def test_evaluate_stuck_commits_growing_resets(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    # Old events but new commits arrived → not stuck.
    stale = (datetime.now(UTC) - timedelta(hours=2)).isoformat(timespec="seconds")
    p.write_text(f'{{"event_type":"x","ts":"{stale}"}}\n', encoding="utf-8")
    r = evaluate_stuck(
        jsonl_path=p,
        start_time=datetime.now(UTC) - timedelta(hours=2),
        now=datetime.now(UTC),
        last_commit_count=0,
        current_commit_count=3,
        stuck_threshold_seconds=600,
    )
    assert r.stuck is False
    assert r.reason == "commits_growing"
    assert r.commits_seen == 3


def test_evaluate_stuck_warmup_no_events_not_yet(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"  # not created
    r = evaluate_stuck(
        jsonl_path=p,
        start_time=datetime.now(UTC),
        now=datetime.now(UTC) + timedelta(seconds=30),
        last_commit_count=0,
        current_commit_count=0,
        stuck_threshold_seconds=600,
    )
    assert r.stuck is False
    assert r.reason == "warmup"


def test_evaluate_stuck_stale_events_no_commits(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    stale = (datetime.now(UTC) - timedelta(hours=2)).isoformat(timespec="seconds")
    p.write_text(f'{{"event_type":"x","ts":"{stale}"}}\n', encoding="utf-8")
    r = evaluate_stuck(
        jsonl_path=p,
        start_time=datetime.now(UTC) - timedelta(hours=2),
        now=datetime.now(UTC),
        last_commit_count=2,
        current_commit_count=2,
        stuck_threshold_seconds=600,
    )
    assert r.stuck is True
    assert r.reason == "events_stale"


def test_evaluate_stuck_no_events_past_threshold(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"  # missing
    r = evaluate_stuck(
        jsonl_path=p,
        start_time=datetime.now(UTC) - timedelta(hours=2),
        now=datetime.now(UTC),
        last_commit_count=0,
        current_commit_count=0,
        stuck_threshold_seconds=600,
    )
    assert r.stuck is True
    assert r.reason == "no_events"


def test_evaluate_stuck_naive_datetimes_handled(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    naive_start = (datetime.now(UTC) - timedelta(hours=2)).replace(tzinfo=None)
    naive_now = datetime.now(UTC).replace(tzinfo=None)
    r = evaluate_stuck(
        jsonl_path=p,
        start_time=naive_start,
        now=naive_now,
        last_commit_count=0,
        current_commit_count=0,
        stuck_threshold_seconds=600,
    )
    assert isinstance(r, StuckCheckResult)


# ── tail_with_stuck_watchdog ────────────────────────────────────────────────


async def _fake_inner_no_events(jsonl_path: Path) -> AsyncIterator[dict[str, Any]]:
    """Mock inner tail that never yields — simulates a frozen JSONL."""
    await asyncio.Event().wait()
    yield  # type: ignore[unreachable]


async def _fake_inner_one_then_idle(
    events: list[dict[str, Any]],
) -> AsyncIterator[dict[str, Any]]:
    """Yield events from a list, then idle forever (cancellable on aclose)."""
    for ev in events:
        yield ev
    # Block on an event that never sets — when aclose() is called the
    # GeneratorExit propagates cleanly out of the await.
    await asyncio.Event().wait()
    yield  # type: ignore[unreachable]


@pytest.mark.asyncio
async def test_watchdog_trips_after_threshold_no_events(tmp_path: Path) -> None:
    bus = EventLoop()

    jsonl = tmp_path / "events.jsonl"  # never created
    fake_now = [datetime.now(UTC)]

    def now_factory() -> datetime:
        return fake_now[0]

    async def zero_commits() -> int:
        return 0

    def inner_factory(_path: Path) -> AsyncIterator[dict[str, Any]]:
        return _fake_inner_no_events(_path)

    yielded: list[dict[str, Any]] = []

    async def consume() -> None:
        async for ev in tail_with_stuck_watchdog(
            jsonl,
            bus,
            story_id="s1",
            worktree="/tmp/wt",
            commit_counter=zero_commits,
            stuck_threshold_seconds=10,
            check_interval_seconds=0.1,
            now_factory=now_factory,
            inner_factory=inner_factory,
        ):
            yielded.append(ev)

    task = asyncio.create_task(consume())
    # Let one watchdog tick run with start_time = "now"; not stuck yet.
    await asyncio.sleep(0.15)
    # Advance clock past threshold.
    fake_now[0] = datetime.now(UTC) + timedelta(seconds=20)
    await asyncio.wait_for(task, timeout=2.0)

    # Synthetic terminal event yielded.
    assert len(yielded) == 1
    assert yielded[0]["event_type"] == "worker_completed"
    assert yielded[0]["status"] == "stuck_timeout"
    # Bus queue carries WORKER_STUCK_TIMEOUT + HUMAN_QUERY (subscribers only
    # fire under dispatch_one/drain — we inspect the queue directly).
    queued_types: set[EventType] = set()
    while not bus.queue.empty():
        queued_types.add(bus.queue.get_nowait().type)
    assert EventType.WORKER_STUCK_TIMEOUT in queued_types
    assert EventType.HUMAN_QUERY in queued_types


@pytest.mark.asyncio
async def test_watchdog_passes_through_terminal_event(tmp_path: Path) -> None:
    """Real tail_jsonl_events: file with stdout_line + worker_completed → both pass through."""
    bus = EventLoop()
    jsonl = tmp_path / "events.jsonl"
    fresh = datetime.now(UTC).isoformat(timespec="seconds")
    jsonl.write_text(
        '{"event_type":"stdout_line","text":"hello","ts":"' + fresh + '"}\n'
        '{"event_type":"worker_completed","exit_code":0,"ts":"' + fresh + '"}\n',
        encoding="utf-8",
    )

    async def zero_commits() -> int:
        return 0

    yielded: list[dict[str, Any]] = []
    async for ev in tail_with_stuck_watchdog(
        jsonl,
        bus,
        story_id="s1",
        worktree="/tmp/wt",
        commit_counter=zero_commits,
        stuck_threshold_seconds=600,
        check_interval_seconds=1.0,
    ):
        yielded.append(ev)

    assert [e["event_type"] for e in yielded] == ["stdout_line", "worker_completed"]
    queued: list[Any] = []
    while not bus.queue.empty():
        queued.append(bus.queue.get_nowait())
    assert not any(e.type == EventType.WORKER_STUCK_TIMEOUT for e in queued)


@pytest.mark.asyncio
async def test_watchdog_resets_on_new_commits(tmp_path: Path) -> None:
    """Commits growing → start_time resets, watchdog does not trip."""
    bus = EventLoop()

    jsonl = tmp_path / "events.jsonl"  # never created
    fake_now = [datetime.now(UTC)]
    commits = [0]

    def now_factory() -> datetime:
        return fake_now[0]

    async def grow_commits() -> int:
        # Every call returns one more; simulates real worker making progress.
        commits[0] += 1
        return commits[0]

    def inner_factory(_path: Path) -> AsyncIterator[dict[str, Any]]:
        return _fake_inner_no_events(_path)

    async def consume() -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        async for ev in tail_with_stuck_watchdog(
            jsonl,
            bus,
            story_id="s1",
            worktree="/tmp/wt",
            commit_counter=grow_commits,
            stuck_threshold_seconds=10,
            check_interval_seconds=0.1,
            now_factory=now_factory,
            inner_factory=inner_factory,
        ):
            out.append(ev)
        return out

    task = asyncio.create_task(consume())
    # Let watchdog tick a few times; commits grow, no trip.
    for _ in range(5):
        await asyncio.sleep(0.12)
        fake_now[0] += timedelta(seconds=20)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    queued: list[Any] = []
    while not bus.queue.empty():
        queued.append(bus.queue.get_nowait())
    assert not any(e.type == EventType.WORKER_STUCK_TIMEOUT for e in queued)
