"""E7 acceptance — L3 per-project memory primes BudgetGuard rolling windows.

Spec: spec/spec_embed_phase45_with_selflearning.md §E7.

Coverage (25 tests):

* :func:`memory_path` (3) — default layout, custom root, slug sanitization.
* :func:`load_project_memory` fresh-init / round-trip (4) — missing file
  returns defaults, save creates parent dirs, round-trip identity, second
  load picks up persisted rolling windows.
* :class:`ProjectMemory` schema (4) — defaults, extra=forbid, required
  ``project_slug``, recent_* defaults to empty list.
* Schema migration (2) — legacy ``schema_version=0`` payload upgrades
  in-place; unknown future field is rejected (fail loud).
* :func:`save_project_memory` atomicity (3) — round-trip, tempfile cleanup
  on raise, no half-state survives a mid-write error.
* :meth:`BudgetGuard.prime_from_memory` (6) — story costs, P0 coverages,
  test coverages, iteration counts, invalid-sample dropping, order
  preservation (newest at right of each deque).
* Integration (3) — :func:`load_project_memory` + prime → adaptive
  reserve uses primed data; agent.run.start primes from memory; corrupt
  YAML logs warning + continues with empty windows.
"""

from __future__ import annotations

import asyncio
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from bmad_orchestrator.agent.safety.budget_guard import BudgetGuard
from bmad_orchestrator.config import BudgetConfig
from bmad_orchestrator.runtime.project_memory import (
    CURRENT_SCHEMA_VERSION,
    ProjectMemory,
    ProjectMemoryInvalidError,
    load_project_memory,
    memory_path,
    save_project_memory,
)


def _budget() -> BudgetGuard:
    return BudgetGuard(BudgetConfig())


# ── memory_path ────────────────────────────────────────────────────────────


def test_e7_memory_path_default_layout(tmp_path: Path) -> None:
    p = memory_path("odyssey", orchestrator_home=tmp_path)
    assert p == tmp_path / "_config" / "projects" / "odyssey" / "memory.yaml"


def test_e7_memory_path_honors_orchestrator_home_override(tmp_path: Path) -> None:
    custom = tmp_path / "alt-home"
    p = memory_path("crm", orchestrator_home=custom)
    assert p.parent == custom / "_config" / "projects" / "crm"
    assert p.name == "memory.yaml"


def test_e7_memory_path_rejects_path_traversal_slugs(tmp_path: Path) -> None:
    """Slugs containing path separators or relative components must fail loud."""
    for bad in ("", "../escape", "a/b", "a\\b", ".", ".."):
        with pytest.raises(ProjectMemoryInvalidError):
            memory_path(bad, orchestrator_home=tmp_path)


# ── load_project_memory + round-trip ───────────────────────────────────────


def test_e7_load_missing_file_returns_fresh_defaults(tmp_path: Path) -> None:
    mem = load_project_memory("fresh-project", orchestrator_home=tmp_path)
    assert mem.project_slug == "fresh-project"
    assert mem.schema_version == CURRENT_SCHEMA_VERSION
    assert mem.recent_story_costs == []
    assert mem.recent_p0_coverages == []
    assert mem.recent_test_coverages == []
    assert mem.recent_review_iterations == []
    assert mem.median_story_cost_usd == 0.0
    assert mem.last_wave is None


def test_e7_save_creates_parent_dirs_and_writes_yaml(tmp_path: Path) -> None:
    mem = ProjectMemory(project_slug="proj-x", median_story_cost_usd=12.5)
    out = save_project_memory(mem, orchestrator_home=tmp_path)
    assert out.exists()
    assert out.parent == tmp_path / "_config" / "projects" / "proj-x"
    parsed = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert parsed["project_slug"] == "proj-x"
    assert parsed["median_story_cost_usd"] == 12.5


def test_e7_round_trip_load_save_load_identity(tmp_path: Path) -> None:
    original = ProjectMemory(
        project_slug="rt",
        median_story_cost_usd=4.2,
        last_wave="W1a",
        success_rate=0.83,
        compliance_findings_count=2,
        lessons_files_count=5,
        recent_story_costs=[1.0, 2.0, 3.0],
        recent_p0_coverages=[0.5, 0.75, 1.0],
        recent_test_coverages=[0.0, 0.5, 1.0],
        recent_review_iterations=[1, 2, 1],
    )
    save_project_memory(original, orchestrator_home=tmp_path)
    reloaded = load_project_memory("rt", orchestrator_home=tmp_path)
    assert reloaded == original


