"""NEW-25 — review spawn must find the ``/bmad-code-review`` skill.

pilot run #7 / replay 1.5 (2026-05-20), after the NEW-24 network fix: the
review spawn no longer hit ``ConnectionRefused`` but the inner ``claude -p``
answered ``Unknown command: /bmad-code-review`` → ``verdict=error``.

Root cause: the skill is reachable nowhere — the target project's
``.claude/skills/`` is gitignored (``git worktree add`` leaves it empty) and
the ``isolated_home`` overlay's ``~/.claude/skills/`` lacks it. The fix copies
the orchestrator's embedded ``skills/upstream/bmad-code-review`` into the
worktree as a project-level skill.
"""

from __future__ import annotations

from pathlib import Path

from bmad_orchestrator.agent.run import _ensure_review_skill_in_worktree


def test_skill_injected_into_empty_worktree(tmp_path: Path) -> None:
    """An empty worktree gets a working bmad-code-review skill."""
    worktree = tmp_path / "wt-1.5"
    worktree.mkdir()

    _ensure_review_skill_in_worktree(str(worktree))

    skill_md = worktree / ".claude" / "skills" / "bmad-code-review" / "SKILL.md"
    assert skill_md.is_file(), "bmad-code-review/SKILL.md must be present"
    assert skill_md.read_text(encoding="utf-8").strip()


def test_injection_is_idempotent(tmp_path: Path) -> None:
    """A second call must not raise or clobber an already-present skill."""
    worktree = tmp_path / "wt-1.5"
    worktree.mkdir()

    _ensure_review_skill_in_worktree(str(worktree))
    # Second call — skill already there.
    _ensure_review_skill_in_worktree(str(worktree))

    skill_dir = worktree / ".claude" / "skills" / "bmad-code-review"
    assert skill_dir.is_dir()


def test_embedded_skill_source_exists() -> None:
    """The embedded skill the fix copies from must actually ship in the repo."""
    package_root = Path(__file__).resolve().parents[1]
    src_skill = package_root / "skills" / "upstream" / "bmad-code-review"
    assert src_skill.is_dir(), f"embedded skill missing: {src_skill}"
    assert (src_skill / "SKILL.md").is_file()
