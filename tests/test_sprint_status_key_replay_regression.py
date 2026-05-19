"""NEW-3-completion — sprint-status mark-done must work on the upstream BMad
flat layout, not just the legacy nested ``epics:`` layout.

Regression source: ``spec/spec_pilot_findings_closure_v3.md`` §2 #3. The
Antares 1a replay kept emitting ``pilot_mark_done_unresolved`` for stories
1.3/1.4/1.5. Diagnosis: the resolver (``resolve_sprint_status_key``, v2 commit
729650f) was fine — but the real-pilot mark-done loop read the *raw* YAML and
looked only under ``snap["epics"]``. Real BMad projects write the flat
``development_status: {1-4-...: done}`` layout, where ``snap["epics"]`` is
absent → every succeeded story fell through to ``pilot_mark_done_unresolved``
and the resolver was never reached.

Fix under test: ``mark_sprint_status_done`` dispatches on layout (legacy
nested + upstream BMad flat) and flips the matched key in place.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from bmad_orchestrator.agent.tools._common import (
    read_sprint_status_yaml,
    write_sprint_status_yaml,
)
from bmad_orchestrator.config import Settings
from bmad_orchestrator.runtime.bmad_format import mark_sprint_status_done

# ── 1. BMad flat development_status: layout (the actual replay layout) ────────


def test_mark_done_bmad_flat_development_status_layout() -> None:
    """Antares 1a writes ``development_status:`` flat keys. Dotted spawned ids
    (``1.4``/``1.5``) must resolve to the kebab-with-prose keys and flip."""
    snap: dict[str, Any] = {
        "development_status": {
            "epic-1": "in-progress",
            "1-3-fastapi-app-lifespan-health": "done",
            "1-4-startup-v2-checks": "ready-for-dev",
            "1-5-graceful-shutdown": "ready-for-dev",
        }
    }
    assert mark_sprint_status_done(snap, "1.4") == "1-4-startup-v2-checks"
    assert mark_sprint_status_done(snap, "1.5") == "1-5-graceful-shutdown"
    ds = snap["development_status"]
    assert ds["1-4-startup-v2-checks"] == "done"
    assert ds["1-5-graceful-shutdown"] == "done"
    # Epic marker untouched — it is not a story key.
    assert ds["epic-1"] == "in-progress"


# ── 2. bare-flat root layout + digit-bearing suffix ──────────────────────────


def test_mark_done_bare_flat_root_and_digit_suffix() -> None:
    """Bare-flat layout (kebab keys at root, no ``development_status`` wrapper).
    The ``v2`` digit in ``1-4-startup-v2-checks`` must not break resolution —
    the kebab id is split on the leading ``epic-story`` prefix only."""
    snap: dict[str, Any] = {
        "epic-1": "in-progress",
        "1-4-startup-v2-checks": "ready-for-dev",
        "1-17b-mv-fallback-conditional": "ready-for-dev",
    }
    # dotted spawned id
    assert mark_sprint_status_done(snap, "1.4") == "1-4-startup-v2-checks"
    # full kebab spawned id (DAG ids are filename stems) — exact match
    assert (
        mark_sprint_status_done(snap, "1-17b-mv-fallback-conditional")
        == "1-17b-mv-fallback-conditional"
    )
    assert snap["1-4-startup-v2-checks"] == "done"
    assert snap["1-17b-mv-fallback-conditional"] == "done"


# ── 3. epic-block scoping — legacy nested layout, no cross-epic false match ───


def test_mark_done_legacy_nested_epic_scoping() -> None:
    """Legacy ``epics:`` nested layout: a story id must flip only the matching
    epic block, and a genuinely absent id returns ``None`` (→ caller logs
    ``pilot_mark_done_unresolved``)."""
    snap: dict[str, Any] = {
        "epics": {
            "1": {"status": "done", "stories": {"1.1": "done", "1.2": "done"}},
            "2": {
                "status": "in-progress",
                "stories": {"2.1": "ready-for-dev", "2.2": "ready-for-dev"},
            },
        }
    }
    assert mark_sprint_status_done(snap, "2.1") == "2.1"
    assert snap["epics"]["2"]["stories"]["2.1"] == "done"
    # Epic-1 stories untouched.
    assert snap["epics"]["1"]["stories"]["1.2"] == "done"
    assert snap["epics"]["2"]["stories"]["2.2"] == "ready-for-dev"
    # Absent story → None, nothing mutated.
    assert mark_sprint_status_done(snap, "9.9") is None


# ── 4. integration — read → mark → write → reread round-trip ─────────────────


def test_pilot_mark_done_loop_roundtrip_on_bmad_flat_file(tmp_path: Path) -> None:
    """Integration: mirror the real-pilot mark-done loop against an
    Antares-shaped ``sprint-status.yaml`` on disk — read raw, flip each
    succeeded story, write back. The flat ``development_status:`` layout must
    survive the round-trip and the succeeded stories must read back ``done``.
    """
    target = tmp_path / "antares"
    artifacts = target / "_bmad-output" / "planning-artifacts"
    artifacts.mkdir(parents=True)
    sprint_path = artifacts / "sprint-status.yaml"
    sprint_path.write_text(
        yaml.safe_dump(
            {
                "development_status": {
                    "epic-1": "in-progress",
                    "1-3-fastapi-app-lifespan-health": "done",
                    "1-4-startup-v2-checks": "ready-for-dev",
                    "1-5-graceful-shutdown": "ready-for-dev",
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    settings = Settings(target_project=target)

    # The pilot mark-done loop, verbatim shape: read raw, mark, write.
    succeeded = ["1.4", "1.5"]
    unresolved: list[str] = []
    snap = read_sprint_status_yaml(settings)
    for sid in succeeded:
        if mark_sprint_status_done(snap, sid) is None:
            unresolved.append(sid)
    write_sprint_status_yaml(snap, settings)

    assert unresolved == [], f"stories left unresolved: {unresolved}"

    reread = read_sprint_status_yaml(settings)
    ds = reread["development_status"]
    assert ds["1-4-startup-v2-checks"] == "done"
    assert ds["1-5-graceful-shutdown"] == "done"
    # Layout preserved — still the flat development_status wrapper.
    assert "epics" not in reread
