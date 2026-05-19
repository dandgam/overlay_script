"""NEW-27 (S3) — the isolated_home overlay must not carry host user skills.

``_create_isolated_home`` snapshots ``~/.claude/`` into a per-worker overlay.
If it copies ``~/.claude/skills/``, a worker's ``claude`` can resolve whatever
skill the operator happens to have installed — not project-agnostic, and a leak
of the same class as NEW-26. The overlay's ``.claude/skills/`` must be an empty
placeholder; Virgil's own skills are injected project-level into the worktree.
"""

from __future__ import annotations

from pathlib import Path

from bmad_orchestrator.runtime.worker_spawn import (
    _cleanup_isolated_home,
    _create_isolated_home,
)


def _fake_host_home(tmp_path: Path) -> Path:
    """A host home with a foreign user-level skill and other ~/.claude state."""
    home = tmp_path / "host-home"
    claude = home / ".claude"
    (claude / "skills" / "foreign-skill").mkdir(parents=True)
    (claude / "skills" / "foreign-skill" / "SKILL.md").write_text(
        "---\nname: foreign-skill\n---\nfrom the operator's machine\n",
        encoding="utf-8",
    )
    (claude / "settings.json").write_text("{}", encoding="utf-8")
    (home / ".claude.json").write_text("{}", encoding="utf-8")
    return home


def test_overlay_skills_dir_is_empty(tmp_path: Path) -> None:
    """The overlay's ``.claude/skills/`` carries none of the host's skills."""
    home = _fake_host_home(tmp_path)
    overlay = _create_isolated_home(worker_label="t", host_home=home)
    try:
        skills = overlay / ".claude" / "skills"
        # The dir exists (placeholder so claude does not EROFS) ...
        assert skills.is_dir(), "overlay must keep an empty skills placeholder"
        # ... but holds nothing from the host.
        assert list(skills.iterdir()) == [], (
            f"overlay leaked host user skills: {list(skills.iterdir())}"
        )
    finally:
        _cleanup_isolated_home(overlay)


def test_overlay_still_copies_other_claude_state(tmp_path: Path) -> None:
    """Stripping skills must not drop the rest of ~/.claude (e.g. settings)."""
    home = _fake_host_home(tmp_path)
    overlay = _create_isolated_home(worker_label="t", host_home=home)
    try:
        assert (overlay / ".claude" / "settings.json").is_file()
        assert (overlay / ".claude.json").is_file()
    finally:
        _cleanup_isolated_home(overlay)
