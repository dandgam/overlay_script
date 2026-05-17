"""B2 acceptance — DagPlanner wired to BMad-upstream sprint-status parser.

End-to-end integration tests against a golden snapshot of the real
``/home/server/odyssey/_bmad/`` layout (captured 2026-05-17 — see
``tests/fixtures/golden-odyssey/`` for the YAML + story files).

These tests prove that:

* :py:meth:`DagPlanner.from_target` consumes the upstream BMad sprint-status
  schema (``development_status:`` wrapper, kebab keys, embedded prose
  comments) without falling back to "all stories backlog".
* :py:meth:`DagPlanner.find_ready` surfaces the ``ready-for-dev`` Odyssey
  story (``3.3`` in the snapshot) instead of always-yielding-epic-1.
* Story dicts carry ``epic_id`` (from sprint-status grouping or filename)
  and ``title`` (from the markdown H1), so the orchestrator pilot stops
  printing ``"no title"`` for Odyssey runs.
* The legacy ``mock-odyssey`` fixture (nested ``epics:`` schema, kebab story
  IDs) keeps working unchanged — backward-compat guarantee.
* ``reload()`` re-applies the parser so freshly-written status changes flow
  through on the next cycle.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from bmad_orchestrator.agent.tools._common import (
    read_sprint_status_yaml,
    write_sprint_status_yaml,
)
from bmad_orchestrator.runtime.dag_planner import DagPlanner

GOLDEN_FIXTURE = Path(__file__).parent / "fixtures" / "golden-odyssey"


@pytest.fixture
def odyssey_target(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Copy the golden Odyssey snapshot into ``tmp_path`` and point the
    orchestrator at it via ``ORCHESTRATOR_TARGET_PROJECT``."""
    dst = tmp_path / "odyssey"
    shutil.copytree(GOLDEN_FIXTURE, dst)
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(dst))
    return dst


