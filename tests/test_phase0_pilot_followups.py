"""Phase 0 — pilot followup tests (spec_parallelism_initiatives §Phase 0).

Coverage:
  * Task 0.1 — ``_kill_stale_orchestrators`` excludes self/PPID; ``_kill_orphan_workers``
    SIGKILLs live worker processes.
  * Task 0.2 — ``_tail_and_emit_completion`` emits ``WORKER_SILENT_FAILURE`` +
    ``WORKER_HALT_FILE`` (and NOT ``WORKER_COMPLETED``) when exit=0 but zero
    new commits landed on the worker branch.
  * Task 0.3 — ``_is_subscription_mode`` env permutations; terminal events emit
    ``cost_tracking_unavailable`` event + log when no usage block ever fed
    into the tracker in subscription mode.
  * Task 0.4 — ``_warn_if_worktree_dirty`` returns porcelain lines + warns
    when a worktree has untracked/modified files.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from bmad_orchestrator.agent.run import (
    _count_new_commits,
    _emit_cost_tracking_unavailable,
    _is_subscription_mode,
    _kill_orphan_workers,
    _kill_stale_orchestrators,
    _resolve_worktree_head,
    _resolve_worktree_reuse_base_sha,
    _tail_and_emit_completion,
    _tracker_has_no_usage,
    _warn_if_worktree_dirty,
)
from bmad_orchestrator.runtime.cost_tracker import WorkerCostTracker
from bmad_orchestrator.runtime.event_loop import EventLoop, EventType
from bmad_orchestrator.runtime.worker_spawn import WorkerHandle

SONNET = "claude-sonnet-4-6"


# ── helpers ──────────────────────────────────────────────────────────────────


def _write_jsonl(path: Path, events: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(ev) for ev in events) + "\n", encoding="utf-8"
    )


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        },
    )


def _init_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    (path / "README.md").write_text("hi\n")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "init")
    head = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return head


# ── Task 0.1 — zombie cleanup ────────────────────────────────────────────────


def test_phase0_kill_stale_no_matching_processes_returns_zero() -> None:
    """pgrep returns nothing → 0 killed; no crash."""
    killed = _kill_stale_orchestrators(project="this-project-does-not-exist-zzz-1234")
    assert killed == 0


def test_phase0_kill_stale_excludes_self_pid() -> None:
    """Even if pgrep matched self, the self-PID filter prevents suicide."""
    import unittest.mock as mock

    my_pid = os.getpid()
    parent_pid = os.getppid()
    fake_proc = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=f"{my_pid}\n{parent_pid}\n", stderr=""
    )
    killed_pids: list[int] = []

    def fake_kill(pid: int, sig: int) -> None:
        killed_pids.append(pid)

    with mock.patch("bmad_orchestrator.agent.run.subprocess.run", return_value=fake_proc), \
            mock.patch("bmad_orchestrator.agent.run.os.kill", side_effect=fake_kill):
        result = _kill_stale_orchestrators(project="antares")

    assert result == 0
    assert killed_pids == []


def test_phase0_kill_stale_kills_unrelated_pid() -> None:
    """A matched PID that is not self/PPID does get SIGKILL'd."""
    import unittest.mock as mock

    my_pid = os.getpid()
    other_pid = my_pid + 1  # fake pid; we mock os.kill so it never reaches the OS
    fake_proc = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=f"{other_pid}\n", stderr=""
    )
    sent: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int) -> None:
        sent.append((pid, sig))

    # H-3 fix: _cmdline_matches_project guards the SIGKILL by reading
    # /proc/<pid>/cmdline. A synthetic pid has no proc entry, so we mock
    # the helper to assert the kill loop fires when cmdline match succeeds.
    with mock.patch("bmad_orchestrator.agent.run.subprocess.run", return_value=fake_proc), \
            mock.patch("bmad_orchestrator.agent.run.os.kill", side_effect=fake_kill), \
            mock.patch("bmad_orchestrator.agent.run._cmdline_matches_project", return_value=True):
        result = _kill_stale_orchestrators(project="antares")

    assert result == 1
    assert sent == [(other_pid, 9)]  # SIGKILL


def test_phase0_kill_stale_pgrep_missing_returns_zero() -> None:
    """If pgrep binary is absent, return 0 silently — not a hard error."""
    import unittest.mock as mock

    with mock.patch(
        "bmad_orchestrator.agent.run.subprocess.run",
        side_effect=FileNotFoundError("no pgrep"),
    ):
        result = _kill_stale_orchestrators(project="antares")
    assert result == 0


