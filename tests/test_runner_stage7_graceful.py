"""#2 NEW-2 Layer A — runner-side graceful Stage 7 cleanup (2026-05-19).

Exercises the ``stage7_cleanup_feature_branch`` bash function inside
``skills/upstream/bmad-auto-dev/scripts/bmad-auto-dev-runner.sh`` directly: the
function source is extracted from the real runner so the tests pin the shipped
behaviour, not a copy.

Scenarios:
  1. clean cleanup — branch not held by a worktree ⇒ ``git branch -d`` deletes it;
  2. reused worktree — branch checked out elsewhere ⇒ skip + structured log;
  3. synthetic verdict — held branch with commits past base ⇒ ``verdict=approve``
     claude_event printed to stdout;
  4. no synthetic verdict — held branch with zero commits past base ⇒ skip only;
  5. env override — ``BMAD_RUNNER_SKIP_STAGE7=1`` skips unconditionally.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

_RUNNER = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "upstream"
    / "bmad-auto-dev"
    / "scripts"
    / "bmad-auto-dev-runner.sh"
)


def _extract_stage7_function() -> str:
    """Pull the ``stage7_cleanup_feature_branch`` definition out of the runner."""
    text = _RUNNER.read_text(encoding="utf-8")
    m = re.search(
        r"^stage7_cleanup_feature_branch\(\) \{.*?^\}",
        text,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert m is not None, "stage7_cleanup_feature_branch not found in runner.sh"
    return m.group(0)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        },
    ).stdout


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    (path / "README.md").write_text("hi\n")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "init")
    # integration branch == main tip.
    _git(path, "branch", "integration/test")


def _make_feature(path: Path, *, with_commit: bool) -> None:
    """Create ``feature/s1``; optionally add one commit past the main tip."""
    _git(path, "branch", "feature/s1")
    if with_commit:
        _git(path, "checkout", "-q", "feature/s1")
        (path / "work.txt").write_text("story work\n")
        _git(path, "add", "work.txt")
        _git(path, "commit", "-q", "-m", "story s1 work")
        _git(path, "checkout", "-q", "main")


def _run_stage7(repo: Path, branch: str, integ: str, *, env_skip: bool = False) -> str:
    """Invoke the extracted function inside ``repo``; return combined output."""
    harness = (
        "set -euo pipefail\n"
        "log()  { printf 'log %s\\n' \"$*\"; }\n"
        "warn() { printf 'WARN %s\\n' \"$*\" >&2; }\n"
        f"{_extract_stage7_function()}\n"
        'stage7_cleanup_feature_branch "$1" "$2"\n'
    )
    env = {**os.environ}
    if env_skip:
        env["BMAD_RUNNER_SKIP_STAGE7"] = "1"
    else:
        env.pop("BMAD_RUNNER_SKIP_STAGE7", None)
    proc = subprocess.run(
        ["bash", "-c", harness, "bash", branch, integ],
        cwd=str(repo),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, f"stage7 returned {proc.returncode}: {proc.stderr}"
    return proc.stdout + proc.stderr


def _branch_exists(repo: Path, branch: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", str(repo), "show-ref", "--verify", "--quiet",
             f"refs/heads/{branch}"],
        ).returncode
        == 0
    )


def test_clean_cleanup_deletes_unheld_branch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _make_feature(repo, with_commit=False)  # merged into main tip ⇒ -d works
    out = _run_stage7(repo, "feature/s1", "integration/test")
    assert "stage7 feature branch deleted branch=feature/s1" in out
    assert not _branch_exists(repo, "feature/s1")


def test_reused_worktree_skips_with_structured_log(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _make_feature(repo, with_commit=False)
    wt = tmp_path / "wt-s1"
    _git(repo, "worktree", "add", str(wt), "feature/s1")
    out = _run_stage7(repo, "feature/s1", "integration/test")
    assert "stage7_skipped reason=used_by_worktree" in out
    assert "branch=feature/s1" in out
    assert f"worktree={wt}" in out
    # skip means the branch survives for the held worktree.
    assert _branch_exists(repo, "feature/s1")


def test_synthetic_verdict_emitted_when_commits_past_base(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _make_feature(repo, with_commit=True)
    wt = tmp_path / "wt-s1"
    _git(repo, "worktree", "add", str(wt), "feature/s1")
    out = _run_stage7(repo, "feature/s1", "integration/test")
    assert "stage7_skipped reason=used_by_worktree" in out
    assert '"verdict":"approve"' in out
    assert '"source":"runner_stage7_skip"' in out
    assert '"commits":1' in out
    assert '"branch":"feature/s1"' in out


def test_no_synthetic_verdict_when_zero_commits(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _make_feature(repo, with_commit=False)  # feature == base, 0 commits past
    wt = tmp_path / "wt-s1"
    _git(repo, "worktree", "add", str(wt), "feature/s1")
    out = _run_stage7(repo, "feature/s1", "integration/test")
    assert "stage7_skipped reason=used_by_worktree" in out
    assert '"verdict":"approve"' not in out


def test_env_override_skips_unconditionally(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _make_feature(repo, with_commit=False)
    # No worktree holds the branch, yet the env flag forces a skip.
    out = _run_stage7(repo, "feature/s1", "integration/test", env_skip=True)
    assert "stage7_skipped reason=env_override branch=feature/s1" in out
    assert _branch_exists(repo, "feature/s1")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