@pytest.fixture
def legacy_target(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Copy the legacy ``mock-odyssey`` fixture (nested ``epics:`` schema) into
    ``tmp_path`` — used for backward-compat assertions."""
    src = Path(__file__).parent / "fixtures" / "mock-odyssey"
    dst = tmp_path / "mock-odyssey"
    shutil.copytree(src, dst)
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(dst))
    return dst


# ── BMad-upstream layout (Odyssey golden snapshot) ──────────────────────────


def test_from_target_normalizes_upstream_bmad_schema(odyssey_target: Path) -> None:
    """``sprint_status`` after load is the unified ``{epics: {N: {status, stories}}}``
    shape with dotted story IDs, even though the on-disk yaml uses
    ``development_status:`` + kebab keys."""
    planner = DagPlanner.from_target()
    epics = planner.sprint_status["epics"]
    assert {"1", "3"}.issubset(epics.keys())
    assert epics["3"]["status"] == "in-progress"
    # Kebab keys collapsed to dotted form by parse_sprint_status_bmad.
    assert "3.1" in epics["3"]["stories"]
    assert "3.3" in epics["3"]["stories"]
    # Embedded prose comment stripped: ``done  # 2026-05-17: ...`` → ``done``.
    assert epics["3"]["stories"]["3.1"] == "done"
    # Multi-token NEEDS-FIX status collapses to ``in-progress``.
    assert epics["3"]["stories"]["3.2"] == "in-progress"


def test_find_ready_surfaces_epic3_ready_for_dev(odyssey_target: Path) -> None:
    """Top of B2's why-we-built-this: real Odyssey wave has 3.3 ready-for-dev;
    pre-B2 DagPlanner returned epic-1 stories instead. Now it must surface 3.3."""
    planner = DagPlanner.from_target()
    ready_ids = {s["id"] for s in planner.find_ready(max_n=10)}
    assert "3.3" in ready_ids
    # 3.1 is done, 3.2 is in-progress — neither is ``ready-for-dev``/``backlog``,
    # so they must NOT appear in find_ready output.
    assert "3.1" not in ready_ids
    assert "3.2" not in ready_ids


def test_find_ready_story_carries_title_from_h1(odyssey_target: Path) -> None:
    """Spec acceptance: stories returned by ``find_ready`` expose the human
    title parsed from ``# Story X.Y: <title>`` so CLI no longer prints
    ``"no title"`` for Odyssey runs."""
    planner = DagPlanner.from_target()
    by_id = {s["id"]: s for s in planner.find_ready(max_n=10)}
    assert by_id["3.3"]["title"] == "Hard-block trigger enforcement"
    assert by_id["3.4"]["title"] == "Free-tier auto-activate"


def test_find_ready_story_carries_epic_id_from_sprint_status(
    odyssey_target: Path,
) -> None:
    """``epic_id`` for 3.3 comes from the sprint-status grouping (``epic-3``)
    even though the filename prefix (``3``) coincides — important when
    sprint-status disagrees with filename for renamed stories."""
    planner = DagPlanner.from_target()
    s33 = next(s for s in planner.find_ready(max_n=10) if s["id"] == "3.3")
    assert s33["epic_id"] == "3"


def test_find_ready_skips_done_and_in_progress_statuses(
    odyssey_target: Path,
) -> None:
    """Defensive: a ``done`` (3.1) or ``in-progress`` (3.2) story MUST NOT
    accidentally re-enter ``find_ready`` — that would re-spawn already-shipped
    work."""
    planner = DagPlanner.from_target()
    ready_ids = {s["id"] for s in planner.find_ready(max_n=20)}
    # Whole epic-1 is done → none of its stories should be ready.
    assert "1.1" not in ready_ids
    assert "1.17b" not in ready_ids
    # epic-3 mid-flight stories: done + in-progress.
    assert "3.1" not in ready_ids
    assert "3.2" not in ready_ids


def test_find_ready_includes_backlog_with_no_deps(odyssey_target: Path) -> None:
    """``backlog`` stories with no unresolved deps remain selectable so the
    wave pre-loads the next batch (matches pre-B2 behavior; only the
    embedded-prose / kebab-key parsing changed)."""
    planner = DagPlanner.from_target()
    ready_ids = {s["id"] for s in planner.find_ready(max_n=20)}
    assert "3.4" in ready_ids  # ``backlog``, no deps
    assert "0.0" in ready_ids  # ``story-0-0-...``, normalized to ``0.0``


def test_reload_picks_up_status_changes(odyssey_target: Path) -> None:
    """After marking 3.3 as ``done``, the next ``reload()`` must drop it from
    ``find_ready``. This drives the mock pilot loop (test_s3) and the real
    spawn cycle in `agent.run`."""
    planner = DagPlanner.from_target()
    assert "3.3" in {s["id"] for s in planner.find_ready(max_n=10)}

    # Flip 3.3 → done via the public write helper (keeps yaml shape).
    raw = read_sprint_status_yaml()
    raw["development_status"]["3-3-hard-block-trigger-enforcement"] = "done"
    write_sprint_status_yaml(raw)

    planner.reload()
    assert "3.3" not in {s["id"] for s in planner.find_ready(max_n=10)}


def test_normalize_lookup_matches_kebab_filenames_against_dotted_status(
    odyssey_target: Path,
) -> None:
    """Cross-form lookup: even though the golden fixture's story files are
    DOTTED (``3.3.md``), the BMad-upstream sprint-status writes KEBAB keys
    (``3-3-hard-block-trigger-enforcement``). The status lookup must
    transparently bridge the two via ``normalize_story_id``.
    """
    # Replace a story file's stem with the kebab equivalent + reload.
    src = odyssey_target / "_bmad" / "stories" / "3.3.md"
    dst = src.with_name("3-3-hard-block-trigger-enforcement.md")
    src.rename(dst)

    planner = DagPlanner.from_target()
    ready_ids = {s["id"] for s in planner.find_ready(max_n=10)}
    # Whichever id form the file kept, find_ready treats it as ``ready-for-dev``.
    assert ready_ids & {"3.3", "3-3-hard-block-trigger-enforcement"}


# ── Legacy backward-compat (mock-odyssey nested epics: schema) ──────────────


def test_legacy_fixture_preserves_kebab_story_ids_in_graph(
    legacy_target: Path,
) -> None:
    """Legacy ``epics: {N: {stories: {<kebab-id>: status}}}`` keeps yielding
    stories whose ``id`` matches the on-disk filename stem. The DagPlanner
    bridge (``_status_by_id`` + ``normalize_story_id`` on lookup) means the
    raw kebab id remains the canonical node key the rest of the pipeline
    (worker spawn, JSONL events, branch naming) already depends on.
    """
    planner = DagPlanner.from_target()
    ids = set(planner.graph.nodes)
    assert "1-1-tenant-signup" in ids
    # Sprint-status flips 1-1 to ready-for-dev → it MUST surface in find_ready.
    ready_ids = {s["id"] for s in planner.find_ready(max_n=10)}
    assert "1-1-tenant-signup" in ready_ids


# ── Golden snapshot integrity guard ─────────────────────────────────────────


def test_golden_fixture_yaml_remains_canonical_bmad_shape() -> None:
    """Guard against accidental fixture edits that would invalidate the rest
    of the suite. The committed snapshot MUST keep:
      * ``development_status:`` wrapper (upstream BMad schema)
      * at least one kebab-with-prose key (``3-1-lifecycle-...``)
      * a retrospective key the parser must silently drop.
    """
    raw_yaml = (
        GOLDEN_FIXTURE / "_bmad" / "implementation-artifacts" / "sprint-status.yaml"
    ).read_text(encoding="utf-8")
    data = yaml.safe_load(raw_yaml)
    assert "development_status" in data
    keys = set(data["development_status"].keys())
    assert "3-1-lifecycle-state-machine" in keys
    assert "epic-1-retrospective" in keys
