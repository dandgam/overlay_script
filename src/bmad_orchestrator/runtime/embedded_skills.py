"""Embedded skills overlay applier (spec §4 E3).

Caller-side helper invoked from `spawn_worker` before subprocess launch.
Copies pristine `skills/upstream/<name>/` into `<worktree>/.claude/skills/<name>/`,
then layers per-skill `customize/<name>.customize.toml` overlays on top.

Safety invariants:

* `worktree` MUST resolve inside `allowed_worktree_root` — path-traversal guard
  (FS9 H2 pattern). The applier refuses to write into the project's main
  `.claude/skills/` directory or anywhere else outside the worktree tree.
* Source symlinks are scanned BEFORE copy; any symlink whose target escapes the
  upstream root raises :class:`SymlinkEscapeError` and aborts the entire apply.
* Customize TOML is loaded via :mod:`skills_repo` — fail-loud on schema errors
  (typo'ed key = build break, not silent drop).
* Overlay semantics (minimum viable, expand in follow-up sessions):
    - ``enabled = false``      → skill not copied.
    - ``description_override`` → replace first ``description:`` line in
      SKILL.md YAML frontmatter.
    - ``body_overlay``         → markdown appended after SKILL.md body.
    - ``extra_triggers`` / ``variables`` are recorded but not yet applied
      (deferred — they need richer SKILL.md parsing).

Worker subprocesses see only the copied + overlaid skills in their worktree;
the project's main `.claude/skills/` is never touched.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from bmad_orchestrator.skills_repo import (
    EMBEDDED_SKILL_NAMES,
    Customize,
    CustomizeNotFoundError,
    load_customize,
)

SKILL_DIR_NAME = ".claude"
_FRONTMATTER_DESC_RE = re.compile(r"(?m)^description:.*$")


class EmbeddedSkillsError(Exception):
    """Base for embedded_skills errors."""


class WorktreeOutOfRootError(EmbeddedSkillsError):
    """Worktree path is not contained strictly inside allowed_worktree_root."""


class UpstreamSourceMissingError(EmbeddedSkillsError):
    """`skills/upstream/` source directory missing or empty."""


class SymlinkEscapeError(EmbeddedSkillsError):
    """A source symlink targets a path outside the upstream root — refuse."""


@dataclass(slots=True)
class ApplyResult:
    """Outcome of one apply call — quoted by caller in audit/JSONL events."""

    skills_applied: list[str] = field(default_factory=list)
    skills_skipped_disabled: list[str] = field(default_factory=list)
    files_written: int = 0
    overlays_applied: int = 0
    target_root: Path | None = None


def _validate_worktree(worktree: Path, allowed_root: Path) -> Path:
    """Resolve and confirm `worktree` is strictly inside `allowed_root`.

    Equal-path raises — overlay must land in a child directory, never the
    root itself.
    """
    wt = worktree.resolve()
    root = allowed_root.resolve()
    if wt == root:
        raise WorktreeOutOfRootError(
            f"worktree equals allowed root: {wt}; refusing to apply skills"
        )
    try:
        wt.relative_to(root)
    except ValueError as exc:
        raise WorktreeOutOfRootError(
            f"worktree {wt} is not under allowed root {root}"
        ) from exc
    return wt


def _scan_for_symlink_escape(src_root: Path) -> None:
    """Walk `src_root` and refuse if any symlink target escapes the tree.

    Symlinks pointing inside `src_root` are allowed (rare but legitimate —
    a skill may reference shared assets). Symlinks resolving outside are
    blocked: in a worker subprocess they could expose host filesystem state.
    """
    root_real = src_root.resolve()
    for child in src_root.rglob("*"):
        if not child.is_symlink():
            continue
        target_real = child.resolve()
        try:
            target_real.relative_to(root_real)
        except ValueError as exc:
            raise SymlinkEscapeError(
                f"symlink {child} → {target_real} escapes upstream root {root_real}"
            ) from exc


def _copytree_fresh(src: Path, dst: Path) -> int:
    """Copy `src` directory tree → `dst`, replacing `dst` if present.

    Symlinks inside `src` are preserved (`symlinks=True`); upstream contract
    was verified by :func:`_scan_for_symlink_escape` upstream of this call.
    Returns the count of regular files written.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=True)
    return sum(1 for p in dst.rglob("*") if p.is_file())


def _apply_overlay(skill_dir: Path, customize: Customize) -> int:
    """Apply customize TOML overlay onto the freshly-copied SKILL.md.

    Returns number of overlay ops actually performed (0 when customize
    leaves both `description_override` and `body_overlay` at defaults).
    """
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return 0
    text = skill_md.read_text(encoding="utf-8")
    ops = 0

    if customize.description_override is not None:
        new = _FRONTMATTER_DESC_RE.sub(
            f"description: {customize.description_override}",
            text,
            count=1,
        )
        if new != text:
            text = new
            ops += 1

    if customize.body_overlay is not None and customize.body_overlay.strip():
        text = text.rstrip() + "\n\n" + customize.body_overlay.rstrip() + "\n"
        ops += 1

    if ops > 0:
        skill_md.write_text(text, encoding="utf-8")
    return ops


def apply_embedded_skills(
    *,
    worktree: Path | str,
    skills_resolution_root: Path | str,
    allowed_worktree_root: Path | str,
) -> ApplyResult:
    """Copy `skills/upstream/` into `<worktree>/.claude/skills/`, layer overlays.

    Args:
        worktree: target worktree directory; receives `.claude/skills/`.
        skills_resolution_root: orchestrator's ``skills/`` dir (must contain
            ``upstream/`` and optionally ``customize/``).
        allowed_worktree_root: parent dir under which `worktree` must live
            (typically ``<target>/.worktrees``). Path-traversal guard.

    Raises:
        WorktreeOutOfRootError: `worktree` is not strictly inside
            `allowed_worktree_root`.
        UpstreamSourceMissingError: `skills_resolution_root/upstream/` missing.
        SymlinkEscapeError: any upstream symlink escapes its root.
    """
    wt = _validate_worktree(Path(worktree), Path(allowed_worktree_root))
    skills_root = Path(skills_resolution_root)
    upstream = skills_root / "upstream"
    if not upstream.is_dir():
        raise UpstreamSourceMissingError(
            f"upstream directory missing: {upstream}"
        )

    _scan_for_symlink_escape(upstream)

    target_skills_root = wt / SKILL_DIR_NAME / "skills"
    target_skills_root.mkdir(parents=True, exist_ok=True)

    result = ApplyResult(target_root=target_skills_root)

    for name in sorted(EMBEDDED_SKILL_NAMES):
        src = upstream / name
        if not src.is_dir():
            continue
        try:
            customize = load_customize(name, root=skills_root)
        except CustomizeNotFoundError:
            customize = Customize()
        # CustomizeInvalidError intentionally propagates — broken stub = build break.

        if not customize.enabled:
            result.skills_skipped_disabled.append(name)
            continue

        dst = target_skills_root / name
        result.files_written += _copytree_fresh(src, dst)
        result.overlays_applied += _apply_overlay(dst, customize)
        result.skills_applied.append(name)

    return result


__all__ = [
    "ApplyResult",
    "EmbeddedSkillsError",
    "SymlinkEscapeError",
    "UpstreamSourceMissingError",
    "WorktreeOutOfRootError",
    "apply_embedded_skills",
]
