"""Tests for NEW-37 — review-worker stuck watchdog.

Covers:
  1. _real_security_review_runner: watchdog trips on silent JSONL (15-min threshold).
  2. _run_merge_gate_spec_stage: watchdog trips, verdict defaults to "error".
  3. _run_merge_gate_quality_stage: same.
  4. BMAD_REVIEW_TIMEOUT_SEC env knob respected.
  5. Without bus (legacy path) — falls back to tail_jsonl_events (no watchdog).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from bmad_orchestrator.runtime.event_loop import EventLoop

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_jsonl_events(events: list[dict[str, Any]], tmp_path: Path) -> Path:
    """Write a JSONL file with the given events and return its path."""
    p = tmp_path / "events.jsonl"
    p.write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
    )
    return p


async def _drain(bus: EventLoop) -> list[Any]:
    """Drain all queued events from bus."""
    out = []
    while not bus.queue.empty():
        out.append(bus.queue.get_nowait())
    return out


# ---------------------------------------------------------------------------
# _review_timeout_sec helper
# ---------------------------------------------------------------------------

def test_review_timeout_sec_default() -> None:
    """Default returns 900 seconds."""
    import os

    from bmad_orchestrator.agent.run import _review_timeout_sec

    os.environ.pop("BMAD_REVIEW_TIMEOUT_SEC", None)
    assert _review_timeout_sec() == 900


def test_review_timeout_sec_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """BMAD_REVIEW_TIMEOUT_SEC is respected."""
    from bmad_orchestrator.agent.run import _review_timeout_sec

    monkeypatch.setenv("BMAD_REVIEW_TIMEOUT_SEC", "300")
    assert _review_timeout_sec() == 300


def test_review_timeout_sec_min_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Values below 60 are clamped to 60."""
    from bmad_orchestrator.agent.run import _review_timeout_sec

    monkeypatch.setenv("BMAD_REVIEW_TIMEOUT_SEC", "5")
    assert _review_timeout_sec() == 60


def test_review_timeout_sec_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid string falls back to default 900."""
    from bmad_orchestrator.agent.run import _review_timeout_sec

    monkeypatch.setenv("BMAD_REVIEW_TIMEOUT_SEC", "notanumber")
    assert _review_timeout_sec() == 900


# ---------------------------------------------------------------------------
# _real_security_review_runner watchdog integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_security_review_runner_without_bus_uses_plain_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without bus= argument the runner falls back to tail_jsonl_events (no watchdog)."""
    from bmad_orchestrator.agent.run import _real_security_review_runner
    from bmad_orchestrator.runtime.worker_spawn import WorkerHandle

    # Build a JSONL with a terminal worker_completed + approve verdict line.
    verdict_event = {
        "event_type": "stdout_line",
        "text": "VERDICT: approve",
        "ts": "2026-01-01T00:00:00+00:00",
    }
    done_event = {"event_type": "worker_completed", "exit_code": 0}
    jsonl_path = _make_jsonl_events([verdict_event, done_event], tmp_path)

    fake_handle = WorkerHandle(
        worktree=str(tmp_path),
        story_id="test-story",
        branch="feature/test-story",
        pid=9999,
        jsonl_path=jsonl_path,
        process=None,
        mock=True,
    )

    with patch(
        "bmad_orchestrator.agent.run._spawn_security_review_worker",
        AsyncMock(return_value=fake_handle),
    ):
        verdict, _findings = await _real_security_review_runner(
            tmp_path, "test-story", "w1"
            # bus NOT passed → plain tail_jsonl_events path
        )

    assert verdict == "approve"