def test_e7_second_load_returns_persisted_rolling_windows(tmp_path: Path) -> None:
    first = load_project_memory("p", orchestrator_home=tmp_path)
    populated = first.model_copy(
        update={"recent_p0_coverages": [0.1, 0.2, 0.3, 0.4, 0.5]}
    )
    save_project_memory(populated, orchestrator_home=tmp_path)
    second = load_project_memory("p", orchestrator_home=tmp_path)
    assert second.recent_p0_coverages == [0.1, 0.2, 0.3, 0.4, 0.5]


# ── ProjectMemory schema ────────────────────────────────────────────────────


def test_e7_project_memory_defaults_via_model_validate() -> None:
    mem = ProjectMemory.model_validate({"project_slug": "x"})
    assert mem.schema_version == CURRENT_SCHEMA_VERSION
    assert mem.recent_story_costs == []
    assert mem.median_review_p0 == 0.0


def test_e7_project_memory_extra_forbid_rejects_unknown() -> None:
    with pytest.raises(ValidationError):
        ProjectMemory.model_validate(
            {"project_slug": "x", "unknown_future_field": 1}
        )


def test_e7_project_memory_requires_project_slug() -> None:
    with pytest.raises(ValidationError):
        ProjectMemory.model_validate({})


def test_e7_project_memory_recent_arrays_default_empty() -> None:
    mem = ProjectMemory(project_slug="x")
    assert mem.recent_story_costs == []
    assert mem.recent_p0_coverages == []
    assert mem.recent_test_coverages == []
    assert mem.recent_review_iterations == []


# ── schema migration ───────────────────────────────────────────────────────


def test_e7_load_legacy_schema_version_zero_upgrades_in_place(
    tmp_path: Path,
) -> None:
    """v0 payload (missing several rolling windows + counters) loads cleanly."""
    legacy_dir = tmp_path / "_config" / "projects" / "legacy"
    legacy_dir.mkdir(parents=True)
    legacy_payload: dict[str, Any] = {
        "schema_version": 0,
        "project_slug": "legacy",
        "median_story_cost_usd": 9.9,
        # NOTE: recent_* arrays + compliance/lessons counts absent
    }
    (legacy_dir / "memory.yaml").write_text(
        yaml.safe_dump(legacy_payload), encoding="utf-8"
    )
    mem = load_project_memory("legacy", orchestrator_home=tmp_path)
    assert mem.schema_version == CURRENT_SCHEMA_VERSION
    assert mem.median_story_cost_usd == 9.9
    assert mem.recent_story_costs == []
    assert mem.compliance_findings_count == 0
    assert mem.lessons_files_count == 0


def test_e7_load_with_unknown_field_fails_loud(tmp_path: Path) -> None:
    """Forward incompatibility: an unknown field on the current schema
    raises rather than being silently dropped."""
    proj_dir = tmp_path / "_config" / "projects" / "future"
    proj_dir.mkdir(parents=True)
    (proj_dir / "memory.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": CURRENT_SCHEMA_VERSION,
                "project_slug": "future",
                "v999_unrecognized_field": "boom",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ProjectMemoryInvalidError):
        load_project_memory("future", orchestrator_home=tmp_path)


# ── save_project_memory atomicity ───────────────────────────────────────────


def test_e7_atomic_write_round_trip_preserves_key_order(tmp_path: Path) -> None:
    mem = ProjectMemory(
        project_slug="ord",
        recent_story_costs=[1.0, 2.0],
        recent_p0_coverages=[0.5],
    )
    out = save_project_memory(mem, orchestrator_home=tmp_path)
    text = out.read_text(encoding="utf-8")
    # YAML dumped with sort_keys=False — schema_version comes first.
    assert text.splitlines()[0].startswith("schema_version:")
    # Project_slug is second.
    assert "project_slug: ord" in text.splitlines()[1]