@pytest.mark.asyncio
async def test_phase0_kill_orphan_workers_kills_live_subprocess(
    tmp_path: Path,
) -> None:
    """A handle whose process is still alive gets ``process.kill()``."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(30)",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        handle = WorkerHandle(
            worktree=str(tmp_path),
            story_id="s1",
            branch="feature/s1",
            pid=proc.pid,
            jsonl_path=tmp_path / "s1.jsonl",
            process=proc,
            mock=False,
            sandbox_kind="bwrap",
        )
        _kill_orphan_workers([handle])
        await asyncio.wait_for(proc.wait(), timeout=5.0)
        assert proc.returncode is not None
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


def test_phase0_kill_orphan_workers_skips_finished() -> None:
    """A handle whose ``process.returncode`` is set is a no-op."""

    class _DoneProc:
        returncode = 0

        def kill(self) -> None:  # pragma: no cover — must not be called
            raise AssertionError("should not kill finished proc")

    handle = WorkerHandle(
        worktree="/tmp/x",
        story_id="s1",
        branch="feature/s1",
        pid=0,
        jsonl_path=Path("/tmp/x/s1.jsonl"),
        process=_DoneProc(),  # type: ignore[arg-type]
        mock=False,
        sandbox_kind="bwrap",
    )
    _kill_orphan_workers([handle])  # must not raise


# ── Task 0.2 — silent-failure detection ──────────────────────────────────────


@pytest.mark.asyncio
async def test_phase0_count_new_commits_returns_zero_when_no_commits(
    tmp_path: Path,
) -> None:
    base = _init_repo(tmp_path)
    assert await _count_new_commits(str(tmp_path), base) == 0


@pytest.mark.asyncio
async def test_phase0_count_new_commits_counts_added_commits(
    tmp_path: Path,
) -> None:
    base = _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("a\n")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-q", "-m", "add a")
    (tmp_path / "b.txt").write_text("b\n")
    _git(tmp_path, "add", "b.txt")
    _git(tmp_path, "commit", "-q", "-m", "add b")
    assert await _count_new_commits(str(tmp_path), base) == 2


@pytest.mark.asyncio
async def test_phase0_count_new_commits_empty_base_sha_returns_zero(
    tmp_path: Path,
) -> None:
    assert await _count_new_commits(str(tmp_path), "") == 0


@pytest.mark.asyncio
async def test_phase0_tail_emits_silent_failure_when_no_commits(
    tmp_path: Path,
) -> None:
    """exit=0 + zero commits on branch ⇒ WORKER_SILENT_FAILURE + WORKER_HALT_FILE,
    NOT WORKER_COMPLETED.
    """
    base = _init_repo(tmp_path)
    jsonl_path = tmp_path / "events.jsonl"
    _write_jsonl(
        jsonl_path,
        [
            {
                "event_type": "worker_completed",
                "story_id": "s1",
                "exit_code": 0,
                "status": "success",
            }
        ],
    )
    handle = WorkerHandle(
        worktree=str(tmp_path),
        story_id="s1",
        branch="feature/s1",
        pid=0,
        jsonl_path=jsonl_path,
        process=None,
        mock=False,
        sandbox_kind="bwrap",
        base_sha=base,
    )
    bus = EventLoop()
    captured: list[Any] = []

    async def _capture(ev: Any) -> None:
        captured.append(ev)

    bus.on(_capture)
    await _tail_and_emit_completion(handle, bus)
    while await bus.dispatch_one(timeout=0.01) is not None:
        pass
    await bus.stop()

    types = [ev.type for ev in captured]
    assert EventType.WORKER_SILENT_FAILURE in types
    assert EventType.WORKER_HALT_FILE in types
    assert EventType.WORKER_COMPLETED not in types


@pytest.mark.asyncio
async def test_phase0_tail_emits_completed_when_commits_present(
    tmp_path: Path,
) -> None:
    """exit=0 + ≥1 new commit on branch ⇒ normal WORKER_COMPLETED path."""
    base = _init_repo(tmp_path)
    (tmp_path / "story-output.txt").write_text("worked\n")
    _git(tmp_path, "add", "story-output.txt")
    _git(tmp_path, "commit", "-q", "-m", "worker output")

    jsonl_path = tmp_path / "events.jsonl"
    _write_jsonl(
        jsonl_path,
        [
            {
                "event_type": "worker_completed",
                "story_id": "s1",
                "exit_code": 0,
                "status": "success",
            }
        ],
    )
    handle = WorkerHandle(
        worktree=str(tmp_path),
        story_id="s1",
        branch="feature/s1",
        pid=0,
        jsonl_path=jsonl_path,
        process=None,
        mock=False,
        sandbox_kind="bwrap",
        base_sha=base,
    )
    bus = EventLoop()
    captured: list[Any] = []

    async def _capture(ev: Any) -> None:
        captured.append(ev)

    bus.on(_capture)
    await _tail_and_emit_completion(handle, bus)
    while await bus.dispatch_one(timeout=0.01) is not None:
        pass
    await bus.stop()

    types = [ev.type for ev in captured]
    assert EventType.WORKER_COMPLETED in types
    assert EventType.WORKER_SILENT_FAILURE not in types


@pytest.mark.asyncio
async def test_phase0_tail_skips_silent_failure_when_no_base_sha(
    tmp_path: Path,
) -> None:
    """Legacy handles without base_sha bypass the silent-failure check."""
    jsonl_path = tmp_path / "events.jsonl"
    _write_jsonl(
        jsonl_path,
        [
            {
                "event_type": "worker_completed",
                "story_id": "s1",
                "exit_code": 0,
                "status": "success",
            }
        ],
    )
    handle = WorkerHandle(
        worktree=str(tmp_path),
        story_id="s1",
        branch="feature/s1",
        pid=0,
        jsonl_path=jsonl_path,
        process=None,
        mock=False,
        sandbox_kind="bwrap",
        base_sha=None,
    )
    bus = EventLoop()
    captured: list[Any] = []

    async def _capture(ev: Any) -> None:
        captured.append(ev)

    bus.on(_capture)
    await _tail_and_emit_completion(handle, bus)
    while await bus.dispatch_one(timeout=0.01) is not None:
        pass
    await bus.stop()

    assert EventType.WORKER_COMPLETED in [ev.type for ev in captured]


# ── Task 0.3 — cost honesty / subscription mode ──────────────────────────────


def test_phase0_is_subscription_mode_no_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("BMAD_DISABLE_BUDGET", raising=False)
    assert _is_subscription_mode() is True


def test_phase0_is_subscription_mode_disable_budget_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("BMAD_DISABLE_BUDGET", "1")
    assert _is_subscription_mode() is True


def test_phase0_is_subscription_mode_api_key_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("BMAD_DISABLE_BUDGET", raising=False)
    assert _is_subscription_mode() is False


def test_phase0_tracker_has_no_usage_initial() -> None:
    tracker = WorkerCostTracker(model=SONNET)
    assert _tracker_has_no_usage(tracker) is True


def test_phase0_tracker_has_no_usage_after_feed() -> None:
    tracker = WorkerCostTracker(model=SONNET)
    tracker.feed(
        {
            "event_type": "claude_event",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        }
    )
    assert _tracker_has_no_usage(tracker) is False


@pytest.mark.asyncio
async def test_phase0_emit_cost_tracking_unavailable_emits_event_and_log(
    capfd: pytest.CaptureFixture[str],
) -> None:
    bus = EventLoop()
    captured: list[Any] = []

    async def _capture(ev: Any) -> None:
        captured.append(ev)

    bus.on(_capture)
    await _emit_cost_tracking_unavailable(bus, "s1", reason="subscription_mode")
    while await bus.dispatch_one(timeout=0.01) is not None:
        pass
    await bus.stop()

    out = capfd.readouterr()
    combined = out.out + out.err
    assert "cost_tracking_unavailable" in combined
    assert any(ev.type == EventType.COST_TRACKING_UNAVAILABLE for ev in captured)


@pytest.mark.asyncio
async def test_phase0_tail_subscription_mode_emits_cost_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Subscription mode + zero usage ⇒ ``cost_tracking_unavailable`` event,
    NOT ``worker_cost_final`` log.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("BMAD_DISABLE_BUDGET", raising=False)
    from bmad_orchestrator.agent.safety.budget_guard import BudgetGuard
    from bmad_orchestrator.config import BudgetConfig

    cfg = BudgetConfig(
        story_alarm_usd=30.0,
        story_halt_usd=60.0,
        batch_alarm_usd=200.0,
        batch_halt_usd=300.0,
        daily_limit_usd=500.0,
    )
    budget = BudgetGuard(cfg)

    base = _init_repo(tmp_path)
    (tmp_path / "out.txt").write_text("x\n")
    _git(tmp_path, "add", "out.txt")
    _git(tmp_path, "commit", "-q", "-m", "out")

    jsonl_path = tmp_path / "events.jsonl"
    _write_jsonl(
        jsonl_path,
        [
            {
                "event_type": "worker_completed",
                "story_id": "s1",
                "exit_code": 0,
                "status": "success",
            }
        ],
    )
    handle = WorkerHandle(
        worktree=str(tmp_path),
        story_id="s1",
        branch="feature/s1",
        pid=0,
        jsonl_path=jsonl_path,
        process=None,
        mock=False,
        sandbox_kind="bwrap",
        base_sha=base,
    )
    bus = EventLoop()
    captured: list[Any] = []

    async def _capture(ev: Any) -> None:
        captured.append(ev)

    bus.on(_capture)
    await _tail_and_emit_completion(handle, bus, budget=budget, model=SONNET)
    while await bus.dispatch_one(timeout=0.01) is not None:
        pass
    await bus.stop()

    out = capfd.readouterr()
    combined = out.out + out.err
    assert "cost_tracking_unavailable" in combined
    assert "worker_cost_final" not in combined
    assert any(
        ev.type == EventType.COST_TRACKING_UNAVAILABLE for ev in captured
    )


# ── Task 0.4 — pre-spawn worktree freshness ──────────────────────────────────


@pytest.mark.asyncio
async def test_phase0_warn_if_worktree_dirty_clean_repo_returns_empty(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    assert await _warn_if_worktree_dirty(tmp_path) == []


@pytest.mark.asyncio
async def test_phase0_warn_if_worktree_dirty_detects_untracked(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    _init_repo(tmp_path)
    (tmp_path / "stray.txt").write_text("residue\n")
    lines = await _warn_if_worktree_dirty(tmp_path)
    assert any("stray.txt" in line for line in lines)
    out = capfd.readouterr()
    assert "worktree_dirty_pre_spawn" in (out.out + out.err)


@pytest.mark.asyncio
async def test_phase0_resolve_worktree_head_returns_sha(
    tmp_path: Path,
) -> None:
    expected = _init_repo(tmp_path)
    assert await _resolve_worktree_head(tmp_path) == expected


# ── NEW-35 — reuse base_sha via merge-base ───────────────────────────────────


def _setup_feature_branch(tmp_path: Path) -> tuple[Path, Path, str]:
    """Create a target repo + feature worktree that mirrors the pilot 2d scenario.

    Returns (target_project, worktree_path, merge_base_sha).
    """
    target = tmp_path / "target"
    target.mkdir()
    _git(target, "init", "-q", "-b", "master")
    (target / "README.md").write_text("init\n")
    _git(target, "add", "README.md")
    _git(target, "commit", "-q", "-m", "initial commit")
    merge_base_sha = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    # Create feature branch from master (go back to master first so worktree
    # add can check it out in a separate directory).
    _git(target, "branch", "feature/1.4")

    # Simulate the worktree (git worktree add checks out the branch there)
    wt = tmp_path / "wt-1.4"
    subprocess.run(
        ["git", "-C", str(target), "worktree", "add", str(wt), "feature/1.4"],
        check=True, capture_output=True,
    )

    # Add 2 commits on the feature branch inside the worktree
    (wt / "feat.py").write_text("# feat\n")
    _git(wt, "add", "feat.py")
    _git(wt, "commit", "-q", "-m", "feat(1.4): implement story")
    (wt / "feat.py").write_text("# feat autofix\n")
    _git(wt, "add", "feat.py")
    _git(wt, "commit", "-q", "-m", "fix(story-1.4): audit autofix")

    # Return master HEAD (= merge-base) as the expected base_sha
    return target, wt, merge_base_sha


@pytest.mark.asyncio
async def test_new35_reuse_base_sha_returns_merge_base(tmp_path: Path) -> None:
    """NEW-35: reused worktree base_sha must be merge-base(feature, upstream),
    not the feature branch HEAD — so base_sha..HEAD > 0 for prior-pilot work.
    """
    target, wt, expected_base = _setup_feature_branch(tmp_path)
    result = await _resolve_worktree_reuse_base_sha(target, wt, "feature/1.4")
    assert result == expected_base, (
        f"Expected merge-base {expected_base!r}, got {result!r}. "
        "Returning feature HEAD would make commit-count 0 → silent_failure."
    )


@pytest.mark.asyncio
async def test_new35_reuse_base_sha_commit_count_nonzero(tmp_path: Path) -> None:
    """NEW-35: using merge-base as base_sha yields positive commit count for
    a reused worktree with prior-pilot work — no false silent_failure.
    """
    target, wt, _ = _setup_feature_branch(tmp_path)
    base = await _resolve_worktree_reuse_base_sha(target, wt, "feature/1.4")
    count = await _count_new_commits(str(wt), base)
    assert count >= 2, (
        f"Expected ≥2 new commits past merge-base, got {count}. "
        "Silent failure guard (count==0) would have fired incorrectly."
    )
