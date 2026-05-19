"""NEW-27 (S1) — worktrees must live outside the target project tree.

A worker's ``claude`` resolves slash commands by walking up the directory tree
from its CWD. With worktrees nested under ``<target>/.worktrees/`` the worker
reaches ``<target>/.claude/skills/`` and resolves the *target project's* skills
instead of Virgil's — the NEW-26 trap. ``worktree_root`` now returns a root
outside every project tree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bmad_orchestrator.agent.tools._common import worktree_root
from bmad_orchestrator.config import Settings


def _settings(tmp_path: Path) -> Settings:
    target = tmp_path / "antares"
    target.mkdir()
    return Settings(target_project=target)


def test_worktree_root_is_outside_the_target_tree(tmp_path: Path) -> None:
    """The worktree root must not be a descendant of the target project."""
    settings = _settings(tmp_path)
    root = worktree_root(settings).resolve()
    target = settings.target_project.resolve()
    assert root != target and target not in root.parents, (
        f"worktree root {root} must not sit inside the target tree {target}"
    )


def test_worktree_root_has_no_dotclaude_ancestor(tmp_path: Path) -> None:
    """No ancestor of the worktree root may carry a ``.claude/skills`` dir.

    That is the discovery surface — if any ancestor has one, the worker can
    resolve a foreign skill.
    """
    settings = _settings(tmp_path)
    root = worktree_root(settings).resolve()
    for ancestor in [root, *root.parents]:
        assert not (ancestor / ".claude" / "skills").exists(), (
            f"{ancestor} carries .claude/skills — worktrees must not sit under it"
        )


def test_worktree_root_default_is_var_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Default root: ``/var/tmp/virgil-worktrees/<target-basename>``."""
    monkeypatch.delenv("ORCHESTRATOR_WORKTREE_ROOT", raising=False)
    settings = _settings(tmp_path)
    root = worktree_root(settings)
    assert root == Path("/var/tmp/virgil-worktrees") / settings.target_project.name


def test_worktree_root_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``ORCHESTRATOR_WORKTREE_ROOT`` is honoured verbatim."""
    override = tmp_path / "custom-wt-root"
    monkeypatch.setenv("ORCHESTRATOR_WORKTREE_ROOT", str(override))
    settings = _settings(tmp_path)
    assert worktree_root(settings) == override
