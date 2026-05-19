"""NEW-14 — PRE_COMMIT_ALLOW_NO_CONFIG=1 must reach the stage5 / pre-merge
recovery ``git commit`` subprocesses.

NEW-12 (v5) injected the flag into the *worker* subprocess env. But the stage5
completeness subscriber (Patch S) and the pre-merge commit-recovery helper
(Patch R) spawn their OWN ``git commit`` subprocesses straight from the
orchestrator process — those inherit a plain ``os.environ`` without the flag.
In a worktree that ships a pre-commit git hook but no ``.pre-commit-config.yaml``
the hook aborts the commit (``stage5_recovery_failed`` on Antares 1.4).

NEW-14 routes both recovery paths through :func:`_git_commit_env`. These tests
reproduce the exact code paths and FAIL on current ``main`` (no ``env=`` kwarg).

2 unit (_git_commit_env helper) + 1 integration (real git, config-less hook).
See spec/spec_pilot_findings_closure_v6.md §2.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bmad_orchestrator.runtime.commit_recovery import recover_pre_merge
from bmad_orchestrator.runtime.git_env import GIT_COMMIT_ENV_INJECTED, _git_commit_env
from bmad_orchestrator.runtime.stage5_completeness import (
    Stage5CompletenessPolicy,
    recover_uncommitted,
)

# The pre-commit git hook installed by ``pre-commit install`` aborts the commit
# when there is no config file — UNLESS PRE_COMMIT_ALLOW_NO_CONFIG is set. This
# stub reproduces exactly that behaviour without needing pre-commit installed.
_NO_CONFIG_HOOK = """#!/bin/sh
if [ -z "$PRE_COMMIT_ALLOW_NO_CONFIG" ] && [ ! -f .pre-commit-config.yaml ]; then
  echo "git commit: No .pre-commit-config.yaml file was found" >&2
  echo "- To temporarily silence this, run PRE_COMMIT_ALLOW_NO_CONFIG=1 git ..." >&2
  exit 1
fi
exit 0
"""


# ════════════════════════════════════════════════════════════════════════════
# Unit — _git_commit_env helper (2)
# ════════════════════════════════════════════════════════════════════════════


def test_git_commit_env_helper_injects_flag() -> None:
    """The helper returns an env mapping carrying PRE_COMMIT_ALLOW_NO_CONFIG=1."""
    env = _git_commit_env()
    assert env["PRE_COMMIT_ALLOW_NO_CONFIG"] == "1"
    assert GIT_COMMIT_ENV_INJECTED["PRE_COMMIT_ALLOW_NO_CONFIG"] == "1"


def test_git_commit_env_helper_overlays_base_and_passes_through() -> None:
    """A caller-supplied base env is passed through; the injected flag wins."""
    base = {"FOO": "bar", "PRE_COMMIT_ALLOW_NO_CONFIG": "0"}
    env = _git_commit_env(base)
    assert env["FOO"] == "bar"  # passthrough
    assert env["PRE_COMMIT_ALLOW_NO_CONFIG"] == "1"  # injected overrides base


# ════════════════════════════════════════════════════════════════════════════
# Integration — real git worktree without .pre-commit-config.yaml (1)
# ════════════════════════════════════════════════════════════════════════════


def _init_repo_with_failing_hook(root: Path) -> None:
    """git init + identity + an initial commit + a config-less pre-commit hook."""
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "commit.gpgsign", "false"], check=True
    )
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-q", "-m", "seed"],
        check=True,
        env={"PRE_COMMIT_ALLOW_NO_CONFIG": "1", "PATH": _path_env()},
    )
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text(_NO_CONFIG_HOOK, encoding="utf-8")
    hook.chmod(0o755)


def _path_env() -> str:
    import os

    return os.environ.get("PATH", "/usr/bin:/bin")


@pytest.mark.asyncio
async def test_stage5_recovery_commits_in_config_less_worktree(tmp_path: Path) -> None:
    """stage5 recover_uncommitted commits in a worktree whose pre-commit hook
    aborts on a missing config — fails on current main (no env injection)."""
    repo = tmp_path / "wt"
    repo.mkdir()
    _init_repo_with_failing_hook(repo)
    # Uncommitted residue the worker forgot to commit.
    (repo / "residue.py").write_text("x = 1\n", encoding="utf-8")

    result = await recover_uncommitted(repo, Stage5CompletenessPolicy())

    assert result.recovered is True, f"stage5 recovery failed: {result.error}"
    assert result.commit_sha
    assert "git commit: No .pre-commit-config.yaml" not in result.error


@pytest.mark.asyncio
async def test_pre_merge_recovery_commits_in_config_less_worktree(
    tmp_path: Path,
) -> None:
    """Patch R recover_pre_merge commits residue in a config-less worktree —
    the second stage5 recovery code path NEW-14 must also cover."""
    repo = tmp_path / "wt"
    repo.mkdir()
    _init_repo_with_failing_hook(repo)
    (repo / "late.py").write_text("y = 2\n", encoding="utf-8")

    result = await recover_pre_merge(repo)

    assert result.recovered is True, f"pre-merge recovery failed: {result.error}"
    assert result.commit_sha
