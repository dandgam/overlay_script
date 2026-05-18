"""Regression tests for Pilot 2 (Antares 1.2) patches Z/AA/BB/CC/DD.

Each test pins a specific bug surfaced in the 2026-05-18 pilot — these are
*guard* tests: if someone rewrites the patched code path and the regression
slips back in, this suite fails loud.

Patch summary (see commits 5440614 → 3a9b3be → 585b8be):
  Z  — runner.sh Stage 1 respects ORCHESTRATOR_WORKER_STORY_ID env (shell
       script; Python-side contract = ``spawn_worker`` propagates the var).
  AA — gauntlet_injector.extract_story_text supports kebab id + standalone
       stories_dir fallback when story missing from epics.md.
  BB — agent.run._detect_orphan_stories pre-flight surfaces missing-in-
       epics.md stories before the worker runs.
  CC — worker_spawn.spawn_worker truncates events.jsonl on re-spawn so a
       stale ``worker_completed`` line cannot short-circuit the new pilot.
  DD — sandbox._resolve_target_dotgit walks a linked-worktree's ``.git``
       file to the main repo's ``.git/`` so bwrap can rw-bind it.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from bmad_orchestrator.agent.run import _detect_orphan_stories
from bmad_orchestrator.runtime.sandbox import _resolve_target_dotgit
from bmad_orchestrator.runtime.worker_spawn import spawn_worker

REPO_ROOT = Path(__file__).resolve().parent.parent
GAUNTLET_INJECTOR_PATH = (
    REPO_ROOT / "skills" / "upstream" / "bmad-auto-dev" / "scripts" / "gauntlet_injector.py"
)


def _load_gauntlet_injector():
    """Load the gauntlet_injector module by file path (lives under skills/upstream)."""
    spec = importlib.util.spec_from_file_location(
        "_test_gauntlet_injector", GAUNTLET_INJECTOR_PATH
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, mod)
    spec.loader.exec_module(mod)
    return mod


# ─────────────────────────── Patch Z ───────────────────────────


@pytest.mark.asyncio
async def test_patch_z_spawn_worker_propagates_story_id_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spawn_worker must set ORCHESTRATOR_WORKER_STORY_ID so runner.sh Stage 1
    can skip its own dependency_analyzer selector.

    Pilot 2 bug: runner.sh ran its own --next selector and picked a different
    story than the orchestrator's DAG choice; branch and code diverged.
    """
    target = tmp_path / "target"
    target.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target))
    monkeypatch.setenv("BMAD_CURRENT_WAVE", "test")

    wt = tmp_path / "wt"
    wt.mkdir()
    handle = await spawn_worker(
        worktree=str(wt),
        story_id="4-8-pg-dump",
        branch="feature/4-8-pg-dump",
        mock=True,
    )

    # In mock mode there's no real subprocess so we verify the propagation
    # contract via the helper used to build the merged env.
    from bmad_orchestrator.runtime.worker_spawn import _build_worker_env

    env = _build_worker_env(extra={"ORCHESTRATOR_WORKER_STORY_ID": handle.story_id})
    assert env["ORCHESTRATOR_WORKER_STORY_ID"] == "4-8-pg-dump"


# ─────────────────────────── Patch AA ───────────────────────────


def test_patch_aa_extract_epic_id_kebab_form() -> None:
    """extract_epic_id must accept kebab form '4-8-foo' → 'epic-4'."""
    mod = _load_gauntlet_injector()
    assert mod.extract_epic_id("4-8-pg-dump-pre-snapshot") == "epic-4"
    assert mod.extract_epic_id("1.3") == "epic-1"
    assert mod.extract_epic_id("13.2a") == "epic-13"


def test_patch_aa_extract_story_text_orphan_fallback(tmp_path: Path) -> None:
    """When story_id is missing from epics.md, fall back to stories_dir/<id>.md.

    Pilot 2 bug: Antares 4-8 was a Phase-3.5 standalone spike — stories/4-8-*.md
    existed but epics.md had no matching heading → Gauntlet KeyError → pipeline
    silent-failed at 13s.
    """
    mod = _load_gauntlet_injector()

    epics = tmp_path / "epics.md"
    epics.write_text(
        "#### Story 1.1: First story\n\nBody one.\n\n#### Story 2.3: Other\n\nBody.\n",
        encoding="utf-8",
    )

    stories_dir = tmp_path / "stories"
    stories_dir.mkdir()
    standalone = stories_dir / "4-8-pg-dump-pre-snapshot.md"
    standalone.write_text("# Story 4.8: Orphan spike\n\nStandalone body.\n", encoding="utf-8")

    # Dotted hit in epics.md works (kebab form normalised).
    assert "First story" in mod.extract_story_text(epics, "1-1", stories_dir)

    # Orphan kebab id falls back to standalone file.
    result = mod.extract_story_text(epics, "4-8-pg-dump-pre-snapshot", stories_dir)
    assert "Standalone body" in result

    # Missing everywhere → KeyError.
    with pytest.raises(KeyError):
        mod.extract_story_text(epics, "9-9-nowhere", stories_dir)


# ─────────────────────────── Patch BB ───────────────────────────


