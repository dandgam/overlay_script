"""Project registry — multi-project orchestration (Initiative #3 Task 3.1-3.2).

A small yaml-backed registry that maps a project ``slug`` (e.g. ``antares``,
``odyssey``) to its on-disk path + BMad layout flavour. Auto-detects layout
from the project's ``_bmad/`` directory shape; tolerates missing files so
``scan`` / ``doctor`` can report degraded entries rather than crash.

Default registry path = ``<orchestrator_home>/config/projects.yaml``; overridable
via env ``BMAD_PROJECTS_REGISTRY=/abs/path`` for tests / parallel installations.

Used by:
* ``cli/main.py`` — new ``init`` / ``scan`` / ``doctor`` / ``resume`` commands
* ``runtime/multi_run.py`` (S8, Init #3 Task 3.3) — resolves slug → path before
  spawning per-project workers.

See spec §Initiative #3 Task 3.1-3.2 and memory
``project_backlog_orchestrator_project_agnostic``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

BmadLayout = Literal["bmm-v6", "odyssey-hybrid", "unknown", "not-bmad"]

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

DEFAULT_REGISTRY_RELPATH = Path("config") / "projects.yaml"
REGISTRY_ENV_VAR = "BMAD_PROJECTS_REGISTRY"


class ProjectRegistryError(ValueError):
    """Raised on malformed yaml, bad slug, or path validation failure."""


class ProjectEntry(BaseModel):
    """One project in the registry."""

    model_config = ConfigDict(extra="forbid", frozen=False)

    path: Path
    bmad_layout: BmadLayout = "unknown"
    sandbox_overrides: dict[str, Any] = Field(default_factory=dict)

    @field_validator("path")
    @classmethod
    def _absolute_path(cls, v: Path) -> Path:
        if not v.is_absolute():
            raise ValueError(f"project path must be absolute, got {v!r}")
        return v


class ProjectsRegistry(BaseModel):
    """Top-level registry document."""

    model_config = ConfigDict(extra="forbid")

    projects: dict[str, ProjectEntry] = Field(default_factory=dict)

    @field_validator("projects")
    @classmethod
    def _validate_slugs(cls, v: dict[str, ProjectEntry]) -> dict[str, ProjectEntry]:
        for slug in v:
            if not _SLUG_RE.match(slug):
                raise ValueError(
                    f"invalid project slug {slug!r}: must match {_SLUG_RE.pattern}"
                )
        return v

    def upsert(self, slug: str, entry: ProjectEntry) -> ProjectsRegistry:
        """Return a copy with ``slug`` set / replaced — keeps the model immutable-friendly."""
        if not _SLUG_RE.match(slug):
            raise ProjectRegistryError(
                f"invalid project slug {slug!r}: must match {_SLUG_RE.pattern}"
            )
        new = {**self.projects, slug: entry}
        return ProjectsRegistry(projects=new)


# ── path resolution ─────────────────────────────────────────────────────────


def registry_path(*, orchestrator_home: Path) -> Path:
    """Return the registry yaml path.

    Order:
    1. ``$BMAD_PROJECTS_REGISTRY`` if set (absolute or relative-to-cwd).
    2. ``<orchestrator_home>/config/projects.yaml``.
    """
    override = os.environ.get(REGISTRY_ENV_VAR)
    if override:
        return Path(override).expanduser().resolve()
    return orchestrator_home / DEFAULT_REGISTRY_RELPATH


# ── load / save ─────────────────────────────────────────────────────────────


def load_registry(path: Path) -> ProjectsRegistry:
    """Load registry from yaml. Missing file → empty registry."""
    if not path.exists():
        return ProjectsRegistry()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ProjectRegistryError(f"registry yaml invalid at {path}: {e}") from e
    if not isinstance(raw, dict):
        raise ProjectRegistryError(
            f"registry root must be a mapping at {path}, got {type(raw).__name__}"
        )
    try:
        return ProjectsRegistry.model_validate(raw)
    except Exception as e:
        raise ProjectRegistryError(f"registry schema mismatch at {path}: {e}") from e


def save_registry(reg: ProjectsRegistry, path: Path) -> Path:
    """Atomically write ``reg`` to ``path``; create parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = reg.model_dump(mode="json")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    return path


