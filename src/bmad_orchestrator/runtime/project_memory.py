"""L3 per-project memory — primes BudgetGuard rolling windows per target project.

Spec: spec/spec_embed_phase45_with_selflearning.md §E7.

Each target project (Odyssey, CRM, etc.) accrues a small YAML rollup at
``<orchestrator_home>/_config/projects/<slug>/memory.yaml`` that captures
the most recent samples driving E6 live tuning + a few high-level
aggregates (median story cost, last wave seen, success rate, compliance
finding count, lessons file count).

On orchestrator boot for ``--project <slug>``:

1. :func:`load_project_memory` reads the file (or returns fresh defaults
   when missing).
2. :meth:`BudgetGuard.prime_from_memory` extends the four rolling deques
   with the persisted ``recent_*`` arrays.
3. From there E6 live tuning has historical signal from the very first
   story of the new run instead of starting empty.

Schema migration: ``schema_version`` is bumped whenever the on-disk shape
changes. :func:`_migrate_payload` upgrades older payloads in-memory so
existing memory files keep loading after a schema bump. The model is
``extra='forbid'``; migration must remove fields removed in a newer
version and inject defaults for fields added.
"""

from __future__ import annotations

import math
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

CURRENT_SCHEMA_VERSION: int = 1


class ProjectMemoryError(Exception):
    """Base for project_memory errors."""


class ProjectMemoryInvalidError(ProjectMemoryError):
    """Raised when memory.yaml fails schema validation."""


