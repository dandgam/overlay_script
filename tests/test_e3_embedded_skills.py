"""E3 acceptance tests — embedded_skills overlay applier + spawn_worker wiring.

См. spec/spec_embed_phase45_with_selflearning.md §4 E3.

Coverage:
- apply_embedded_skills: copy logic, customize merge, overlay semantics.
- Path-traversal guards: worktree outside allowed root → refuse.
- Symlink safety (FS9 H2): escape detection + in-tree symlink ok.
- Spawn_worker integration: opt-in via embedded_skills_root, JSONL event,
  backward compat when not opted in.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bmad_orchestrator.runtime.embedded_skills import (
    ApplyResult,
    SymlinkEscapeError,
    UpstreamSourceMissingError,
    WorktreeOutOfRootError,
    apply_embedded_skills,
)
from bmad_orchestrator.runtime.worker_spawn import spawn_worker
from bmad_orchestrator.skills_repo import EMBEDDED_SKILL_NAMES

PRODUCTION_SKILLS_ROOT = Path(__file__).resolve().parent.parent / "skills"


def _build_skills_fixture(root: Path, skill_names: list[str] | None = None) -> Path:
    """Build minimal `skills/` overlay fixture with N upstream skills + customize stubs.

    Each skill has SKILL.md with YAML frontmatter + body; matching customize stub
    is an empty TOML file (defaults apply).
    """
    names = skill_names if skill_names is not None else sorted(EMBEDDED_SKILL_NAMES)
    upstream = root / "upstream"
    customize = root / "customize"
    upstream.mkdir(parents=True)
    customize.mkdir(parents=True)
    for name in names:
        skill_dir = upstream / name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: stub for {name}\n---\n\n"
            f"# {name}\n\nBody text.\n",
            encoding="utf-8",
        )
        (customize / f"{name}.customize.toml").write_text("", encoding="utf-8")
    return root


def _make_worktree(parent: Path, name: str = "wt-1") -> Path:
    """Build a `.worktrees/<name>/` dir under `parent`; returns the worktree path."""
    wt_root = parent / ".worktrees"
    wt_root.mkdir(parents=True, exist_ok=True)
    wt = wt_root / name
    wt.mkdir()
    return wt


# ─────────────────── 1-3. Happy path — copy + structure ───────────────────


def test_apply_embedded_skills_copies_14_canonical_skills(tmp_path: Path) -> None:
    skills_root = _build_skills_fixture(tmp_path / "skills")
    wt = _make_worktree(tmp_path / "target")
    result = apply_embedded_skills(
        worktree=wt,
        skills_resolution_root=skills_root,
        allowed_worktree_root=tmp_path / "target" / ".worktrees",
    )
    assert isinstance(result, ApplyResult)
    assert sorted(result.skills_applied) == sorted(EMBEDDED_SKILL_NAMES)
    assert len(result.skills_applied) == 14
    assert result.skills_skipped_disabled == []
    # Target dir laid out at <worktree>/.claude/skills/<name>/SKILL.md
    for name in EMBEDDED_SKILL_NAMES:
        assert (wt / ".claude" / "skills" / name / "SKILL.md").is_file()


def test_apply_embedded_skills_returns_target_root_inside_worktree(tmp_path: Path) -> None:
    skills_root = _build_skills_fixture(tmp_path / "skills", skill_names=["bmad-auto-dev"])
    wt = _make_worktree(tmp_path / "target")
    result = apply_embedded_skills(
        worktree=wt,
        skills_resolution_root=skills_root,
        allowed_worktree_root=tmp_path / "target" / ".worktrees",
    )
    assert result.target_root == wt / ".claude" / "skills"
    assert result.target_root.is_dir()


# ─────────────────── 4-6. Overlay — description_override + body_overlay ───────────────────


def test_apply_embedded_skills_description_override_rewrites_frontmatter(tmp_path: Path) -> None:
    skills_root = _build_skills_fixture(tmp_path / "skills", skill_names=["bmad-auto-dev"])
    # Inject description override.
    (skills_root / "customize" / "bmad-auto-dev.customize.toml").write_text(
        'description_override = "AABIT-tweaked stub"\n', encoding="utf-8"
    )
    wt = _make_worktree(tmp_path / "target")
    result = apply_embedded_skills(
        worktree=wt,
        skills_resolution_root=skills_root,
        allowed_worktree_root=tmp_path / "target" / ".worktrees",
    )
    assert result.overlays_applied == 1
    txt = (wt / ".claude" / "skills" / "bmad-auto-dev" / "SKILL.md").read_text(encoding="utf-8")
    assert "description: AABIT-tweaked stub" in txt
    assert "description: stub for bmad-auto-dev" not in txt


def test_apply_embedded_skills_body_overlay_appends(tmp_path: Path) -> None:
    skills_root = _build_skills_fixture(tmp_path / "skills", skill_names=["bmad-dev-story"])
    (skills_root / "customize" / "bmad-dev-story.customize.toml").write_text(
        'body_overlay = """\n## Local addendum\n\nApply RLS audit.\n"""\n',
        encoding="utf-8",
    )
    wt = _make_worktree(tmp_path / "target")
    result = apply_embedded_skills(
        worktree=wt,
        skills_resolution_root=skills_root,
        allowed_worktree_root=tmp_path / "target" / ".worktrees",
    )
    assert result.overlays_applied == 1
    txt = (wt / ".claude" / "skills" / "bmad-dev-story" / "SKILL.md").read_text(encoding="utf-8")
    assert "Local addendum" in txt
    assert "RLS audit" in txt
    # Original body must survive — overlay is append, not replace.
    assert "Body text." in txt


