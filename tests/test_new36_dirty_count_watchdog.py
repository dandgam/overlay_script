"""NEW-36 — worktree dirty-count liveness signal + hard-timeout auto-stage recovery.

Tests:
* ``evaluate_stuck`` with worktree_growing reason.
* ``tail_with_stuck_watchdog`` with dirty_counter — growing dirty keeps worker alive.
* ``_auto_stage_worktree`` — mocked subprocess: stages + commits → returns SHA.
* ``_auto_stage_worktree`` — nothing to stage (clean worktree) → returns None.
* ``_worker_hard_timeout_sec`` — new env var BMAD_WORKER_HARD_TIMEOUT_SEC.
* WORKER_AUTO_STAGE_RECOVERY in EventType enum.
* _dirty_counter_for_handle wired into _tail_and_emit_completion source.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from bmad_orchestrator.runtime.event_loop import EventLoop, EventType
from bmad_orchestrator.runtime.stuck_watchdog import (
    evaluate_stuck,
    tail_with_stuck_watchdog,
)

# ── evaluate_stuck: worktree_growing ─────────────────────────────────────────


def test_evaluate_stuck_worktree_growing_not_stuck(tmp_path: Path) -> None:
    """Stale events + no commits + dirty count growing → worktree_growing (not stuck)."""
    p = tmp_path / "events.jsonl"
    stale = (datetime.now(UTC) - timedelta(hours=2)).isoformat(timespec="seconds")
    p.write_text(f'{{"event_type":"x","ts":"{stale}"}}\n', encoding="utf-8")
    r = evaluate_stuck(
        jsonl_path=p,
        start_time=datetime.now(UTC) - timedelta(hours=2),
        now=datetime.now(UTC),
        last_commit_count=0,
        current_commit_count=0,
        last_dirty_count=10,
        current_dirty_count=34,
        stuck_threshold_seconds=600,
    )
    assert r.stuck is False
    assert r.reason == "worktree_growing"
    assert r.dirty_count == 34


def test_evaluate_stuck_worktree_frozen_after_growth_is_stuck(tmp_path: Path) -> None:
    """Dirty count reached 34 but stopped growing → stuck (stale events, no commits)."""
    p = tmp_path / "events.jsonl"
    stale = (datetime.now(UTC) - timedelta(hours=2)).isoformat(timespec="seconds")
    p.write_text(f'{{"event_type":"x","ts":"{stale}"}}\n', encoding="utf-8")
    r = evaluate_stuck(
        jsonl_path=p,
        start_time=datetime.now(UTC) - timedelta(hours=2),
        now=datetime.now(UTC),
        last_commit_count=0,
        current_commit_count=0,
        last_dirty_count=34,
        current_dirty_count=34,  # frozen at peak
        stuck_threshold_seconds=600,
    )
    assert r.stuck is True


def test_evaluate_stuck_dirty_zero_both_no_false_positive(tmp_path: Path) -> None:
    """last=0 current=0 (no worktree / clean worktree) must NOT trigger worktree_growing."""
    p = tmp_path / "events.jsonl"
    stale = (datetime.now(UTC) - timedelta(hours=2)).isoformat(timespec="seconds")
    p.write_text(f'{{"event_type":"x","ts":"{stale}"}}\n', encoding="utf-8")
    r = evaluate_stuck(
        jsonl_path=p,
        start_time=datetime.now(UTC) - timedelta(hours=2),
        now=datetime.now(UTC),
        last_commit_count=0,
        current_commit_count=0,
        last_dirty_count=0,
        current_dirty_count=0,
        stuck_threshold_seconds=600,
    )
    assert r.stuck is True
    assert r.reason != "worktree_growing"


# ── tail_with_stuck_watchdog: dirty_counter integration ──────────────────────


async def _fake_inner_no_events(jsonl_path: Path):  # type: ignore[return]
    await asyncio.Event().wait()
    yield  # type: ignore[misc]


@pytest.mark.asyncio
async def test_watchdog_dirty_grows_prevents_trip(tmp_path: Path) -> None:
    """Growing dirty count keeps watchdog from tripping even with frozen events."""
    bus = EventLoop()
    jsonl = tmp_path / "events.jsonl"
    fake_now = [datetime.now(UTC)]
    dirty = [5]

    def now_factory() -> datetime:
        return fake_now[0]

    async def zero_commits() -> int:
        return 0

    async def growing_dirty() -> int:
        dirty[0] += 4
        return dirty[0]

    def inner_factory(_: Path):
        return _fake_inner_no_events(_)

    task = asyncio.create_task(
        _consume_watchdog(
            jsonl, bus, zero_commits, growing_dirty, now_factory, inner_factory
        )
    )
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


@pytest.mark.asyncio
async def test_watchdog_frozen_dirty_trips(tmp_path: Path) -> None:
    """Frozen dirty + frozen events + zero commits → watchdog trips."""
    bus = EventLoop()
    jsonl = tmp_path / "events.jsonl"
    fake_now = [datetime.now(UTC)]

    def now_factory() -> datetime:
        return fake_now[0]

    async def zero_commits() -> int:
        return 0

    async def frozen_dirty() -> int:
        return 7  # never changes

    def inner_factory(_: Path):
        return _fake_inner_no_events(_)

    yielded: list[dict[str, Any]] = []

    async def consume() -> None:
        async for ev in tail_with_stuck_watchdog(
            jsonl,
            bus,
            story_id="s-x",
            worktree="/tmp/wt-x",
            commit_counter=zero_commits,
            dirty_counter=frozen_dirty,
            stuck_threshold_seconds=10,
            check_interval_seconds=0.1,
            now_factory=now_factory,
            inner_factory=inner_factory,
        ):
            yielded.append(ev)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.15)
    fake_now[0] = datetime.now(UTC) + timedelta(seconds=20)
    await asyncio.wait_for(task, timeout=2.0)

    assert any(e["event_type"] == "worker_completed" for e in yielded)
    types: set[EventType] = set()
    while not bus.queue.empty():
        types.add(bus.queue.get_nowait().type)
    assert EventType.WORKER_STUCK_TIMEOUT in types


async def _consume_watchdog(
    jsonl: Path,
    bus: EventLoop,
    commit_counter,
    dirty_counter,
    now_factory,
    inner_factory,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    async for ev in tail_with_stuck_watchdog(
        jsonl,
        bus,
        story_id="s-test",
        worktree="/tmp/wt-test",
        commit_counter=commit_counter,
        dirty_counter=dirty_counter,
        stuck_threshold_seconds=10,
        check_interval_seconds=0.1,
        now_factory=now_factory,
        inner_factory=inner_factory,
    ):
        out.append(ev)
    return out


# ── _auto_stage_worktree ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auto_stage_worktree_dirty_commits_and_returns_sha(tmp_path: Path) -> None:
    """_auto_stage_worktree stages + commits dirty files; returns SHA."""
    import git as gitlib

    repo = gitlib.Repo.init(str(tmp_path))
    repo.config_writer().set_value("user", "name", "Test").release()
    repo.config_writer().set_value("user", "email", "t@t.com").release()
    # Initial commit so HEAD exists.
    (tmp_path / "README").write_text("init", encoding="utf-8")
    repo.index.add(["README"])
    repo.index.commit("init")
    # Create a dirty file.
    (tmp_path / "new_file.py").write_text("x = 1", encoding="utf-8")

    from bmad_orchestrator.runtime.worker_spawn import _auto_stage_worktree

    sha = await _auto_stage_worktree(str(tmp_path), "test-story")
    assert sha is not None
    assert len(sha) == 40  # full SHA
    # Verify the file is committed (name attr on tree blobs).
    head_files = [item.name for item in repo.head.commit.tree.traverse()]
    assert "new_file.py" in head_files


@pytest.mark.asyncio
async def test_auto_stage_worktree_clean_returns_none(tmp_path: Path) -> None:
    """_auto_stage_worktree with a clean worktree returns None (nothing to stage)."""
    import git as gitlib

    repo = gitlib.Repo.init(str(tmp_path))
    repo.config_writer().set_value("user", "name", "Test").release()
    repo.config_writer().set_value("user", "email", "t@t.com").release()
    (tmp_path / "README").write_text("init", encoding="utf-8")
    repo.index.add(["README"])
    repo.index.commit("init")
    # Nothing dirty.

    from bmad_orchestrator.runtime.worker_spawn import _auto_stage_worktree

    sha = await _auto_stage_worktree(str(tmp_path), "test-story")
    assert sha is None


# ── _worker_hard_timeout_sec env vars ─────────────────────────────────────────


def test_worker_hard_timeout_sec_default_is_14400(monkeypatch: pytest.MonkeyPatch) -> None:
    """NEW-36: hard-ceiling default is 4 h (14400 s); stuck_watchdog owns 30-min soft."""
    monkeypatch.delenv("BMAD_WORKER_TIMEOUT_SEC", raising=False)
    monkeypatch.delenv("BMAD_WORKER_HARD_TIMEOUT_SEC", raising=False)
    from bmad_orchestrator.runtime.worker_spawn import _worker_hard_timeout_sec

    assert _worker_hard_timeout_sec() == 14400


def test_worker_hard_timeout_sec_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """BMAD_WORKER_HARD_TIMEOUT_SEC env var overrides built-in default."""
    monkeypatch.delenv("BMAD_WORKER_TIMEOUT_SEC", raising=False)
    monkeypatch.setenv("BMAD_WORKER_HARD_TIMEOUT_SEC", "7200")
    from bmad_orchestrator.runtime.worker_spawn import _worker_hard_timeout_sec

    assert _worker_hard_timeout_sec() == 7200


def test_worker_hard_timeout_sec_legacy_override_takes_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BMAD_WORKER_TIMEOUT_SEC overrides BMAD_WORKER_HARD_TIMEOUT_SEC (compat)."""
    monkeypatch.setenv("BMAD_WORKER_TIMEOUT_SEC", "3600")
    monkeypatch.setenv("BMAD_WORKER_HARD_TIMEOUT_SEC", "7200")
    from bmad_orchestrator.runtime.worker_spawn import _worker_hard_timeout_sec

    assert _worker_hard_timeout_sec() == 3600


# ── EventType.WORKER_AUTO_STAGE_RECOVERY in enum ────────────────────────────


def test_worker_auto_stage_recovery_event_type_exists() -> None:
    """NEW-36: WORKER_AUTO_STAGE_RECOVERY must be in EventType."""
    assert hasattr(EventType, "WORKER_AUTO_STAGE_RECOVERY")
    assert EventType.WORKER_AUTO_STAGE_RECOVERY == "worker_auto_stage_recovery"


# ── _dirty_counter_for_handle wired in _tail_and_emit_completion ─────────────


def test_dirty_counter_wired_in_tail_and_emit_completion() -> None:
    """NEW-36: _dirty_counter_for_handle must be passed to tail_with_stuck_watchdog."""
    from bmad_orchestrator.agent.run import _tail_and_emit_completion

    src = inspect.getsource(_tail_and_emit_completion)
    assert "_dirty_counter_for_handle" in src
    assert "dirty_counter=_dirty_counter_for_handle" in src
