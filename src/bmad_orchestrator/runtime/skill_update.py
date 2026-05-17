"""Skill upgrade pipeline (spec §4 E4).

Pulls a fresh copy of upstream BMad skills, computes a diff vs the current
``skills/upstream/`` tree, re-applies any ``skills/patches/*.diff`` files on top
of the new upstream, and (when not in dry-run mode) swaps the upstream tree +
updates ``skills/upstream/.bmad-version``.

Hard invariants:

* ``skills/customize/``, ``skills/policy/``, ``skills/lessons/`` are NEVER
  touched. The updater only mutates ``skills/upstream/`` and (on conflict)
  writes a fresh report file ``skills/upstream-conflicts-<ts>.md``.
* Default mode is **dry-run** — caller must opt in with ``dry_run=False``.
* On any patch conflict during ``--apply`` the updater raises
  :class:`PatchConflictError` AFTER writing the report. The on-disk
  ``skills/upstream/`` tree is restored from a pre-swap backup, so a failed
  apply leaves the repo in its original state.
* Source git rev is read best-effort via ``git -C <source> rev-parse HEAD``;
  if the source path is not inside a git repo, the previous rev is preserved.

See :mod:`bmad_orchestrator.runtime.embedded_skills` for the runtime-side
applier that copies the result of this updater into worker worktrees.
"""

from __future__ import annotations

import datetime as _dt
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

_REQUIRED_VERSION_FIELDS: tuple[str, ...] = (
    "source_path",
    "source_repo",
    "source_git_rev",
    "source_git_date",
    "copied_at",
    "copied_by",
    "skills_count",
    "skills",
)


class SkillUpdateError(Exception):
    """Base for skill_update errors."""


class BmadVersionNotFoundError(SkillUpdateError):
    """``skills/upstream/.bmad-version`` missing."""


class BmadVersionInvalidError(SkillUpdateError):
    """``.bmad-version`` failed YAML parse or schema validation."""


class SourceMissingError(SkillUpdateError):
    """Upstream source path missing or not a directory."""


class PatchConflictError(SkillUpdateError):
    """One or more patches failed to apply on the candidate upstream tree."""


class BmadVersion(BaseModel):
    """Schema for ``skills/upstream/.bmad-version``."""

    model_config = ConfigDict(extra="allow")

    source_path: str
    source_repo: str
    source_git_rev: str
    source_git_date: str
    copied_at: str
    copied_by: str
    skills_count: int
    skills: list[str]


@dataclass(slots=True)
class DiffSummary:
    """File-level diff between current and candidate upstream trees."""

    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.modified)


@dataclass(slots=True)
class PatchResult:
    """Outcome of one patch apply attempt."""

    patch_name: str
    status: str  # "ok" | "conflict"
    detail: str = ""


@dataclass(slots=True)
class UpdateResult:
    """Outcome of one ``update_skills`` call — quoted by CLI + audit."""

    diff: DiffSummary
    patch_results: list[PatchResult]
    applied: bool
    source_path: Path
    source_git_rev: str
    conflict_report: Path | None = None

    @property
    def conflicts(self) -> list[PatchResult]:
        return [r for r in self.patch_results if r.status == "conflict"]


# ── version file IO ─────────────────────────────────────────────────────────


def version_file_path(skills_root: Path) -> Path:
    """Return canonical path to ``<skills_root>/upstream/.bmad-version``."""
    return skills_root / "upstream" / ".bmad-version"