# ── layout detection ────────────────────────────────────────────────────────


def detect_bmad_layout(project_path: Path) -> BmadLayout:
    """Heuristically classify a project's BMad layout.

    * ``not-bmad`` — no ``_bmad/`` dir.
    * ``bmm-v6`` — has ``_bmad/bmm/config.yaml`` (per-module config). Antares.
    * ``odyssey-hybrid`` — has ``_bmad/config.toml`` AND ``_bmad/planning-artifacts/``.
    * ``unknown`` — ``_bmad/`` exists but matches neither shape.

    Returned classification is best-effort; consumers should fall back to
    ``unknown`` behaviour rather than crash.
    """
    bmad = project_path / "_bmad"
    if not bmad.is_dir():
        return "not-bmad"
    if (bmad / "bmm" / "config.yaml").is_file():
        return "bmm-v6"
    if (bmad / "config.toml").is_file() and (bmad / "planning-artifacts").is_dir():
        return "odyssey-hybrid"
    return "unknown"


# ── init / scan / doctor ────────────────────────────────────────────────────


def slug_from_path(project_path: Path) -> str:
    """Derive a registry slug from an absolute project path.

    Lowercases the basename, replaces invalid chars with ``-``; trims to 64 chars.
    Pure helper — does NOT consult the registry for uniqueness.
    """
    base = project_path.resolve().name.lower()
    slug = re.sub(r"[^a-z0-9_-]", "-", base).strip("-_") or "project"
    if not _SLUG_RE.match(slug):
        slug = re.sub(r"^[^a-z0-9]+", "", slug) or "project"
    return slug[:64]


def register_project(
    reg: ProjectsRegistry,
    project_path: Path,
    *,
    slug: str | None = None,
    sandbox_overrides: dict[str, Any] | None = None,
) -> tuple[ProjectsRegistry, str, ProjectEntry]:
    """Add or update a project in the registry.

    * Auto-detects ``slug`` from path basename if not supplied.
    * Auto-detects ``bmad_layout`` via :func:`detect_bmad_layout`.
    * Raises :class:`ProjectRegistryError` on missing path.
    """
    project_path = project_path.expanduser().resolve()
    if not project_path.is_dir():
        raise ProjectRegistryError(
            f"project path does not exist or is not a directory: {project_path}"
        )
    final_slug = slug or slug_from_path(project_path)
    if not _SLUG_RE.match(final_slug):
        raise ProjectRegistryError(
            f"invalid project slug {final_slug!r}: must match {_SLUG_RE.pattern}"
        )
    entry = ProjectEntry(
        path=project_path,
        bmad_layout=detect_bmad_layout(project_path),
        sandbox_overrides=sandbox_overrides or {},
    )
    new_reg = reg.upsert(final_slug, entry)
    return new_reg, final_slug, entry


@dataclass(frozen=True)
class ScanRow:
    """One row of ``scan`` output."""

    slug: str
    path: Path
    bmad_layout: BmadLayout
    status: Literal["ok", "missing", "stale"]
    detail: str = ""


def scan_registry(reg: ProjectsRegistry) -> list[ScanRow]:
    """List all projects with current on-disk status.

    * ``ok`` — path exists and detected layout matches recorded layout.
    * ``missing`` — recorded path does not exist.
    * ``stale`` — path exists but detected layout differs from recorded layout.
    """
    rows: list[ScanRow] = []
    for slug, entry in sorted(reg.projects.items()):
        if not entry.path.is_dir():
            rows.append(
                ScanRow(
                    slug=slug,
                    path=entry.path,
                    bmad_layout=entry.bmad_layout,
                    status="missing",
                    detail="path not found",
                )
            )
            continue
        detected = detect_bmad_layout(entry.path)
        if detected != entry.bmad_layout:
            rows.append(
                ScanRow(
                    slug=slug,
                    path=entry.path,
                    bmad_layout=entry.bmad_layout,
                    status="stale",
                    detail=f"detected={detected}, recorded={entry.bmad_layout}",
                )
            )
            continue
        rows.append(
            ScanRow(
                slug=slug,
                path=entry.path,
                bmad_layout=entry.bmad_layout,
                status="ok",
            )
        )
    return rows