@pytest.mark.asyncio
async def test_security_review_runner_with_bus_uses_watchdog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With bus= supplied, tail_with_stuck_watchdog is used (accepts bus param)."""
    from bmad_orchestrator.agent.run import _real_security_review_runner
    from bmad_orchestrator.runtime.worker_spawn import WorkerHandle

    # Build JSONL with approve verdict.
    verdict_event = {
        "event_type": "stdout_line",
        "text": "VERDICT: approve",
        "ts": "2026-01-01T00:00:00+00:00",
    }
    done_event = {"event_type": "worker_completed", "exit_code": 0}
    jsonl_path = _make_jsonl_events([verdict_event, done_event], tmp_path)

    fake_handle = WorkerHandle(
        worktree=str(tmp_path),
        story_id="test-story",
        branch="feature/test-story",
        pid=9999,
        jsonl_path=jsonl_path,
        process=None,
        mock=True,
    )

    bus = EventLoop()
    watchdog_calls: list[tuple[Any, ...]] = []

    async def _mock_watchdog(
        path: Path,
        _bus: EventLoop,
        *,
        story_id: str,
        worktree: str,
        commit_counter: Any,
        dirty_counter: Any,
        stuck_threshold_seconds: float,
        check_interval_seconds: float,
        **_kw: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        watchdog_calls.append((story_id, stuck_threshold_seconds, dirty_counter))
        # Replay the real events from the JSONL so the runner gets a verdict.
        for line in path.read_text().splitlines():
            if line.strip():
                yield json.loads(line)

    with patch(
        "bmad_orchestrator.agent.run._spawn_security_review_worker",
        AsyncMock(return_value=fake_handle),
    ), patch(
        "bmad_orchestrator.agent.run.tail_with_stuck_watchdog",
        _mock_watchdog,
    ):
        verdict, _ = await _real_security_review_runner(
            tmp_path, "test-story", "w1", bus=bus
        )

    assert verdict == "approve"
    assert len(watchdog_calls) == 1
    sid, threshold, dirty = watchdog_calls[0]
    assert sid == "test-story"
    # dirty_counter should be None (review workers don't write code)
    assert dirty is None
    # threshold should match _review_timeout_sec() (default 900)
    monkeypatch.delenv("BMAD_REVIEW_TIMEOUT_SEC", raising=False)
    from bmad_orchestrator.agent.run import _review_timeout_sec
    assert threshold == float(_review_timeout_sec())


# ---------------------------------------------------------------------------
# Merge-gate spec / quality stages — watchdog wired
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_merge_gate_spec_stage_uses_watchdog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_run_merge_gate_spec_stage replaces tail_jsonl_events with watchdog."""
    from bmad_orchestrator.agent.run import _run_merge_gate_spec_stage
    from bmad_orchestrator.runtime.worker_spawn import WorkerHandle

    # Build JSONL with an approve verdict.
    verdict_event = {
        "event_type": "stdout_line",
        "text": "VERDICT: approve",
        "ts": "2026-01-01T00:00:00+00:00",
    }
    done_event = {"event_type": "worker_completed", "exit_code": 0}
    jsonl_path = _make_jsonl_events([verdict_event, done_event], tmp_path)

    fake_handle = WorkerHandle(
        worktree=str(tmp_path),
        story_id="1-1",
        branch="feature/1-1",
        pid=9999,
        jsonl_path=jsonl_path,
        process=None,
        mock=True,
    )

    watchdog_calls: list[tuple[str, Any]] = []

    async def _mock_watchdog(
        path: Path,
        _bus: EventLoop,
        *,
        story_id: str,
        worktree: str,
        commit_counter: Any,
        dirty_counter: Any,
        stuck_threshold_seconds: float,
        check_interval_seconds: float,
        **_kw: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        watchdog_calls.append((story_id, dirty_counter))
        for line in path.read_text().splitlines():
            if line.strip():
                yield json.loads(line)

    bus = EventLoop()
    with patch(
        "bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker",
        AsyncMock(return_value=fake_handle),
    ), patch(
        "bmad_orchestrator.agent.run.tail_with_stuck_watchdog",
        _mock_watchdog,
    ):
        verdict, _summary, _metrics, _jsonl = await _run_merge_gate_spec_stage(
            worktree=str(tmp_path), story_id="1-1", wave="w1", bus=bus
        )

    assert verdict == "approve"
    assert len(watchdog_calls) == 1
    sid, dirty = watchdog_calls[0]
    assert sid == "1-1"
    assert dirty is None  # review workers never write code


@pytest.mark.asyncio
async def test_merge_gate_quality_stage_uses_watchdog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_run_merge_gate_quality_stage replaces tail_jsonl_events with watchdog."""
    from bmad_orchestrator.agent.run import _run_merge_gate_quality_stage
    from bmad_orchestrator.runtime.worker_spawn import WorkerHandle

    verdict_event = {
        "event_type": "stdout_line",
        "text": "VERDICT: approve",
        "ts": "2026-01-01T00:00:00+00:00",
    }
    done_event = {"event_type": "worker_completed", "exit_code": 0}
    jsonl_path = _make_jsonl_events([verdict_event, done_event], tmp_path)

    fake_handle = WorkerHandle(
        worktree=str(tmp_path),
        story_id="1-1",
        branch="feature/1-1",
        pid=9999,
        jsonl_path=jsonl_path,
        process=None,
        mock=True,
    )

    watchdog_calls: list[str] = []

    async def _mock_watchdog(
        path: Path,
        _bus: EventLoop,
        *,
        story_id: str,
        worktree: str,
        commit_counter: Any,
        dirty_counter: Any,
        **_kw: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        watchdog_calls.append(story_id)
        for line in path.read_text().splitlines():
            if line.strip():
                yield json.loads(line)

    bus = EventLoop()
    with patch(
        "bmad_orchestrator.agent.run._spawn_merge_gate_quality_worker",
        AsyncMock(return_value=fake_handle),
    ), patch(
        "bmad_orchestrator.agent.run.tail_with_stuck_watchdog",
        _mock_watchdog,
    ):
        verdict, _summary, _metrics, _jsonl = await _run_merge_gate_quality_stage(
            worktree=str(tmp_path), story_id="1-1", wave="w1", bus=bus
        )

    assert verdict == "approve"
    assert watchdog_calls == ["1-1"]


# ---------------------------------------------------------------------------
# Hard timeout path — watchdog trips on silent JSONL → verdict=error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_merge_gate_spec_stage_watchdog_trip_returns_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When watchdog trips (no events for threshold seconds), verdict → error."""
    from bmad_orchestrator.agent.run import _run_merge_gate_spec_stage
    from bmad_orchestrator.runtime.worker_spawn import WorkerHandle

    # Empty JSONL — watchdog will trip and emit a synthetic terminal event.
    jsonl_path = tmp_path / "events.jsonl"
    jsonl_path.write_text("", encoding="utf-8")

    fake_handle = WorkerHandle(
        worktree=str(tmp_path),
        story_id="stuck-1",
        branch="feature/stuck-1",
        pid=9999,
        jsonl_path=jsonl_path,
        process=None,
        mock=True,
    )

    # Mock watchdog to immediately emit a synthetic worker_completed (mimics trip).
    async def _tripped_watchdog(
        path: Path,
        _bus: EventLoop,
        *,
        story_id: str,
        **_kw: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        # Watchdog trip emits a synthetic terminal event.
        yield {"event_type": "worker_completed", "exit_code": 1, "status": "stuck_timeout"}

    bus = EventLoop()
    with patch(
        "bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker",
        AsyncMock(return_value=fake_handle),
    ), patch(
        "bmad_orchestrator.agent.run.tail_with_stuck_watchdog",
        _tripped_watchdog,
    ):
        verdict, _summary, _metrics, _jsonl = await _run_merge_gate_spec_stage(
            worktree=str(tmp_path), story_id="stuck-1", wave="w1", bus=bus
        )

    # No explicit verdict event was emitted → defaults to "error".
    assert verdict == "error"