def read_bmad_version(skills_root: Path) -> BmadVersion:
    """Load + validate the ``.bmad-version`` file under ``skills_root``."""
    path = version_file_path(skills_root)
    if not path.exists():
        raise BmadVersionNotFoundError(f".bmad-version not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise BmadVersionInvalidError(f"YAML parse error in {path}: {e}") from e
    if not isinstance(raw, dict):
        raise BmadVersionInvalidError(
            f".bmad-version must be a mapping in {path}, got {type(raw).__name__}"
        )
    missing = [k for k in _REQUIRED_VERSION_FIELDS if k not in raw]
    if missing:
        raise BmadVersionInvalidError(
            f"missing required keys in {path}: {sorted(missing)}"
        )
    try:
        return BmadVersion.model_validate(raw)
    except ValidationError as e:
        raise BmadVersionInvalidError(f"schema validation failed for {path}: {e}") from e


def write_bmad_version(skills_root: Path, version: BmadVersion) -> Path:
    """Persist ``version`` as YAML to the canonical path; returns the path."""
    path = version_file_path(skills_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = version.model_dump()
    # yaml.safe_dump emits keys alphabetically by default; force insertion order
    # so the file diff stays human-readable across updates.
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


# ── source resolution + diff ────────────────────────────────────────────────


def _resolve_source(source: Path | None, version: BmadVersion) -> Path:
    candidate = source if source is not None else Path(version.source_path)
    if not candidate.exists() or not candidate.is_dir():
        raise SourceMissingError(f"upstream source missing or not a directory: {candidate}")
    return candidate


def _relative_files(root: Path) -> dict[str, Path]:
    if not root.exists():
        return {}
    files: dict[str, Path] = {}
    for child in root.rglob("*"):
        if not child.is_file():
            continue
        rel = child.relative_to(root).as_posix()
        files[rel] = child
    return files


def compute_diff(current: Path, candidate: Path) -> DiffSummary:
    """Compare regular files under ``current`` vs ``candidate``."""
    cur = _relative_files(current)
    new = _relative_files(candidate)
    cur_keys = set(cur)
    new_keys = set(new)
    added = sorted(new_keys - cur_keys)
    removed = sorted(cur_keys - new_keys)
    modified: list[str] = []
    for k in sorted(cur_keys & new_keys):
        try:
            if cur[k].read_bytes() != new[k].read_bytes():
                modified.append(k)
        except OSError:
            modified.append(k)
    return DiffSummary(added=added, removed=removed, modified=modified)


# ── patches ────────────────────────────────────────────────────────────────


def _list_patches(patches_dir: Path) -> list[Path]:
    if not patches_dir.exists():
        return []
    return sorted(p for p in patches_dir.iterdir() if p.is_file() and p.suffix == ".diff")


def _git_apply(target_dir: Path, patch: Path, *, check_only: bool) -> tuple[bool, str]:
    """Run ``git apply`` against ``target_dir``; return (ok, stderr)."""
    cmd = ["git", "apply"]
    if check_only:
        cmd.append("--check")
    cmd.append(str(patch.resolve()))
    proc = subprocess.run(  # noqa: S603 — own argv, no shell  # nosec
        cmd,
        cwd=str(target_dir),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        return True, ""
    return False, (proc.stderr or proc.stdout).strip()


def apply_patches_check(patches_dir: Path, target_dir: Path) -> list[PatchResult]:
    """Dry-run each patch against ``target_dir`` (does not modify it)."""
    results: list[PatchResult] = []
    for patch in _list_patches(patches_dir):
        ok, msg = _git_apply(target_dir, patch, check_only=True)
        results.append(
            PatchResult(
                patch_name=patch.name,
                status="ok" if ok else "conflict",
                detail="" if ok else msg,
            )
        )
    return results


def apply_patches(patches_dir: Path, target_dir: Path) -> list[PatchResult]:
    """Apply each patch in order; raise on first failure (results preserved)."""
    results: list[PatchResult] = []
    for patch in _list_patches(patches_dir):
        ok, msg = _git_apply(target_dir, patch, check_only=False)
        results.append(
            PatchResult(
                patch_name=patch.name,
                status="ok" if ok else "conflict",
                detail="" if ok else msg,
            )
        )
        if not ok:
            raise PatchConflictError(f"patch {patch.name} failed: {msg}")
    return results


# ── conflict report ─────────────────────────────────────────────────────────


def _format_timestamp(ts: _dt.datetime | None = None) -> str:
    moment = ts if ts is not None else _dt.datetime.now(_dt.UTC)
    return moment.strftime("%Y%m%dT%H%M%SZ")


def write_conflict_report(
    skills_root: Path,
    results: list[PatchResult],
    source: Path,
    source_rev: str,
    *,
    ts: _dt.datetime | None = None,
) -> Path:
    """Write a markdown report of patch conflicts. Returns the file path."""
    stamp = _format_timestamp(ts)
    path = skills_root / f"upstream-conflicts-{stamp}.md"
    conflicts = [r for r in results if r.status == "conflict"]
    lines: list[str] = [
        f"# Upstream patch conflicts — {stamp}",
        "",
        f"- **source:** `{source}`",
        f"- **source_git_rev:** `{source_rev}`",
        f"- **patches attempted:** {len(results)}",
        f"- **conflicts:** {len(conflicts)}",
        "",
        "## Conflicts",
        "",
    ]
    if not conflicts:
        lines.append("(none — all patches applied cleanly)")
    else:
        for r in conflicts:
            lines.append(f"### {r.patch_name}")
            lines.append("")
            lines.append("```text")
            lines.append(r.detail or "(no detail)")
            lines.append("```")
            lines.append("")
    lines.append("## All results")
    lines.append("")
    for r in results:
        lines.append(f"- {r.patch_name}: {r.status}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ── git rev helper ──────────────────────────────────────────────────────────


def _git_rev(source: Path) -> str:
    """Best-effort: return short HEAD sha of repo containing ``source``."""
    proc = subprocess.run(  # noqa: S603 — own argv, no shell  # nosec
        ["git", "-C", str(source), "rev-parse", "HEAD"],  # noqa: S607 — git on PATH
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        return proc.stdout.strip()
    return ""


# ── top-level orchestrator ──────────────────────────────────────────────────


def update_skills(
    skills_root: Path,
    source: Path | None = None,
    *,
    dry_run: bool = True,
    ts: _dt.datetime | None = None,
    updated_by: str = "skill-update CLI",
) -> UpdateResult:
    """Pull upstream, diff, re-apply patches; optionally swap the on-disk tree.

    Args:
        skills_root: Path to ``skills/`` directory (contains ``upstream/`` +
            ``patches/`` + ``customize/`` + ``policy/`` + ``lessons/``).
        source: Override for upstream source. Defaults to the ``source_path``
            recorded in ``.bmad-version``.
        dry_run: When True (default), no on-disk changes are made beyond an
            optional conflict report. When False, ``skills/upstream/`` is
            replaced and ``.bmad-version`` is rewritten.
        ts: Optional explicit timestamp (for deterministic tests).
        updated_by: String stamped into the new ``.bmad-version``.

    Returns:
        :class:`UpdateResult` describing the diff, patch results, and
        whether changes were applied.

    Raises:
        BmadVersionNotFoundError / BmadVersionInvalidError on bad version file.
        SourceMissingError when the source path is absent.
        PatchConflictError when ``dry_run=False`` and at least one patch fails.
    """
    version = read_bmad_version(skills_root)
    src = _resolve_source(source, version)
    current_upstream = skills_root / "upstream"
    diff = compute_diff(current_upstream, src)
    patches_dir = skills_root / "patches"

    if dry_run:
        results = apply_patches_check(patches_dir, src)
        report: Path | None = None
        if any(r.status == "conflict" for r in results):
            report = write_conflict_report(
                skills_root, results, src, _git_rev(src) or version.source_git_rev, ts=ts
            )
        return UpdateResult(
            diff=diff,
            patch_results=results,
            applied=False,
            source_path=src,
            source_git_rev=_git_rev(src) or version.source_git_rev,
            conflict_report=report,
        )

    backup = current_upstream.with_name("upstream.backup")
    if backup.exists():
        shutil.rmtree(backup)
    moved_to_backup = False
    if current_upstream.exists():
        shutil.move(str(current_upstream), str(backup))
        moved_to_backup = True
    conflict_report: Path | None = None
    try:
        shutil.copytree(src, current_upstream, symlinks=True)
        try:
            results = apply_patches(patches_dir, current_upstream)
        except PatchConflictError as exc:
            partial = apply_patches_check(patches_dir, src)
            conflict_report = write_conflict_report(
                skills_root, partial, src, _git_rev(src) or version.source_git_rev, ts=ts
            )
            exc.add_note(f"conflict report written to {conflict_report}")
            raise
    except Exception:
        if current_upstream.exists():
            shutil.rmtree(current_upstream)
        if moved_to_backup and backup.exists():
            shutil.move(str(backup), str(current_upstream))
        raise
    if moved_to_backup and backup.exists():
        shutil.rmtree(backup)

    new_rev = _git_rev(src) or version.source_git_rev
    new_version = BmadVersion(
        source_path=str(src),
        source_repo=version.source_repo,
        source_git_rev=new_rev,
        source_git_date=(ts or _dt.datetime.now(_dt.UTC)).date().isoformat(),
        copied_at=(ts or _dt.datetime.now(_dt.UTC)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        copied_by=updated_by,
        skills_count=version.skills_count,
        skills=list(version.skills),
    )
    write_bmad_version(skills_root, new_version)

    return UpdateResult(
        diff=diff,
        patch_results=results,
        applied=True,
        source_path=src,
        source_git_rev=new_rev,
    )


# ── status helper (CLI surfaces) ────────────────────────────────────────────


def skill_status(skills_root: Path) -> dict[str, Any]:
    """Snapshot of current upstream version + applied patches + pending conflicts."""
    try:
        version = read_bmad_version(skills_root)
        version_info: dict[str, Any] | None = version.model_dump()
    except BmadVersionNotFoundError:
        version_info = None
    patches_dir = skills_root / "patches"
    patches = [p.name for p in _list_patches(patches_dir)]
    pending = sorted(
        p.name
        for p in skills_root.glob("upstream-conflicts-*.md")
        if p.is_file()
    )
    return {
        "version": version_info,
        "patches": patches,
        "pending_conflicts": pending,
    }