@dataclass(frozen=True)
class DoctorCheck:
    """One health-check result."""

    name: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class DoctorReport:
    """Aggregated health report for one project."""

    slug: str
    path: Path
    bmad_layout: BmadLayout
    checks: tuple[DoctorCheck, ...]

    @property
    def healthy(self) -> bool:
        return all(c.ok for c in self.checks)


def doctor(reg: ProjectsRegistry, slug: str) -> DoctorReport:
    """Run health checks on one project.

    Checks (cheap, read-only):
    * registry entry exists
    * project path exists
    * detected layout matches recorded
    * ``_bmad-output/`` dir exists
    * ``_bmad-output/sprint-status.md`` readable (if present)
    """
    if slug not in reg.projects:
        return DoctorReport(
            slug=slug,
            path=Path("/"),
            bmad_layout="unknown",
            checks=(
                DoctorCheck(name="registered", ok=False, detail="not in registry"),
            ),
        )

    entry = reg.projects[slug]
    checks: list[DoctorCheck] = [DoctorCheck(name="registered", ok=True)]

    path_ok = entry.path.is_dir()
    checks.append(
        DoctorCheck(
            name="path_exists",
            ok=path_ok,
            detail="" if path_ok else f"missing: {entry.path}",
        )
    )
    if not path_ok:
        return DoctorReport(
            slug=slug, path=entry.path, bmad_layout=entry.bmad_layout, checks=tuple(checks)
        )

    detected = detect_bmad_layout(entry.path)
    layout_ok = detected == entry.bmad_layout
    checks.append(
        DoctorCheck(
            name="layout_matches",
            ok=layout_ok,
            detail="" if layout_ok else f"detected={detected}, recorded={entry.bmad_layout}",
        )
    )

    output_dir = entry.path / "_bmad-output"
    output_ok = output_dir.is_dir()
    checks.append(
        DoctorCheck(
            name="bmad_output_dir",
            ok=output_ok,
            detail="" if output_ok else f"missing: {output_dir}",
        )
    )

    if output_ok:
        sprint = output_dir / "sprint-status.md"
        if sprint.exists():
            try:
                _ = sprint.read_text(encoding="utf-8")
                checks.append(DoctorCheck(name="sprint_status_readable", ok=True))
            except OSError as e:
                checks.append(
                    DoctorCheck(
                        name="sprint_status_readable",
                        ok=False,
                        detail=f"unreadable: {e}",
                    )
                )
        else:
            checks.append(
                DoctorCheck(
                    name="sprint_status_present",
                    ok=False,
                    detail="no sprint-status.md (first run?)",
                )
            )

    return DoctorReport(
        slug=slug,
        path=entry.path,
        bmad_layout=entry.bmad_layout,
        checks=tuple(checks),
    )


def resume_hint(reg: ProjectsRegistry, slug: str) -> str:
    """Return a one-line shell hint to resume work on ``slug``.

    Pure formatting helper — no state mutation. The actual ``resume`` command
    just prints this hint plus doctor() output so the operator can paste-and-go.
    """
    if slug not in reg.projects:
        return f"# unknown project {slug!r}; run `bmad-orchestrator scan` to list known projects"
    entry = reg.projects[slug]
    return (
        f"ORCHESTRATOR_TARGET_PROJECT={entry.path} "
        f"bmad-orchestrator run --project {slug} --wave <wave_id> --real"
    )
