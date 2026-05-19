"""NEW-23 — ``_ff_merge_to_integration`` survives a dirty integration worktree.

pilot run #6 / replay 1.5 (2026-05-20): all review verdicts were ``approve`` but
``git merge feature/1.5 --ff-only`` aborted with::

    error: Your local changes to the following files would be overwritten by
    merge: _bmad/output/planning/stories/sprint-status.yaml

The orchestrator's own ``mark_sprint_status_done`` had left ``sprint-status.yaml``
uncommitted in the integration worktree. The fix discards tracked residue before
the ff-merge (integration is an automated branch; the feature branch carries the
authoritative metadata version).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from bmad_orchestrator.agent.run import _ff_merge_to_integration


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


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "target"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    status = repo / "sprint-status.yaml"
    status.write_text("1-4: ready-for-dev\n1-5: ready-for-dev\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    # integration/1a == main; feature/1.5 is a ff-able descendant.
    _git(repo, "branch", "integration/1a")
    _git(repo, "checkout", "-b", "feature/1.5")
    (repo / "story.txt").write_text("story 1.5 work\n")
    status.write_text("1-4: ready-for-dev\n1-5: done\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "feat(1.5)")
    _git(repo, "checkout", "integration/1a")
    return repo


@pytest.mark.asyncio
async def test_ff_merge_discards_dirty_tracked_residue(tmp_path: Path) -> None:
    """A dirty tracked file in the integration worktree must not block the merge."""
    repo = _make_repo(tmp_path)
    feature_head = _git(repo, "rev-parse", "feature/1.5").strip()

    # Orchestrator residue: mark_sprint_status_done wrote sprint-status.yaml
    # but never committed it.
    (repo / "sprint-status.yaml").write_text("1-4: done\n1-5: done\n")
    assert _git(repo, "status", "--porcelain").strip()  # dirty

    head = await _ff_merge_to_integration(
        target_project=repo,
        integration_branch="integration/1a",
        feature_branch="feature/1.5",
    )

    assert head == feature_head
    assert _git(repo, "rev-parse", "integration/1a").strip() == feature_head
    assert (repo / "story.txt").exists()


@pytest.mark.asyncio
async def test_ff_merge_clean_worktree_still_works(tmp_path: Path) -> None:
    """Regression: a clean integration worktree merges exactly as before."""
    repo = _make_repo(tmp_path)
    feature_head = _git(repo, "rev-parse", "feature/1.5").strip()

    head = await _ff_merge_to_integration(
        target_project=repo,
        integration_branch="integration/1a",
        feature_branch="feature/1.5",
    )

    assert head == feature_head