class ProjectMemory(BaseModel):
    """Per-project rollup persisted across orchestrator runs."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = CURRENT_SCHEMA_VERSION
    project_slug: str

    # ── high-level aggregates (surfaced to operator + future lessons parser) ──
    median_story_cost_usd: float = 0.0
    median_review_p0: float = 0.0
    median_test_count: float = 0.0
    last_wave: str | None = None
    success_rate: float = 0.0
    compliance_findings_count: int = 0
    lessons_files_count: int = 0

    # ── rolling windows that prime BudgetGuard deques ─────────────────────────
    # Oldest first → newest last; up to 10 items each. BudgetGuard.extend()
    # preserves order so the newest sample lands at the right of the deque.
    recent_story_costs: list[float] = Field(default_factory=list)
    recent_p0_coverages: list[float] = Field(default_factory=list)
    recent_test_coverages: list[float] = Field(default_factory=list)
    recent_review_iterations: list[int] = Field(default_factory=list)


def memory_path(slug: str, *, orchestrator_home: Path) -> Path:
    """Resolve the on-disk memory path for ``slug`` under ``orchestrator_home``.

    Layout: ``<orchestrator_home>/_config/projects/<slug>/memory.yaml``. The
    slug is treated as a directory name component — callers MUST sanitize
    untrusted input before passing it here (we reject path separators to
    fail loud on accidental misuse).
    """
    if not slug or "/" in slug or "\\" in slug or slug in {".", ".."}:
        raise ProjectMemoryInvalidError(f"invalid project slug: {slug!r}")
    return orchestrator_home / "_config" / "projects" / slug / "memory.yaml"


def _migrate_payload(raw: dict[str, Any], slug: str) -> dict[str, Any]:
    """Up-convert an on-disk payload to :data:`CURRENT_SCHEMA_VERSION`.

    Schema_version=0 (pre-release prototype): missing some recent_* arrays
    or compliance_findings_count / lessons_files_count. Fill defaults.
    Forward-compat: unknown fields in a future version cause a fail-loud
    via ``extra='forbid'`` in model_validate — that's intentional, we want
    the orchestrator to refuse rather than silently drop.
    """
    payload = dict(raw)
    payload.setdefault("schema_version", CURRENT_SCHEMA_VERSION)
    payload.setdefault("project_slug", slug)

    if payload["schema_version"] < CURRENT_SCHEMA_VERSION:
        payload.setdefault("recent_story_costs", [])
        payload.setdefault("recent_p0_coverages", [])
        payload.setdefault("recent_test_coverages", [])
        payload.setdefault("recent_review_iterations", [])
        payload.setdefault("compliance_findings_count", 0)
        payload.setdefault("lessons_files_count", 0)
        payload.setdefault("median_story_cost_usd", 0.0)
        payload.setdefault("median_review_p0", 0.0)
        payload.setdefault("median_test_count", 0.0)
        payload.setdefault("last_wave", None)
        payload.setdefault("success_rate", 0.0)
        payload["schema_version"] = CURRENT_SCHEMA_VERSION

    return payload


def load_project_memory(
    slug: str, *, orchestrator_home: Path
) -> ProjectMemory:
    """Load memory for ``slug`` or return fresh defaults on missing file.

    Missing file → ``ProjectMemory(project_slug=slug)`` with all defaults
    (the orchestrator can then save it after the first story completes).
    Malformed YAML or schema violation → :class:`ProjectMemoryInvalidError`.
    """
    path = memory_path(slug, orchestrator_home=orchestrator_home)
    if not path.exists():
        return ProjectMemory(project_slug=slug)

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ProjectMemoryInvalidError(
            f"memory.yaml not valid YAML at {path}: {e}"
        ) from e

    if not isinstance(raw, dict):
        raise ProjectMemoryInvalidError(
            f"memory.yaml must be a mapping at {path}, got {type(raw).__name__}"
        )

    payload = _migrate_payload(raw, slug)

    try:
        return ProjectMemory.model_validate(payload)
    except ValidationError as e:
        raise ProjectMemoryInvalidError(
            f"memory.yaml schema mismatch at {path}: {e}"
        ) from e


def save_project_memory(
    memory: ProjectMemory, *, orchestrator_home: Path
) -> Path:
    """Atomically persist ``memory`` to its on-disk path; create dirs.

    Returns the resolved path. Uses tempfile + fsync + os.replace (same
    pattern as :func:`runtime.live_tuning.atomic_write_gates_yaml`). On
    exception the tempfile is unlinked so the target stays at its
    pre-call content.
    """
    path = memory_path(memory.project_slug, orchestrator_home=orchestrator_home)
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "schema_version": memory.schema_version,
        "project_slug": memory.project_slug,
        "median_story_cost_usd": float(memory.median_story_cost_usd),
        "median_review_p0": float(memory.median_review_p0),
        "median_test_count": float(memory.median_test_count),
        "last_wave": memory.last_wave,
        "success_rate": float(memory.success_rate),
        "compliance_findings_count": int(memory.compliance_findings_count),
        "lessons_files_count": int(memory.lessons_files_count),
        "recent_story_costs": [float(x) for x in memory.recent_story_costs],
        "recent_p0_coverages": [float(x) for x in memory.recent_p0_coverages],
        "recent_test_coverages": [float(x) for x in memory.recent_test_coverages],
        "recent_review_iterations": [int(x) for x in memory.recent_review_iterations],
    }
    serialised = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)

    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            fh.write(serialised)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise
    return path


def _finite_positive(values: Iterable[float]) -> list[float]:
    """Drop NaN / inf / non-positive values; preserve order."""
    out: list[float] = []
    for v in values:
        f = float(v)
        if math.isnan(f) or not math.isfinite(f) or f <= 0.0:
            continue
        out.append(f)
    return out


def _finite_clamped_ratio(values: Iterable[float]) -> list[float]:
    """Drop NaN / inf; clamp surviving values to [0, 1]; preserve order."""
    out: list[float] = []
    for v in values:
        f = float(v)
        if math.isnan(f) or not math.isfinite(f):
            continue
        out.append(min(1.0, max(0.0, f)))
    return out


def _finite_nonneg_int(values: Iterable[int]) -> list[int]:
    """Drop negative / non-int-coercible values; preserve order."""
    out: list[int] = []
    for v in values:
        try:
            i = int(v)
        except (TypeError, ValueError):
            continue
        if i < 0:
            continue
        out.append(i)
    return out


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "ProjectMemory",
    "ProjectMemoryError",
    "ProjectMemoryInvalidError",
    "_finite_clamped_ratio",
    "_finite_nonneg_int",
    "_finite_positive",
    "load_project_memory",
    "memory_path",
    "save_project_memory",
]