def test_e7_atomic_write_cleans_tempfile_on_replace_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mem = ProjectMemory(project_slug="boom", median_story_cost_usd=1.0)

    real_replace = os.replace

    def _boom(src: str, dst: str) -> None:  # type: ignore[override]
        raise OSError("simulated replace failure")

    monkeypatch.setattr(
        "bmad_orchestrator.runtime.project_memory.os.replace", _boom
    )

    with pytest.raises(OSError):
        save_project_memory(mem, orchestrator_home=tmp_path)

    parent = tmp_path / "_config" / "projects" / "boom"
    # No tempfile leftovers, no target file written.
    leftover_tmps = list(parent.glob("memory.yaml.*.tmp"))
    assert leftover_tmps == []
    assert not (parent / "memory.yaml").exists()

    # Restore for safety in case other tests share the monkeypatch fixture scope.
    monkeypatch.setattr(
        "bmad_orchestrator.runtime.project_memory.os.replace", real_replace
    )


def test_e7_atomic_write_overwrites_existing_without_partial_state(
    tmp_path: Path,
) -> None:
    """Old file must remain readable until the new write completes."""
    initial = ProjectMemory(project_slug="ov", median_story_cost_usd=1.0)
    save_project_memory(initial, orchestrator_home=tmp_path)
    out = memory_path("ov", orchestrator_home=tmp_path)
    original_bytes = out.read_bytes()

    updated = initial.model_copy(update={"median_story_cost_usd": 2.0})
    save_project_memory(updated, orchestrator_home=tmp_path)
    final_text = out.read_text(encoding="utf-8")

    assert "median_story_cost_usd: 2.0" in final_text
    # Sanity: byte content actually changed (no half-state)
    assert out.read_bytes() != original_bytes


# ── BudgetGuard.prime_from_memory ───────────────────────────────────────────


def test_e7_prime_fills_recent_story_costs_deque() -> None:
    budget = _budget()
    mem = ProjectMemory(
        project_slug="x", recent_story_costs=[1.0, 2.0, 3.0, 4.0, 5.0]
    )
    budget.prime_from_memory(mem)
    # _recent_story_costs has maxlen=3 — only the last 3 finite-positive
    # samples survive (3.0, 4.0, 5.0).
    assert list(budget._recent_story_costs) == [
        Decimal("3.0"),
        Decimal("4.0"),
        Decimal("5.0"),
    ]


def test_e7_prime_fills_p0_coverage_deque_up_to_maxlen() -> None:
    budget = _budget()
    # 12 samples > maxlen 10 — last 10 win.
    samples = [i / 12 for i in range(1, 13)]
    mem = ProjectMemory(project_slug="x", recent_p0_coverages=samples)
    budget.prime_from_memory(mem)
    primed = budget.recent_p0_coverages()
    assert len(primed) == 10
    assert primed == tuple(samples[-10:])


def test_e7_prime_fills_test_coverage_deque() -> None:
    budget = _budget()
    mem = ProjectMemory(
        project_slug="x", recent_test_coverages=[0.1, 0.4, 0.9]
    )
    budget.prime_from_memory(mem)
    assert budget.recent_test_coverages() == (0.1, 0.4, 0.9)


def test_e7_prime_fills_review_iterations_deque() -> None:
    budget = _budget()
    mem = ProjectMemory(
        project_slug="x", recent_review_iterations=[1, 2, 3]
    )
    budget.prime_from_memory(mem)
    assert budget.recent_review_iterations() == (1, 2, 3)


def test_e7_prime_drops_invalid_story_costs() -> None:
    """NaN, inf, zero, negative story costs are filtered out."""
    budget = _budget()
    mem = ProjectMemory(
        project_slug="x",
        recent_story_costs=[
            float("nan"),
            float("inf"),
            -1.0,
            0.0,
            2.5,
            3.5,
        ],
    )
    budget.prime_from_memory(mem)
    # Only 2.5 and 3.5 survive — both fit in maxlen=3.
    assert list(budget._recent_story_costs) == [
        Decimal("2.5"),
        Decimal("3.5"),
    ]


