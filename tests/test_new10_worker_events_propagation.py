"""#10 NEW-10 (spec_pilot_findings_closure_v4 §2) — worker events propagation.

Pilot run #3 left the main ``_bmad-output/runs/default/wt-<id>.events.jsonl``
with a stale mtime: worker-side events written inside the worktree copy never
reached the orchestrator-side runs directory, so the run was undiagnosable and
the orchestrator log was silent for 30 minutes.

The fix (:mod:`runtime.worker_events`): after ``worker_completed``,
``_tail_and_emit_completion`` merges the worktree-internal events file into the
orchestrator-side events file, deduplicating already-present events.

Coverage:
  * 3 unit  — :func:`merge_worktree_events` append semantics, ts dedup,
    missing source file.
  * 2 unit  — path resolution for the worktree-internal vs main events files.
  * 2 integration — ``_tail_and_emit_completion`` propagates worktree events
    into the main file; a missing worktree file is handled gracefully.

Spec target: +7 tests.
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
from bmad_orchestrator.agent.tools._common import worker_jsonl_path
from bmad_orchestrator.runtime.event_loop import EventLoop
from bmad_orchestrator.runtime.worker_events import (
    main_events_path,
    merge_worktree_events,
    worktree_events_path,
)
from bmad_orchestrator.runtime.worker_spawn import WorkerHandle

# ── unit — merge_worktree_events ────────────────────────────────────────────


def _write_jsonl(path: Path, events: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(ev) for ev in events) + "\n", encoding="utf-8"
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(ln)
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]


def test_merge_appends_new_events(tmp_path: Path) -> None:
    """Events from the worktree file are appended onto the main file."""
    src = tmp_path / "wt.events.jsonl"
    dest = tmp_path / "main.events.jsonl"
    _write_jsonl(dest, [{"ts": "t0", "event_type": "worker_spawned"}])
    _write_jsonl(
        src,
        [
            {"ts": "t1", "event_type": "stage_started"},
            {"ts": "t2", "event_type": "review_done"},
        ],
    )

    appended = merge_worktree_events(src, dest)

    assert appended == 2
    types = [e["event_type"] for e in _read_jsonl(dest)]
    assert types == ["worker_spawned", "stage_started", "review_done"]


def test_merge_dedups_by_ts(tmp_path: Path) -> None:
    """An event already present in the main file (same ts) is not duplicated."""
    src = tmp_path / "wt.events.jsonl"
    dest = tmp_path / "main.events.jsonl"
    shared = {"ts": "t1", "event_type": "stage_started", "story_id": "1.5"}
    _write_jsonl(dest, [shared])
    _write_jsonl(src, [shared, {"ts": "t2", "event_type": "review_done"}])

    appended = merge_worktree_events(src, dest)

    assert appended == 1
    events = _read_jsonl(dest)
    assert len(events) == 2
    assert [e["ts"] for e in events] == ["t1", "t2"]


def test_merge_missing_source_is_noop(tmp_path: Path) -> None:
    """A missing worktree-internal file is not an error — returns 0."""
    src = tmp_path / "absent.events.jsonl"
    dest = tmp_path / "main.events.jsonl"
    _write_jsonl(dest, [{"ts": "t0", "event_type": "worker_spawned"}])

    appended = merge_worktree_events(src, dest)

    assert appended == 0
    assert len(_read_jsonl(dest)) == 1


# ── unit — path resolution ──────────────────────────────────────────────────


def test_worktree_events_path_is_worktree_internal(tmp_path: Path) -> None:
    """The worktree-internal events file lives under the worktree itself."""
    wt = tmp_path / "wt-1.5"
    path = worktree_events_path(wt, wave="default")
    assert path == wt / "_bmad-output" / "runs" / "default" / "wt-1.5.events.jsonl"


def test_main_events_path_matches_worker_jsonl_path(tmp_path: Path) -> None:
    """The main events file resolution matches the orchestrator's tailer path."""
    wt = tmp_path / "wt-2.1"
    assert main_events_path(wt) == worker_jsonl_path(str(wt))


# ── integration — _tail_and_emit_completion propagates worktree events ──────


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True, capture_output=True, text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        },
    ).stdout


def _repo_with_commit(repo: Path) -> str:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("hi\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "init")
    base = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "work.txt").write_text("story work\n")
    _git(repo, "add", "work.txt")
    _git(repo, "commit", "-q", "-m", "story work")
    return base


def _handle(worktree: Path, jsonl: Path, base_sha: str) -> WorkerHandle:
    return WorkerHandle(
        worktree=str(worktree),
        story_id="1.5",
        branch="feature/1.5",
        pid=0,
        jsonl_path=jsonl,
        process=None,
        mock=False,
        sandbox_kind="bwrap",
        base_sha=base_sha,
    )


async def _run_tail(handle: WorkerHandle) -> None:
    bus = EventLoop()
    await _tail_and_emit_completion(handle, bus)
    while await bus.dispatch_one(timeout=0.01) is not None:
        pass
    await bus.stop()


@pytest.mark.asyncio
async def test_tail_propagates_worktree_events_to_main(tmp_path: Path) -> None:
    """worker_completed → worktree-internal events land in the main file."""
    wt = tmp_path / "wt-1.5"
    base = _repo_with_commit(wt)

    main = tmp_path / "main.events.jsonl"
    _write_jsonl(main, [{"ts": "t9", "event_type": "worker_completed", "exit_code": 0}])

    wt_events = worktree_events_path(wt)
    _write_jsonl(
        wt_events,
        [
            {"ts": "t1", "event_type": "stage_started", "stage": "4"},
            {"ts": "t2", "event_type": "code_review_verdict", "verdict": "approve"},
        ],
    )

    await _run_tail(_handle(wt, main, base))

    types = {e["event_type"] for e in _read_jsonl(main)}
    assert "stage_started" in types
    assert "code_review_verdict" in types


@pytest.mark.asyncio
async def test_tail_missing_worktree_events_is_graceful(tmp_path: Path) -> None:
    """No worktree-internal file → tail completes, main file left intact."""
    wt = tmp_path / "wt-1.5"
    base = _repo_with_commit(wt)

    main = tmp_path / "main.events.jsonl"
    _write_jsonl(main, [{"ts": "t9", "event_type": "worker_completed", "exit_code": 0}])

    await _run_tail(_handle(wt, main, base))

    # The worktree-internal file never existed — main keeps its own events.
    assert any(
        e["event_type"] == "worker_completed" for e in _read_jsonl(main)
    )
