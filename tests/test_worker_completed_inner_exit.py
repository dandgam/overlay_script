"""#4 NEW-4 — ``worker_completed`` honours the inner runner exit code.

Race closed here: the outer ``claude -p`` process exits 0 (it dutifully ran),
while the inner ``bmad-auto-dev-runner.sh`` exited non-zero and echoed
``Exit code: N`` to stdout. ``_tail_and_emit_completion`` used to look only at
the outer code and emit ``worker_completed status=success`` — a false positive
that masks a real halt. The orchestrator now parses the inner code from the
stdout tail and lets a non-zero inner code override an outer-0 success.

Coverage:
  * 4 unit  — :func:`parse_inner_exit_code` (zero, non-zero, glyph form,
    absent, last-wins, whitespace tolerance).
  * 2 behaviour — ``_tail_and_emit_completion`` over a mock JSONL: mismatch
    flips status to ``failure``; matched-zero / absent preserve success.
  * 1 integration — a real git repo with commits past base AND an inner
    failure: the override beats the otherwise-``completed`` commits path.
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
from bmad_orchestrator.runtime.worker_silent_failure import parse_inner_exit_code
from bmad_orchestrator.runtime.worker_spawn import WorkerHandle

# ── unit — parse_inner_exit_code ────────────────────────────────────────────


def test_parse_inner_exit_code_zero() -> None:
    assert parse_inner_exit_code(["Stage 7 cleanup", "Exit code: 0"]) == 0


def test_parse_inner_exit_code_nonzero_and_glyph_form() -> None:
    assert parse_inner_exit_code(["Exit code: 1"]) == 1
    # the ``❯`` shell prompt glyph the wrapper sometimes prepends
    assert parse_inner_exit_code(["❯ Exit code: 137"]) == 137


def test_parse_inner_exit_code_absent_returns_none() -> None:
    # no marker line → caller preserves the legacy outer-exit-only behaviour
    assert parse_inner_exit_code(["just some", "ordinary output"]) is None
    # a substring mention must NOT match — only a full line counts
    assert parse_inner_exit_code(["the Exit code: 1 was logged earlier"]) is None


def test_parse_inner_exit_code_last_wins_and_whitespace() -> None:
    # nested wrappers may echo more than once — the last marker wins
    assert parse_inner_exit_code(["Exit code: 0", "Exit code: 2"]) == 2
    # surrounding whitespace is tolerated
    assert parse_inner_exit_code(["  Exit code: 3  "]) == 3
    # non-str entries are skipped, not crashed on
    assert parse_inner_exit_code([None, 42, "Exit code: 4"]) == 4  # type: ignore[list-item]


# ── helpers for the behaviour / integration tests ───────────────────────────


def _write_jsonl(path: Path, events: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(ev) for ev in events) + "\n", encoding="utf-8"
    )


async def _run_tail(handle: WorkerHandle) -> tuple[str, list[Event]]:
    """Drive ``_tail_and_emit_completion`` and collect WORKER_COMPLETED events."""
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


def _handle(jsonl: Path, *, base_sha: str | None = None) -> WorkerHandle:
    return WorkerHandle(
        worktree=str(jsonl.parent),
        story_id="1.3",
        branch="feature/1.3",
        pid=0,
        jsonl_path=jsonl,
        process=None,
        mock=False,
        sandbox_kind="bwrap",
        base_sha=base_sha,
    )


# ── behaviour — _tail_and_emit_completion over mock JSONL ───────────────────


@pytest.mark.asyncio
async def test_inner_failure_overrides_outer_zero(tmp_path: Path) -> None:
    """inner exit 1 + outer 0 → status=failure with both codes recorded."""
    jsonl = tmp_path / "wt" / "events.jsonl"
    _write_jsonl(
        jsonl,
        [
            {"event_type": "stdout_line", "text": "Stage 7 cleanup"},
            {"event_type": "stdout_line", "text": "Exit code: 1"},
            {"event_type": "worker_completed", "exit_code": 0, "status": "success"},
        ],
    )
    outcome, completed = await _run_tail(_handle(jsonl))

    assert outcome == "failed"
    assert len(completed) == 1
    payload = completed[0].payload
    assert payload["status"] == "failure"
    assert payload["inner_exit_code"] == 1
    assert payload["outer_exit_code"] == 0
    assert payload["exit_code"] == 0  # outer is still surfaced verbatim


@pytest.mark.asyncio
async def test_inner_zero_and_absent_preserve_success(tmp_path: Path) -> None:
    """inner exit 0 — and a missing marker — both keep the success path."""
    matched = tmp_path / "a" / "events.jsonl"
    _write_jsonl(
        matched,
        [
            {"event_type": "stdout_line", "text": "Exit code: 0"},
            {"event_type": "worker_completed", "exit_code": 0, "status": "success"},
        ],
    )
    outcome, completed = await _run_tail(_handle(matched))
    assert outcome == "completed"
    assert completed[0].payload["status"] == "success"
    assert "inner_exit_code" not in completed[0].payload

    absent = tmp_path / "b" / "events.jsonl"
    _write_jsonl(
        absent,
        [
            {"event_type": "stdout_line", "text": "ordinary worker output"},
            {"event_type": "worker_completed", "exit_code": 0, "status": "success"},
        ],
    )
    outcome, completed = await _run_tail(_handle(absent))
    assert outcome == "completed"
    assert completed[0].payload["status"] == "success"
    assert "inner_exit_code" not in completed[0].payload


# ── integration — real git repo, commits past base AND inner failure ────────


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


@pytest.mark.asyncio
async def test_inner_failure_beats_commits_completed_path(tmp_path: Path) -> None:
    """Real work on the branch would yield ``completed`` — the inner exit
    code 1 still flips the terminal to ``failed`` / ``status=failure``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("hi\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "init")
    base = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "work.txt").write_text("story 1.3 work\n")
    _git(repo, "add", "work.txt")
    _git(repo, "commit", "-q", "-m", "story 1.3 work")

    jsonl = repo / "events.jsonl"
    _write_jsonl(
        jsonl,
        [
            {"event_type": "stdout_line", "text": "Stage 6.pass — merged"},
            {"event_type": "stdout_line", "text": "❯ Exit code: 1"},
            {"event_type": "worker_completed", "exit_code": 0, "status": "success"},
        ],
    )
    outcome, completed = await _run_tail(_handle(jsonl, base_sha=base))

    assert outcome == "failed"
    assert completed[0].payload["status"] == "failure"
    assert completed[0].payload["inner_exit_code"] == 1
    assert completed[0].payload["outer_exit_code"] == 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
