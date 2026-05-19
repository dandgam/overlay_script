"""NEW-17 — a worker that exits without committing its work is loud, not silent.

pilot_findings_closure_v6 spec §5: on real Antares story 1.5 the worker exited
``status=success`` with ZERO new commits past base_sha (already flagged as
``worker_silent_failure``) — but it had *written files* into its worktree and
just never committed them, with no ``stage5`` recovery marker. The plain
``worker_silent_failure`` event cannot tell "worker did nothing" from "worker
did work and lost it"; the latter is strictly worse.

:func:`decide_uncommitted_exit` decides whether to additionally emit a loud
``WORKER_EXIT_UNCOMMITTED`` audit event.

Coverage: 2 unit (decision matrix) + 1 integration (``_tail_and_emit_completion``
over a mock JSONL + a real dirty git worktree → event on the bus).
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
from bmad_orchestrator.runtime.worker_silent_failure import decide_uncommitted_exit
from bmad_orchestrator.runtime.worker_spawn import WorkerHandle

# ── unit — decide_uncommitted_exit ──────────────────────────────────────────


def test_dirty_worktree_no_stage5_emits() -> None:
    """Worktree dirty + stage5 never ran → emit WORKER_EXIT_UNCOMMITTED."""
    decision = decide_uncommitted_exit(worktree_dirty=True, stage5_seen=False)
    assert decision.emit is True
    assert decision.reason == "uncommitted_no_stage5"


def test_clean_or_stage5_suppresses_event() -> None:
    """A clean worktree, or one where stage5 ran, must NOT emit the event."""
    clean = decide_uncommitted_exit(worktree_dirty=False, stage5_seen=False)
    assert clean.emit is False
    assert clean.reason == "clean_worktree"

    stage5 = decide_uncommitted_exit(worktree_dirty=True, stage5_seen=True)
    assert stage5.emit is False
    assert stage5.reason == "stage5_ran"


# ── integration — _tail_and_emit_completion over a dirty git worktree ───────


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


def _write_jsonl(path: Path, events: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(ev) for ev in events) + "\n", encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_uncommitted_exit_emits_event_on_dirty_worktree(
    tmp_path: Path,
) -> None:
    """Worker exits success with zero new commits but a dirty worktree —
    the orchestrator emits WORKER_EXIT_UNCOMMITTED, not just silence."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("hi\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "init")
    base = _git(repo, "rev-parse", "HEAD").strip()

    # Worker wrote a file into the worktree but never committed it.
    (repo / "story_work.py").write_text("# uncommitted story 1.5 work\n")

    jsonl = repo / "events.jsonl"
    _write_jsonl(
        jsonl,
        [
            {"event_type": "stdout_line", "text": "ordinary worker output"},
            {"event_type": "worker_completed", "exit_code": 0, "status": "success"},
        ],
    )
    handle = WorkerHandle(
        worktree=str(repo),
        story_id="1.5",
        branch="feature/1.5",
        pid=0,
        jsonl_path=jsonl,
        process=None,
        mock=False,
        sandbox_kind="bwrap",
        base_sha=base,
    )

    bus = EventLoop()
    seen: list[Event] = []

    async def collect(event: Event) -> None:
        seen.append(event)

    bus.on(collect)
    outcome = await _tail_and_emit_completion(handle, bus)
    while await bus.dispatch_one(timeout=0.01) is not None:
        pass
    await bus.stop()

    assert outcome == "silent_failure"
    uncommitted = [
        ev for ev in seen if ev.type == EventType.WORKER_EXIT_UNCOMMITTED
    ]
    assert len(uncommitted) == 1
    assert uncommitted[0].payload["story_id"] == "1.5"
    assert uncommitted[0].payload["reason"] == "uncommitted_no_stage5"