def test_apply_embedded_skills_disabled_customize_skips_copy(tmp_path: Path) -> None:
    skills_root = _build_skills_fixture(
        tmp_path / "skills", skill_names=["bmad-auto-dev", "bmad-dev-story"]
    )
    (skills_root / "customize" / "bmad-auto-dev.customize.toml").write_text(
        "enabled = false\n", encoding="utf-8"
    )
    wt = _make_worktree(tmp_path / "target")
    result = apply_embedded_skills(
        worktree=wt,
        skills_resolution_root=skills_root,
        allowed_worktree_root=tmp_path / "target" / ".worktrees",
    )
    assert result.skills_applied == ["bmad-dev-story"]
    assert result.skills_skipped_disabled == ["bmad-auto-dev"]
    assert not (wt / ".claude" / "skills" / "bmad-auto-dev").exists()
    assert (wt / ".claude" / "skills" / "bmad-dev-story").is_dir()


# ─────────────────── 7-9. Path-traversal + worktree guards ───────────────────


def test_apply_embedded_skills_worktree_outside_root_raises(tmp_path: Path) -> None:
    skills_root = _build_skills_fixture(tmp_path / "skills", skill_names=["bmad-auto-dev"])
    rogue_wt = tmp_path / "elsewhere" / "wt"
    rogue_wt.mkdir(parents=True)
    allowed = tmp_path / "target" / ".worktrees"
    allowed.mkdir(parents=True)
    with pytest.raises(WorktreeOutOfRootError, match="not under allowed root"):
        apply_embedded_skills(
            worktree=rogue_wt,
            skills_resolution_root=skills_root,
            allowed_worktree_root=allowed,
        )


def test_apply_embedded_skills_worktree_equals_root_raises(tmp_path: Path) -> None:
    skills_root = _build_skills_fixture(tmp_path / "skills", skill_names=["bmad-auto-dev"])
    allowed = tmp_path / "target" / ".worktrees"
    allowed.mkdir(parents=True)
    with pytest.raises(WorktreeOutOfRootError, match="equals allowed root"):
        apply_embedded_skills(
            worktree=allowed,
            skills_resolution_root=skills_root,
            allowed_worktree_root=allowed,
        )


