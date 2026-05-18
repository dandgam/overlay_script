"""Pilot findings closure §3 #9 — real_pilot_done counter split (2026-05-19).

Verifies :func:`bmad_orchestrator.agent.run._tail_and_emit_completion` returns
an outcome tag that callers (``_run_real_pilot_body``) bucket into
``succeeded`` / ``failed`` for the post-pilot ``spawned=X succeeded=Y
failed=Z`` log line.

Outcome tags:
  * ``"completed"`` — worker_completed event with exit_code 0 (+ commits when
    base_sha provided).
  * ``"failed"``     — worker_completed event with non-zero exit_code.
  * ``"halted"``     — explicit worker_halt_file terminal event.
  * ``"silent_failure"`` — worker_completed exit_code=0 but zero new commits
    (caught by Phase 0 Task 0.2 commit-gap detector).
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
from bmad_orchestrator.runtime.event_loop import EventLoop
from bmad_orchestrator.runtime.worker_spawn import WorkerHandle


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


def _make_handle(tmp_path: Path, jsonl: Path, *, base_sha: str | None) -> WorkerHandle:
    return WorkerHandle(
        worktree=str(tmp_path),
        story_id="s1",
        branch="feature/s1",
        pid=0,
        jsonl_path=jsonl,
        process=None,
        mock=False,
        sandbox_kind="bwrap",
        base_sha=base_sha,
    )


async def _drain(bus: EventLoop) -> None:
    while await bus.dispatch_one(timeout=0.01) is not None:
        pass
    await bus.stop()


@pytest.mark.asyncio
async def test_tail_returns_completed_on_success_with_commits(tmp_path: Path) -> None:
    base = _init_repo(tmp_path)
    (tmp_path / "out.txt").write_text("done\n")
    _git(tmp_path, "add", "out.txt")
    _git(tmp_path, "commit", "-q", "-m", "story output")
    jsonl = tmp_path / "events.jsonl"
    _write_jsonl(
        jsonl,
        [{"event_type": "worker_completed", "exit_code": 0, "status": "success"}],
    )
    handle = _make_handle(tmp_path, jsonl, base_sha=base)
    bus = EventLoop()
    outcome = await _tail_and_emit_completion(handle, bus)
    await _drain(bus)
    assert outcome == "completed"


@pytest.mark.asyncio
async def test_tail_returns_failed_on_nonzero_exit(tmp_path: Path) -> None:
    jsonl = tmp_path / "events.jsonl"
    _write_jsonl(
        jsonl,
        [{"event_type": "worker_completed", "exit_code": 2, "status": "error"}],
    )
    # No base_sha so the commit-gap path is bypassed; the exit_code drives outcome.
    handle = _make_handle(tmp_path, jsonl, base_sha=None)
    bus = EventLoop()
    outcome = await _tail_and_emit_completion(handle, bus)
    await _drain(bus)
    assert outcome == "failed"


@pytest.mark.asyncio
async def test_tail_returns_halted_on_explicit_halt_file(tmp_path: Path) -> None:
    jsonl = tmp_path / "events.jsonl"
    _write_jsonl(
        jsonl,
        [
            {
                "event_type": "worker_halt_file",
                "reason": "loc_cap_exceeded",
            }
        ],
    )
    handle = _make_handle(tmp_path, jsonl, base_sha=None)
    bus = EventLoop()
    outcome = await _tail_and_emit_completion(handle, bus)
    await _drain(bus)
    assert outcome == "halted"


@pytest.mark.asyncio
async def test_tail_returns_silent_failure_when_zero_commits(tmp_path: Path) -> None:
    """exit=0 but no new commits ⇒ Phase 0 commit-gap path returns ``silent_failure``."""
    base = _init_repo(tmp_path)
    jsonl = tmp_path / "events.jsonl"
    _write_jsonl(
        jsonl,
        [{"event_type": "worker_completed", "exit_code": 0, "status": "success"}],
    )
    handle = _make_handle(tmp_path, jsonl, base_sha=base)
    bus = EventLoop()
    outcome = await _tail_and_emit_completion(handle, bus)
    await _drain(bus)
    assert outcome == "silent_failure"
