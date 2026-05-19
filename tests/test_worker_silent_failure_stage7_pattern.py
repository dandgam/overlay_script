"""#2 NEW-2 Layer B — orchestrator-side reused-worktree detector (2026-05-19).

Covers :mod:`bmad_orchestrator.runtime.worker_silent_failure` (pure helpers) and
its wiring into :func:`bmad_orchestrator.agent.run._tail_and_emit_completion`:

  1. regex match — the git refusal line is recognised, branch name extracted;
  2. recovery decision — commits>0 ⇒ recover, commits==0 ⇒ preserve halt;
  3. no-commits halt path — detector fires but the orchestrator still halts;
  4. event-bus integration — commits>0 ⇒ synthetic approve verdict on the bus.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from bmad_orchestrator.agent.run import _tail_and_emit_completion
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.runtime.worker_silent_failure import (
    decide_cleanup_recovery,
    detect_reused_worktree_cleanup_failure,
    is_reused_worktree_cleanup_line,
)
from bmad_orchestrator.runtime.worker_spawn import WorkerHandle

_GIT_ERR = (
    "error: cannot delete branch 'feature/1.3' used by worktree "
    "at '/home/server/Antares/.worktrees/wt-1.3'"
)


# ── 1. regex match ──────────────────────────────────────────────────────────


def test_regex_extracts_branch_from_git_refusal() -> None:
    assert is_reused_worktree_cleanup_line(_GIT_ERR) is True
    branch = detect_reused_worktree_cleanup_failure(
        ["Stage 6.pass — merge", _GIT_ERR, "trailing noise"]
    )
    assert branch == "feature/1.3"


def test_regex_no_match_on_clean_output() -> None:
    assert (
        detect_reused_worktree_cleanup_failure(
            ["Stage 6.pass — merge", "stage7 feature branch deleted", ""]
        )
        is None
    )
    # non-str entries are tolerated and skipped.
    assert detect_reused_worktree_cleanup_failure([None, 42, "ok"]) is None  # type: ignore[list-item]


# ── 2. recovery decision ────────────────────────────────────────────────────


def test_decide_cleanup_recovery_branches() -> None:
    recover = decide_cleanup_recovery(3)
    assert recover.recover is True
    assert recover.commits == 3
    assert recover.reason == "runner_cleanup_recovery"

    halt = decide_cleanup_recovery(0)
    assert halt.recover is False
    assert halt.commits == 0
    assert halt.reason == "no_commits_preserve_halt"
    # negative / junk counts clamp to the halt decision.
    assert decide_cleanup_recovery(-5).recover is False


# ── test helpers for the bus-integration cases ──────────────────────────────


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
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _make_handle(tmp_path: Path, jsonl: Path, *, base_sha: str | None) -> WorkerHandle:
    return WorkerHandle(
        worktree=str(tmp_path),
        story_id="1.3",
        branch="feature/1.3",
        pid=0,
        jsonl_path=jsonl,
        process=None,
        mock=False,
        sandbox_kind="bwrap",
        base_sha=base_sha,
    )


async def _collect(bus: EventLoop) -> list[Event]:
    seen: list[Event] = []
    while (ev := await bus.dispatch_one(timeout=0.01)) is not None:
        seen.append(ev)
    await bus.stop()
    return seen


# ── 3. no-commits halt path ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_commits_preserves_halt(tmp_path: Path) -> None:
    """Detector fires, but with zero commits the orchestrator still halts."""
    base = _init_repo(tmp_path)  # base == HEAD ⇒ no commits past base
    jsonl = tmp_path / "events.jsonl"
    _write_jsonl(
        jsonl,
        [
            {"event_type": "stdout_line", "text": _GIT_ERR},
            {"event_type": "worker_completed", "exit_code": 0, "status": "success"},
        ],
    )
    handle = _make_handle(tmp_path, jsonl, base_sha=base)
    bus = EventLoop()
    outcome = await _tail_and_emit_completion(handle, bus)
    events = await _collect(bus)
    assert outcome == "silent_failure"
    types = [e.type for e in events]
    # the cleanup-failure event is still surfaced for observability...
    assert EventType.RUNNER_CLEANUP_FAILED_REUSED_WORKTREE in types
    # ...but no synthetic approve verdict is emitted.
    assert EventType.CODE_REVIEW_VERDICT not in types
    assert EventType.WORKER_HALT_FILE in types


# ── 4. event-bus integration ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_commits_present_emits_recovery_verdict(tmp_path: Path) -> None:
    base = _init_repo(tmp_path)
    (tmp_path / "work.txt").write_text("recovered story work\n")
    _git(tmp_path, "add", "work.txt")
    _git(tmp_path, "commit", "-q", "-m", "story 1.3 work")
    jsonl = tmp_path / "events.jsonl"
    _write_jsonl(
        jsonl,
        [
            {"event_type": "stdout_line", "text": "Stage 6.pass — merge"},
            {"event_type": "stdout_line", "text": _GIT_ERR},
            {"event_type": "worker_completed", "exit_code": 0, "status": "success"},
        ],
    )
    handle = _make_handle(tmp_path, jsonl, base_sha=base)
    bus = EventLoop()
    outcome = await _tail_and_emit_completion(handle, bus)
    events = await _collect(bus)
    assert outcome == "completed"

    cleanup = [e for e in events if e.type == EventType.RUNNER_CLEANUP_FAILED_REUSED_WORKTREE]
    assert len(cleanup) == 1
    assert cleanup[0].payload["branch"] == "feature/1.3"
    assert cleanup[0].payload["commits"] == 1

    verdicts = [e for e in events if e.type == EventType.CODE_REVIEW_VERDICT]
    assert len(verdicts) == 1
    assert verdicts[0].payload["verdict"] == "approve"
    assert verdicts[0].payload["source"] == "runner_cleanup_recovery"
    assert verdicts[0].payload["commits"] == 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
