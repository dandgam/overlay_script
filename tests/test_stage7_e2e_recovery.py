"""#2 NEW-2 — Stage 7 reused-worktree recovery, end-to-end (2026-05-19).

Two integration tests spanning both defence layers:

  1. Layer B e2e — an *old* runner (no Layer A patch) emits the
     ``cannot delete branch ... used by worktree`` refusal; the orchestrator
     detector turns it into a synthetic approve verdict, and a merge subscriber
     wired onto the bus actually creates ``integration/wave-1a`` from the
     feature branch — proving lost work is recovered despite a runner failure.
  2. Layer A e2e — the *patched* runner's ``stage7_cleanup_feature_branch``
     skips gracefully against a real reused worktree, preserves the branch, and
     prints a synthetic verdict line that parses as a valid claude_event.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from bmad_orchestrator.agent.run import _tail_and_emit_completion
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.runtime.worker_spawn import WorkerHandle

_GIT_ERR = (
    "error: cannot delete branch 'feature/1.3' used by worktree "
    "at '/home/server/Antares/.worktrees/wt-1.3'"
)
_RUNNER = (
    Path(__file__).resolve().parents[1]
    / "skills" / "upstream" / "bmad-auto-dev" / "scripts"
    / "bmad-auto-dev-runner.sh"
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


def _write_jsonl(path: Path, events: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(ev) for ev in events) + "\n", encoding="utf-8"
    )


def _init_repo_with_feature(path: Path, *, leave_on_feature: bool) -> str:
    """Repo with ``feature/1.3`` carrying one extra commit past ``main``.

    ``leave_on_feature`` keeps HEAD on ``feature/1.3`` — the real worker worktree
    state (the orchestrator hands the worktree to the runner already on the
    feature branch). When False the repo ends on ``main``.
    """
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    (path / "README.md").write_text("hi\n")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "init")
    base = _git(path, "rev-parse", "HEAD").strip()
    _git(path, "checkout", "-q", "-b", "feature/1.3")
    (path / "work.txt").write_text("recovered story 1.3 work\n")
    _git(path, "add", "work.txt")
    _git(path, "commit", "-q", "-m", "story 1.3 work")
    if not leave_on_feature:
        _git(path, "checkout", "-q", "main")
    return base


# ── 1. Layer B e2e — detector recovery creates the integration branch ───────


@pytest.mark.asyncio
async def test_e2e_old_runner_error_recovered_into_integration_branch(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    # worker worktree HEAD stays on feature/1.3 — the runner crashed before any
    # checkout back; the recovered commits live on the worktree's HEAD.
    base = _init_repo_with_feature(repo, leave_on_feature=True)

    jsonl = repo / "events.jsonl"
    # An old runner (no Layer A) merged the story then crashed on `git branch -d`.
    _write_jsonl(
        jsonl,
        [
            {"event_type": "stdout_line", "text": "Stage 6.pass — merge feature/1.3"},
            {"event_type": "stdout_line", "text": _GIT_ERR},
            # outer claude -p still exits 0 (the NEW-4 race) — see S3.
            {"event_type": "worker_completed", "exit_code": 0, "status": "success"},
        ],
    )
    handle = WorkerHandle(
        worktree=str(repo),
        story_id="1.3",
        branch="feature/1.3",
        pid=0,
        jsonl_path=jsonl,
        process=None,
        mock=False,
        sandbox_kind="bwrap",
        base_sha=base,
    )

    bus = EventLoop()
    merged: dict[str, Any] = {}

    async def merge_subscriber(event: Event) -> None:
        """Minimal stand-in for merge_to_integration_subscriber."""
        if event.type != EventType.CODE_REVIEW_VERDICT:
            return
        if event.payload.get("verdict") != "approve":
            return
        story = str(event.payload["story_id"])
        feature = f"feature/{story}"
        _git(repo, "branch", "integration/wave-1a", "main")
        _git(repo, "checkout", "-q", "integration/wave-1a")
        _git(repo, "merge", "--no-ff", "-m", f"merge story {story}", feature)
        merged["source"] = event.payload.get("source")
        merged["commits"] = event.payload.get("commits")

    bus.on(merge_subscriber)

    outcome = await _tail_and_emit_completion(handle, bus)
    while await bus.dispatch_one(timeout=0.01) is not None:
        pass
    await bus.stop()

    assert outcome == "completed"
    assert merged["source"] == "runner_cleanup_recovery"
    assert merged["commits"] == 1
    # the integration branch now exists and carries the recovered work.
    branches = _git(repo, "branch", "--list", "integration/wave-1a")
    assert "integration/wave-1a" in branches
    files = _git(repo, "ls-tree", "--name-only", "integration/wave-1a")
    assert "work.txt" in files


# ── 2. Layer A e2e — patched runner skips + emits a parseable verdict ───────


def _extract_stage7_function() -> str:
    text = _RUNNER.read_text(encoding="utf-8")
    m = re.search(
        r"^stage7_cleanup_feature_branch\(\) \{.*?^\}",
        text, flags=re.DOTALL | re.MULTILINE,
    )
    assert m is not None
    return m.group(0)


def test_e2e_patched_runner_skips_reused_worktree_and_keeps_work(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo_with_feature(repo, leave_on_feature=False)
    wt = tmp_path / "wt-1.3"
    _git(repo, "worktree", "add", str(wt), "feature/1.3")

    harness = (
        "set -euo pipefail\n"
        "log()  { printf 'log %s\\n' \"$*\"; }\n"
        "warn() { printf 'WARN %s\\n' \"$*\" >&2; }\n"
        f"{_extract_stage7_function()}\n"
        'stage7_cleanup_feature_branch "feature/1.3" "main"\n'
    )
    proc = subprocess.run(
        ["bash", "-c", harness],
        cwd=str(repo), capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr

    # the held feature branch survives the skip — work is not lost.
    assert (
        subprocess.run(
            ["git", "-C", str(repo), "show-ref", "--verify", "--quiet",
             "refs/heads/feature/1.3"],
        ).returncode
        == 0
    )

    # the synthetic verdict line parses as a valid claude_event the
    # orchestrator's JSONL stream can ingest.
    verdict_lines = [
        ln for ln in proc.stdout.splitlines()
        if ln.startswith("{") and "verdict" in ln
    ]
    assert len(verdict_lines) == 1
    payload = json.loads(verdict_lines[0])
    assert payload["event_type"] == "claude_event"
    assert payload["verdict"] == "approve"
    assert payload["source"] == "runner_stage7_skip"
    assert payload["commits"] == 1
    assert payload["branch"] == "feature/1.3"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