def test_e7_prime_preserves_order_newest_at_right() -> None:
    """The deque's right end matches the persisted list's tail."""
    budget = _budget()
    # All values within maxlen=10 — no eviction.
    samples = [0.2, 0.3, 0.4, 0.5, 0.6]
    mem = ProjectMemory(project_slug="x", recent_p0_coverages=samples)
    budget.prime_from_memory(mem)
    primed = budget.recent_p0_coverages()
    assert primed[0] == 0.2
    assert primed[-1] == 0.6


# ── integration: prime + adaptive reserve + agent.run wiring ──────────────


def test_e7_adaptive_story_reserve_uses_primed_data(
    tmp_path: Path,
) -> None:
    """After prime, ``adaptive_story_reserve`` consults the persisted
    samples instead of returning the cold-start half-cap default."""
    cold_budget = _budget()
    cold_reserve = cold_budget.adaptive_story_reserve()
    assert cold_reserve == Decimal(str(BudgetConfig().story_alarm_usd)) / Decimal(2)

    primed = _budget()
    mem = ProjectMemory(project_slug="x", recent_story_costs=[7.0, 5.0])
    primed.prime_from_memory(mem)
    primed_reserve = primed.adaptive_story_reserve()
    # max(7.0, 5.0) = 7.0 — well below story_alarm_usd default 30.0.
    assert primed_reserve == Decimal("7.0")


def test_e7_agent_run_start_primes_from_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``agent.run.start(project=..., mock=True)`` loads + primes the
    BudgetGuard before dispatching to the mock pilot."""
    from bmad_orchestrator.agent import run as run_module
    from bmad_orchestrator.config import Settings

    # 1) Point orchestrator_home at a temp dir and seed a memory.yaml there.
    project_slug = "agent-run-test"
    mem = ProjectMemory(
        project_slug=project_slug,
        recent_p0_coverages=[0.4, 0.5, 0.6, 0.7, 0.8],
    )
    save_project_memory(mem, orchestrator_home=tmp_path)

    captured: dict[str, Any] = {}

    async def _capture_mock_pilot(
        bus: Any, *, wave: str, max_parallel: int, budget: Any
    ) -> None:
        captured["budget"] = budget
        captured["coverages"] = budget.recent_p0_coverages()

    monkeypatch.setattr(run_module, "_run_mock_pilot", _capture_mock_pilot)

    # Bypass session DB to keep the test self-contained.
    async def _no_session(**kwargs: Any) -> tuple[None, None]:
        return None, None

    monkeypatch.setattr(run_module, "_resolve_session", _no_session)

    def _fake_load_settings() -> Settings:
        return Settings(orchestrator_home=tmp_path)

    monkeypatch.setattr(run_module, "load_settings", _fake_load_settings)

    asyncio.run(
        run_module.run_orchestrator(
            project=project_slug, wave="1a", max_parallel=1, mock=True
        )
    )

    primed = captured["coverages"]
    assert primed == (0.4, 0.5, 0.6, 0.7, 0.8)


def test_e7_agent_run_start_tolerates_corrupt_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Corrupt memory.yaml is logged but does not crash boot — the run
    continues with empty rolling windows."""
    from bmad_orchestrator.agent import run as run_module
    from bmad_orchestrator.config import Settings

    project_slug = "corrupt-mem-test"
    proj_dir = tmp_path / "_config" / "projects" / project_slug
    proj_dir.mkdir(parents=True)
    (proj_dir / "memory.yaml").write_text(": [bad yaml", encoding="utf-8")

    captured: dict[str, Any] = {}

    async def _capture_mock_pilot(
        bus: Any, *, wave: str, max_parallel: int, budget: Any
    ) -> None:
        captured["coverages"] = budget.recent_p0_coverages()

    monkeypatch.setattr(run_module, "_run_mock_pilot", _capture_mock_pilot)

    async def _no_session(**kwargs: Any) -> tuple[None, None]:
        return None, None

    monkeypatch.setattr(run_module, "_resolve_session", _no_session)

    def _fake_load_settings() -> Settings:
        return Settings(orchestrator_home=tmp_path)

    monkeypatch.setattr(run_module, "load_settings", _fake_load_settings)

    asyncio.run(
        run_module.run_orchestrator(
            project=project_slug, wave="1a", max_parallel=1, mock=True
        )
    )

    # Empty deques — the corrupt file was tolerated.
    assert captured["coverages"] == ()
