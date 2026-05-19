"""#9 NEW-9 — Stage 6 verdict is the source-of-truth for worker success.

Pilot run #3 (Antares 1a) regression: story 1.5 ran the full cycle — dev-story
commit + Stage 6 review (verdict approve) + autofix commit — 2 real commits.
But ``bmad-auto-dev-runner.sh`` exited non-zero on a late cleanup stage, and the
NEW-4 inner-exit override flipped the otherwise-complete story to ``failure``.
``succeeded=0 failed=3`` despite real work, and ``_reconcile_success_verdicts``
saw no success → no ``integration/<wave>`` branch.

NEW-9 demotes the runner exit code from sole decider to a secondary signal:
``decide_worker_status`` resolves the terminal status from the Stage 6 verdict
+ commit count, falling back to exit-code logic only when no review log exists.

Coverage:
  * 5 unit  — :func:`decide_worker_status` decision rules.
  * 3 unit  — :func:`read_runner_verdict` over runner Stage 6 logs.
  * 2 integration — ``_tail_and_emit_completion`` resolves status by verdict.
  * 1 regression — the pilot run #3 1.5 fixture (approve + 2 commits + inner
    exit non-zero) now lands as ``status=success``.
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
from bmad_orchestrator.runtime.verdict_fallback import read_runner_verdict
from bmad_orchestrator.runtime.worker_silent_failure import decide_worker_status
from bmad_orchestrator.runtime.worker_spawn import WorkerHandle

# ── unit — decide_worker_status ─────────────────────────────────────────────


def test_decide_approve_with_commits_is_success_despite_inner_exit() -> None:
    """approve + commits → success even when the inner runner exit is non-zero."""
    assert (
        decide_worker_status(
            verdict="approve", new_commits_count=2, inner_exit=2, outer_exit=0
        )
        == "success"
    )


def test_decide_approve_zero_commits_is_failure() -> None:
    """approve with no work produced is pathological — treat as failure."""
    assert (
        decide_worker_status(
            verdict="approve", new_commits_count=0, inner_exit=0, outer_exit=0
        )
        == "failure"
    )


def test_decide_request_changes_is_failure_even_with_commits() -> None:
    """request_changes → failure even when commits exist (review rejected it)."""
    assert (
        decide_worker_status(
            verdict="request_changes",
            new_commits_count=3,
            inner_exit=0,
            outer_exit=0,
        )
        == "failure"
    )
    # reject behaves the same way.
    assert (
        decide_worker_status(
            verdict="reject", new_commits_count=3, inner_exit=0, outer_exit=0
        )
        == "failure"
    )


def test_decide_verdict_none_inner_failure_falls_back_to_failure() -> None:
    """No review log → exit-code fallback: non-zero inner exit → failure."""
    assert (
        decide_worker_status(
            verdict=None, new_commits_count=2, inner_exit=1, outer_exit=0
        )
        == "failure"
    )
    # non-zero outer exit also fails in fallback mode.
    assert (
        decide_worker_status(
            verdict=None, new_commits_count=2, inner_exit=None, outer_exit=1
        )
        == "failure"
    )


def test_decide_verdict_none_clean_exit_with_commits_is_success() -> None:
    """No review log + clean exit codes + commits → success (legacy path)."""
    assert (
        decide_worker_status(
            verdict=None, new_commits_count=1, inner_exit=0, outer_exit=0
        )
        == "success"
    )


# ── unit — read_runner_verdict ──────────────────────────────────────────────


def _write_review_log(worktree: Path, story_id: str, token: str) -> None:
    reviews = worktree / "_bmad" / "auto-dev-state" / "reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    (reviews / f"{story_id}-stage6.log").write_text(
        f"Stage 6 code-review for {story_id}\n... review body ...\n{token}\n",
        encoding="utf-8",
    )


def test_read_runner_verdict_approve(tmp_path: Path) -> None:
    _write_review_log(tmp_path, "1.5", "PASS")
    assert read_runner_verdict(tmp_path, "1.5") == "approve"


def test_read_runner_verdict_request_changes(tmp_path: Path) -> None:
    _write_review_log(tmp_path, "1.5", "NEEDS-FIX")
    assert read_runner_verdict(tmp_path, "1.5") == "request_changes"


def test_read_runner_verdict_missing_log_is_none(tmp_path: Path) -> None:
    # no reviews directory at all → None (caller falls back to exit codes).
    assert read_runner_verdict(tmp_path, "1.5") is None


# ── helpers — integration / regression ──────────────────────────────────────


def _write_jsonl(path: Path, events: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(ev) for ev in events) + "\n", encoding="utf-8"
    )


async def _run_tail(handle: WorkerHandle) -> tuple[str, list[Event]]:
    bus = EventLoop()
    completed: list[Event] = []

    async def collect(event: Event) -> None:
        if event.type == EventType.WORKER_COMPLETED:
            completed.append(event)

    bus.on(collect)
    outcome = await _tail_and_emit_completion(handle, bus)
    while await bus.dispatch_one(timeout=0.01) is not None:
        pass
    await bus.stop()
    return outcome, completed


def _handle(worktree: Path, jsonl: Path, *, base_sha: str | None) -> WorkerHandle:
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


def _repo_with_commits(repo: Path, n_extra: int) -> str:
    """Init a repo, return base SHA, add ``n_extra`` commits past base."""
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("hi\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "init")
    base = _git(repo, "rev-parse", "HEAD").strip()
    for i in range(n_extra):
        (repo / f"work{i}.txt").write_text(f"story 1.5 work {i}\n")
        _git(repo, "add", f"work{i}.txt")
        _git(repo, "commit", "-q", "-m", f"story 1.5 work {i}")
    return base


# ── integration — _tail_and_emit_completion resolves status by verdict ──────


@pytest.mark.asyncio
async def test_tail_request_changes_verdict_fails_despite_commits(
    tmp_path: Path,
) -> None:
    """A NEEDS-FIX Stage 6 log flips a commits-bearing run to failure."""
    repo = tmp_path / "repo"
    base = _repo_with_commits(repo, 2)
    _write_review_log(repo, "1.5", "NEEDS-FIX")

    jsonl = repo / "events.jsonl"
    _write_jsonl(
        jsonl,
        [
            {"event_type": "stdout_line", "text": "Stage 6 review done"},
            {"event_type": "worker_completed", "exit_code": 0, "status": "success"},
        ],
    )
    outcome, completed = await _run_tail(_handle(repo, jsonl, base_sha=base))

    assert outcome == "failed"
    payload = completed[0].payload
    assert payload["status"] == "failure"
    assert payload["verdict"] == "request_changes"
    assert payload["status_decided_by"] == "verdict"
    assert payload["new_commits_count"] == 2


@pytest.mark.asyncio
async def test_tail_no_review_log_uses_exit_code_fallback(
    tmp_path: Path,
) -> None:
    """No Stage 6 log → status decided by exit codes, backwards-compatible."""
    repo = tmp_path / "repo"
    base = _repo_with_commits(repo, 1)

    jsonl = repo / "events.jsonl"
    _write_jsonl(
        jsonl,
        [
            {"event_type": "stdout_line", "text": "ordinary worker output"},
            {"event_type": "worker_completed", "exit_code": 0, "status": "success"},
        ],
    )
    outcome, completed = await _run_tail(_handle(repo, jsonl, base_sha=base))

    assert outcome == "completed"
    payload = completed[0].payload
    assert payload["status"] == "success"
    assert payload["verdict"] is None
    assert payload["status_decided_by"] == "exit_code_fallback"


# ── regression — pilot run #3 story 1.5 fixture ─────────────────────────────


@pytest.mark.asyncio
async def test_regression_pilot3_story_1_5_approve_inner_exit_nonzero(
    tmp_path: Path,
) -> None:
    """Pilot run #3 1.5: approve verdict + 2 commits + inner runner exit 2.

    Before NEW-9 the non-zero inner exit forced ``status=failure``. Now the
    approve verdict + real commits resolve it to ``success``; the inner exit
    code is still surfaced for forensics, with ``status_decided_by=verdict``.
    """
    repo = tmp_path / "repo"
    base = _repo_with_commits(repo, 2)  # dev-story + autofix commits
    _write_review_log(repo, "1.5", "PASS")

    jsonl = repo / "events.jsonl"
    _write_jsonl(
        jsonl,
        [
            {"event_type": "stdout_line", "text": "Stage 6.pass — review approved"},
            {"event_type": "stdout_line", "text": "❯ Exit code: 2"},
            {"event_type": "worker_completed", "exit_code": 0, "status": "failure"},
        ],
    )
    outcome, completed = await _run_tail(_handle(repo, jsonl, base_sha=base))

    assert outcome == "completed"
    payload = completed[0].payload
    assert payload["status"] == "success"
    assert payload["verdict"] == "approve"
    assert payload["status_decided_by"] == "verdict"
    assert payload["new_commits_count"] == 2
    # inner exit code still surfaced for forensics.
    assert payload["inner_exit_code"] == 2
    assert payload["outer_exit_code"] == 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