def test_apply_embedded_skills_missing_upstream_raises(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills-empty"
    skills_root.mkdir()
    # No upstream/ subdir created.
    wt = _make_worktree(tmp_path / "target")
    with pytest.raises(UpstreamSourceMissingError, match="upstream directory missing"):
        apply_embedded_skills(
            worktree=wt,
            skills_resolution_root=skills_root,
            allowed_worktree_root=tmp_path / "target" / ".worktrees",
        )


# ─────────────────── 10-11. Symlink safety (FS9 H2 pattern) ───────────────────


def test_apply_embedded_skills_symlink_escape_raises(tmp_path: Path) -> None:
    skills_root = _build_skills_fixture(tmp_path / "skills", skill_names=["bmad-auto-dev"])
    # Inject a symlink whose target escapes the upstream root.
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (skills_root / "upstream" / "bmad-auto-dev" / "leak").symlink_to(outside)
    wt = _make_worktree(tmp_path / "target")
    with pytest.raises(SymlinkEscapeError, match="escapes upstream root"):
        apply_embedded_skills(
            worktree=wt,
            skills_resolution_root=skills_root,
            allowed_worktree_root=tmp_path / "target" / ".worktrees",
        )


def test_apply_embedded_skills_in_tree_symlink_ok(tmp_path: Path) -> None:
    skills_root = _build_skills_fixture(tmp_path / "skills", skill_names=["bmad-auto-dev"])
    # Symlink targeting another file inside the same skill — allowed.
    target = skills_root / "upstream" / "bmad-auto-dev" / "shared.md"
    target.write_text("shared content", encoding="utf-8")
    (skills_root / "upstream" / "bmad-auto-dev" / "alias").symlink_to(target)
    wt = _make_worktree(tmp_path / "target")
    result = apply_embedded_skills(
        worktree=wt,
        skills_resolution_root=skills_root,
        allowed_worktree_root=tmp_path / "target" / ".worktrees",
    )
    assert "bmad-auto-dev" in result.skills_applied
    copied = wt / ".claude" / "skills" / "bmad-auto-dev" / "alias"
    assert copied.exists()


# ─────────────────── 12-14. Re-apply / overwrite / missing customize ───────────────────


def test_apply_embedded_skills_overwrites_existing_target(tmp_path: Path) -> None:
    skills_root = _build_skills_fixture(tmp_path / "skills", skill_names=["bmad-auto-dev"])
    wt = _make_worktree(tmp_path / "target")
    # Seed pre-existing skill content that would be stale.
    stale = wt / ".claude" / "skills" / "bmad-auto-dev"
    stale.mkdir(parents=True)
    (stale / "OLD.md").write_text("stale", encoding="utf-8")
    result = apply_embedded_skills(
        worktree=wt,
        skills_resolution_root=skills_root,
        allowed_worktree_root=tmp_path / "target" / ".worktrees",
    )
    assert "bmad-auto-dev" in result.skills_applied
    assert not (stale / "OLD.md").exists()
    assert (stale / "SKILL.md").is_file()


def test_apply_embedded_skills_missing_customize_uses_defaults(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    upstream = skills_root / "upstream" / "bmad-auto-dev"
    upstream.mkdir(parents=True)
    (upstream / "SKILL.md").write_text(
        "---\nname: bmad-auto-dev\ndescription: x\n---\n\nBody.\n", encoding="utf-8"
    )
    # No customize/ dir at all — defaults must still apply silently.
    wt = _make_worktree(tmp_path / "target")
    result = apply_embedded_skills(
        worktree=wt,
        skills_resolution_root=skills_root,
        allowed_worktree_root=tmp_path / "target" / ".worktrees",
    )
    assert result.skills_applied == ["bmad-auto-dev"]
    assert result.overlays_applied == 0


def test_apply_embedded_skills_invalid_customize_propagates(tmp_path: Path) -> None:
    from bmad_orchestrator.skills_repo import CustomizeInvalidError

    skills_root = _build_skills_fixture(tmp_path / "skills", skill_names=["bmad-auto-dev"])
    (skills_root / "customize" / "bmad-auto-dev.customize.toml").write_text(
        "rogue_field = true\n", encoding="utf-8"
    )
    wt = _make_worktree(tmp_path / "target")
    with pytest.raises(CustomizeInvalidError):
        apply_embedded_skills(
            worktree=wt,
            skills_resolution_root=skills_root,
            allowed_worktree_root=tmp_path / "target" / ".worktrees",
        )


def test_apply_embedded_skills_production_skills_dir_smoke(tmp_path: Path) -> None:
    """Smoke: real `<repo>/skills/` overlay must apply without error.

    Catches drift between skills_repo's EMBEDDED_SKILL_NAMES + the actual
    `skills/upstream/` contents shipped in the repo.
    """
    wt = _make_worktree(tmp_path / "target")
    result = apply_embedded_skills(
        worktree=wt,
        skills_resolution_root=PRODUCTION_SKILLS_ROOT,
        allowed_worktree_root=tmp_path / "target" / ".worktrees",
    )
    assert len(result.skills_applied) == 14
    assert result.files_written > 14  # each skill has multiple files
    for name in EMBEDDED_SKILL_NAMES:
        assert (wt / ".claude" / "skills" / name / "SKILL.md").is_file()


# ─────────────────── 15-20. spawn_worker integration ───────────────────


@pytest.fixture
def _runs_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Isolate runs/ so JSONL writes don't pollute target_project."""
    target = tmp_path / "target-runs"
    target.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target))
    monkeypatch.setenv("BMAD_CURRENT_WAVE", "test")
    return target