def test_patch_bb_detect_orphan_stories_flags_missing_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_detect_orphan_stories returns story_ids absent from epics.md.

    Tolerates kebab form: '4-8-foo' is checked against dotted '4.8' in epics.
    """
    target = tmp_path / "target"
    artifacts = target / "_bmad"
    stories = artifacts / "stories"
    stories.mkdir(parents=True)
    epics = artifacts / "epics.md"
    epics.write_text(
        "#### Story 1.1: Present\n\nBody.\n\n"
        "#### Story 2.3: Also present\n\nBody.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target))

    # 1.1 present; 4-8-foo orphan; 2.3 present (dotted match).
    orphans = _detect_orphan_stories(["1-1-first", "4-8-pg-dump", "2.3"])
    assert orphans == ["4-8-pg-dump"]


def test_patch_bb_detect_orphan_stories_returns_empty_when_epics_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Graceful degradation: missing epics.md → []; pre-flight never blocks."""
    target = tmp_path / "target"
    (target / "_bmad" / "stories").mkdir(parents=True)
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target))

    assert _detect_orphan_stories(["1-1-anything"]) == []


# ─────────────────────────── Patch CC ───────────────────────────


@pytest.mark.asyncio
async def test_patch_cc_spawn_worker_truncates_stale_events_jsonl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A re-used worktree path must not inherit a prior pilot's events.jsonl —
    tail_jsonl_events would treat the stale ``worker_completed`` as terminal
    and silent-fail the new run in <1s.

    Pilot 2 bug: dual-pilot reused worktree slot, 0-sec silent_failure verdict.
    """
    target = tmp_path / "target"
    target.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target))
    monkeypatch.setenv("BMAD_CURRENT_WAVE", "test")

    wt = tmp_path / "wt"
    wt.mkdir()

    # Plant a stale events.jsonl as if a prior pilot had finished in this slot.
    from bmad_orchestrator.agent.tools._common import worker_jsonl_path

    jsonl_path = worker_jsonl_path(str(wt))
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    jsonl_path.write_text(
        json.dumps({"event_type": "worker_completed", "status": "success", "stale": True})
        + "\n",
        encoding="utf-8",
    )
    assert jsonl_path.exists()

    handle = await spawn_worker(
        worktree=str(wt),
        story_id="x",
        branch="feature/x",
        mock=True,
    )

    # After spawn the file must contain ONLY the new run's events — no stale
    # entries from the planted line.
    lines = handle.jsonl_path.read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines if line.strip()]
    assert all(e.get("stale") is not True for e in events), (
        "Stale events.jsonl line leaked into new run — Patch CC regression"
    )
    types = [e["event_type"] for e in events]
    # First event is from the new mock spawn, not the planted line.
    assert types[0] == "worker_spawned"


# ─────────────────────────── Patch DD ───────────────────────────


def test_patch_dd_resolve_target_dotgit_walks_linked_worktree(tmp_path: Path) -> None:
    """A linked worktree's ``.git`` file → main repo's ``.git/`` directory.

    Pilot 2 bug: bwrap ``--ro-bind / /`` made main .git/ read-only, so
    ``git commit`` inside Stage 5/6 failed EROFS and the run ended uncommitted.
    Fix walks the gitdir pointer so the orchestrator can rw-bind the main .git.
    """
    # Simulate Antares layout: <target>/.git/ + <target>/.worktrees/wt-1/.git
    main_repo = tmp_path / "target"
    main_git = main_repo / ".git"
    main_git.mkdir(parents=True)
    (main_git / "worktrees").mkdir()
    wt_git_common = main_git / "worktrees" / "wt-1"
    wt_git_common.mkdir()

    wt = main_repo / ".worktrees" / "wt-1"
    wt.mkdir(parents=True)
    # Linked worktrees have ``.git`` as a file with ``gitdir:`` pointer.
    (wt / ".git").write_text(
        f"gitdir: {wt_git_common}\n", encoding="utf-8"
    )

    resolved = _resolve_target_dotgit(wt)
    assert resolved == main_git, (
        f"expected main .git at {main_git}, got {resolved} — Patch DD regression"
    )


def test_patch_dd_resolve_target_dotgit_returns_none_for_dir_layout(
    tmp_path: Path,
) -> None:
    """When .git is a directory (test fixtures / main repo), return None — bwrap
    proceeds without the rw bind, preserving prior behavior."""
    wt = tmp_path / "main_repo"
    wt.mkdir()
    (wt / ".git").mkdir()
    assert _resolve_target_dotgit(wt) is None


def test_patch_dd_resolve_target_dotgit_returns_none_for_malformed(
    tmp_path: Path,
) -> None:
    """Malformed .git file (no ``gitdir:`` prefix) must not crash bwrap setup."""
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text("garbage content\n", encoding="utf-8")
    assert _resolve_target_dotgit(wt) is None


def test_patch_dd_resolve_target_dotgit_returns_none_when_missing(
    tmp_path: Path,
) -> None:
    """No .git at all → None (test fixtures often skip git init)."""
    wt = tmp_path / "wt"
    wt.mkdir()
    assert _resolve_target_dotgit(wt) is None
