"""NEW-31 — _ff_merge_to_integration rebases feature branch onto integration.

Parallel stories each branch ``feature/<story>`` from the same integration
base. After the first story ff-merges, integration moves forward and the
second story's branch is no longer a descendant → ``--ff-only`` fails.
Fix: rebase the feature branch inside its worker worktree before the merge.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from bmad_orchestrator.agent.run import _ff_merge_to_integration

# ── helpers ──────────────────────────────────────────────────────────────────

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        env=GIT_ENV,
    ).stdout.strip()


def _make_parallel_stories_repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Build a repo with two feature branches off the same integration base.

    Layout::

        base ← integration/x
              ← feature/s1  (adds file_a.py)
              ← feature/s2  (adds file_b.py, DISJOINT from s1)

    Returns (target_repo, worktree_s1, worktree_s2).
    The main clone is left on ``integration/x`` to mirror the orchestrator
    checkout convention.
    """
    repo = tmp_path / "target"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    (repo / "base.txt").write_text("base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")

    # integration/x starts at same commit as main
    _git(repo, "branch", "integration/x")

    # feature/s1 — adds file_a.py (branch off integration/x)
    _git(repo, "checkout", "-b", "feature/s1", "integration/x")
    (repo / "file_a.py").write_text("# story 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "feat(s1)")

    # feature/s2 — adds file_b.py (DISJOINT), also off integration/x
    _git(repo, "checkout", "-b", "feature/s2", "integration/x")
    (repo / "file_b.py").write_text("# story 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "feat(s2)")

    # Return to integration/x so neither feature branch is active
    _git(repo, "checkout", "integration/x")

    # Add worktrees for each feature branch — must happen AFTER checking out
    # integration/x so neither feature branch is "current" in the main clone.
    wt1 = tmp_path / "wt_s1"
    wt2 = tmp_path / "wt_s2"
    _git(repo, "worktree", "add", str(wt1), "feature/s1")
    _git(repo, "worktree", "add", str(wt2), "feature/s2")

    return repo, wt1, wt2


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_second_story_ff_merges_cleanly_after_rebase(tmp_path: Path) -> None:
    """After the first story merges, the second (disjoint) must succeed via rebase."""
    repo, wt1, wt2 = _make_parallel_stories_repo(tmp_path)

    # Merge first story — integration/x advances
    sha1 = await _ff_merge_to_integration(
        target_project=repo,
        integration_branch="integration/x",
        feature_branch="feature/s1",
        worktree=str(wt1),
    )
    assert (repo / "file_a.py").exists()

    # Merge second story — without rebase this would fail; with rebase it succeeds.
    sha2 = await _ff_merge_to_integration(
        target_project=repo,
        integration_branch="integration/x",
        feature_branch="feature/s2",
        worktree=str(wt2),
    )
    assert sha2 != sha1
    assert (repo / "file_b.py").exists()
    assert (repo / "file_a.py").exists()  # first story's work preserved


@pytest.mark.asyncio
async def test_conflicting_branch_raises_and_no_rebase_in_progress(
    tmp_path: Path,
) -> None:
    """A conflicting rebase must raise and leave NO rebase-in-progress state."""
    repo = tmp_path / "target"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    (repo / "conflict.txt").write_text("original\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")

    _git(repo, "branch", "integration/x")

    # First story merges a change to conflict.txt into integration/x
    _git(repo, "checkout", "-b", "feature/s1", "integration/x")
    (repo / "conflict.txt").write_text("changed by s1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "feat(s1)")

    # Merge s1 into integration so integration advances past the base
    _git(repo, "checkout", "integration/x")
    _git(repo, "merge", "feature/s1", "--ff-only")

    # Second story divergently changed the SAME file from the pre-s1 base
    # — rebase onto integration/x will conflict.
    # Create branch from the original base (one commit before integration)
    base_sha = _git(repo, "rev-parse", "integration/x~1")
    _git(repo, "checkout", "-b", "feature/s2", base_sha)
    (repo / "conflict.txt").write_text("divergent change by s2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "feat(s2)")

    # Return to integration/x so feature/s2 is not active in main clone
    _git(repo, "checkout", "integration/x")

    # Set up worktree for s2 (safe now: s2 not current in main clone)
    wt2 = tmp_path / "wt_s2"
    _git(repo, "worktree", "add", str(wt2), "feature/s2")

    with pytest.raises(Exception):
        await _ff_merge_to_integration(
            target_project=repo,
            integration_branch="integration/x",
            feature_branch="feature/s2",
            worktree=str(wt2),
        )

    # No rebase-in-progress state must remain in the worktree
    # (worktree .git is a FILE, not a dir; MERGE_HEAD / REBASE_HEAD are
    # files written by git inside the GIT_DIR pointed to by that file)
    wt_git_dir_file = wt2 / ".git"
    assert wt_git_dir_file.is_file(), "worktree .git should be a file"

    # Parse the actual GIT_DIR path from the .git file
    git_dir_line = wt_git_dir_file.read_text().strip()
    # Format: "gitdir: /abs/path/to/.git/worktrees/<name>"
    git_dir = Path(git_dir_line.split("gitdir:", 1)[1].strip())

    assert not (git_dir / "rebase-merge").exists(), "rebase-merge must be cleaned up"
    assert not (git_dir / "rebase-apply").exists(), "rebase-apply must be cleaned up"


@pytest.mark.asyncio
async def test_new34_fresh_integration_branch_diverging_feature_succeeds(
    tmp_path: Path,
) -> None:
    """NEW-34 — feature diverges from freshly-created integration branch.

    Pilot 2c scenario: feature/3-2-zfs was created from an older base commit
    (base_sha=1d86f83).  integration/2c did NOT exist yet; _ff_merge_to_integration
    creates it from main (which is ahead of the old base).  Before the fix,
    ``integration_branch in existing`` was False (captured before creation) →
    rebase skipped → ff-merge raised exit-128.  After the fix the rebase guard
    no longer relies on the stale ``existing`` set.
    """
    repo = tmp_path / "target"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")

    # Commit 1 — base (this is where feature will be created from)
    (repo / "base.txt").write_text("base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    old_base_sha = _git(repo, "rev-parse", "HEAD")

    # Commit 2 — main advances (simulates work merged before pilot 2c)
    (repo / "main_advance.txt").write_text("main advance\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "main: advance")

    # feature/s1 branches off the OLD base (diverges from current main)
    _git(repo, "checkout", "-b", "feature/s1", old_base_sha)
    (repo / "feature_work.py").write_text("# new feature\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "feat(s1): new feature")

    # Return to main (integration branch does NOT exist yet)
    _git(repo, "checkout", "main")

    # Add worktree for the feature branch
    wt1 = tmp_path / "wt_s1"
    _git(repo, "worktree", "add", str(wt1), "feature/s1")

    # _ff_merge_to_integration must: (1) create integration/x from main,
    # (2) rebase feature/s1 onto it (NEW-34 fix), (3) ff-merge successfully.
    sha = await _ff_merge_to_integration(
        target_project=repo,
        integration_branch="integration/x",  # does NOT pre-exist
        feature_branch="feature/s1",
        worktree=str(wt1),
    )
    assert sha
    assert (repo / "feature_work.py").exists(), "feature work must be present"
    assert (repo / "main_advance.txt").exists(), "main advance must be present"


@pytest.mark.asyncio
async def test_new34_fresh_integration_conflict_escalates(tmp_path: Path) -> None:
    """NEW-34 — conflicting rebase on fresh integration branch raises cleanly.

    When integration branch is freshly created and feature diverges AND conflicts,
    _ff_merge_to_integration must: abort the rebase and raise (so the caller's
    except block can emit HUMAN_QUERY).  Worktree must have no rebase-in-progress
    state.
    """
    repo = tmp_path / "target"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")

    # Base commit — shared file that will conflict
    (repo / "conflict.txt").write_text("original\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    old_base_sha = _git(repo, "rev-parse", "HEAD")

    # main advances AND modifies conflict.txt
    (repo / "conflict.txt").write_text("changed by main\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "main: change conflict.txt")

    # feature/s1 branches from OLD base and divergently changes the same file
    _git(repo, "checkout", "-b", "feature/s1", old_base_sha)
    (repo / "conflict.txt").write_text("changed by feature\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "feat(s1): divergent conflict.txt")

    _git(repo, "checkout", "main")

    wt1 = tmp_path / "wt_s1"
    _git(repo, "worktree", "add", str(wt1), "feature/s1")

    # _ff_merge_to_integration must raise (rebase conflict)
    import pytest as _pytest

    with _pytest.raises(Exception):
        await _ff_merge_to_integration(
            target_project=repo,
            integration_branch="integration/x",  # does NOT pre-exist
            feature_branch="feature/s1",
            worktree=str(wt1),
        )

    # Worktree must have no rebase-in-progress state
    wt_git_file = wt1 / ".git"
    assert wt_git_file.is_file(), "worktree .git should be a file pointer"
    git_dir = Path(wt_git_file.read_text().strip().split("gitdir:", 1)[1].strip())
    assert not (git_dir / "rebase-merge").exists(), "rebase-merge must be cleaned up"
    assert not (git_dir / "rebase-apply").exists(), "rebase-apply must be cleaned up"


@pytest.mark.asyncio
async def test_no_worktree_param_backward_compat(tmp_path: Path) -> None:
    """Existing callers WITHOUT worktree param must still work (no rebase path)."""
    repo = tmp_path / "target"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    (repo / "base.txt").write_text("base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")

    _git(repo, "branch", "integration/x")
    _git(repo, "checkout", "-b", "feature/s1", "integration/x")
    (repo / "work.txt").write_text("work\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "feat(s1)")
    _git(repo, "checkout", "integration/x")

    sha = await _ff_merge_to_integration(
        target_project=repo,
        integration_branch="integration/x",
        feature_branch="feature/s1",
        # worktree intentionally omitted — backward compat
    )
    assert sha
    assert (repo / "work.txt").exists()