@pytest.mark.asyncio
async def test_spawn_worker_mock_with_embedded_skills_emits_event(
    tmp_path: Path, _runs_dir: Path
) -> None:
    skills_root = _build_skills_fixture(tmp_path / "skills", skill_names=["bmad-auto-dev"])
    wt = _make_worktree(tmp_path / "target")
    handle = await spawn_worker(
        worktree=str(wt),
        story_id="1-1",
        branch="feature/1-1",
        mock=True,
        embedded_skills_root=skills_root,
        allowed_worktree_root=tmp_path / "target" / ".worktrees",
    )
    assert handle.mock is True
    lines = handle.jsonl_path.read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines if line.strip()]
    types = [e["event_type"] for e in events]
    # embedded_skills_applied appears BEFORE worker_spawned.
    assert "embedded_skills_applied" in types
    idx_emb = types.index("embedded_skills_applied")
    idx_spawn = types.index("worker_spawned")
    assert idx_emb < idx_spawn
    emb = events[idx_emb]
    assert emb["skills_applied"] == ["bmad-auto-dev"]
    assert emb["files_written"] >= 1
    assert emb["target_root"].endswith(".claude/skills")


@pytest.mark.asyncio
async def test_spawn_worker_mock_without_embedded_skills_skips_event(
    tmp_path: Path, _runs_dir: Path
) -> None:
    """Backward-compat: existing callers without the new kwargs must not emit."""
    wt = tmp_path / "wt-no-skills"
    wt.mkdir()
    handle = await spawn_worker(
        worktree=str(wt),
        story_id="x",
        branch="feature/x",
        mock=True,
    )
    lines = handle.jsonl_path.read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines if line.strip()]
    types = [e["event_type"] for e in events]
    assert "embedded_skills_applied" not in types
    # worker_spawned + worker_completed still both emitted.
    assert types == ["worker_spawned", "worker_completed"]


@pytest.mark.asyncio
async def test_spawn_worker_embedded_skills_without_allowed_root_raises(
    tmp_path: Path, _runs_dir: Path
) -> None:
    skills_root = _build_skills_fixture(tmp_path / "skills", skill_names=["bmad-auto-dev"])
    wt = _make_worktree(tmp_path / "target")
    with pytest.raises(ValueError, match="allowed_worktree_root must be provided"):
        await spawn_worker(
            worktree=str(wt),
            story_id="x",
            branch="feature/x",
            mock=True,
            embedded_skills_root=skills_root,
        )


@pytest.mark.asyncio
async def test_spawn_worker_embedded_skills_worktree_escapes_raises(
    tmp_path: Path, _runs_dir: Path
) -> None:
    skills_root = _build_skills_fixture(tmp_path / "skills", skill_names=["bmad-auto-dev"])
    rogue_wt = tmp_path / "rogue"
    rogue_wt.mkdir()
    allowed = tmp_path / "target" / ".worktrees"
    allowed.mkdir(parents=True)
    with pytest.raises(WorktreeOutOfRootError):
        await spawn_worker(
            worktree=str(rogue_wt),
            story_id="x",
            branch="feature/x",
            mock=True,
            embedded_skills_root=skills_root,
            allowed_worktree_root=allowed,
        )


def test_settings_skills_resolution_root_default() -> None:
    """Settings exposes skills_resolution_root with orchestrator default."""
    from bmad_orchestrator.config import Settings

    s = Settings()
    assert s.skills_resolution_root.name == "skills"
    assert str(s.skills_resolution_root).endswith("bmad-orchestrator/skills")


def test_settings_skills_resolution_root_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from bmad_orchestrator.config import Settings

    override = tmp_path / "custom-skills"
    monkeypatch.setenv("ORCHESTRATOR_SKILLS_RESOLUTION_ROOT", str(override))
    s = Settings()
    assert s.skills_resolution_root == override


