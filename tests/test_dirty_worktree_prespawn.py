"""Initiative pilot_findings_closure v3 (#5 NEW-5) — dirty reused worktree gate.

Covers :func:`runtime.worker_spawn.spawn_worker`'s pre-Popen dirty-worktree
gate:

  * auto_clean=True (default) → uncommitted residue discarded, worker spawns;
  * auto_clean=False (safe mode) → ``WORKER_HALT_PRESPAWN reason=dirty_worktree``,
    no spawn, worktree left untouched;
  * clean worktree → no-op in both modes;
  * non-git path → gate not applicable, spawn proceeds.

Spec target: +5 tests.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from bmad_orchestrator.agent.tools._common import worker_jsonl_path
from bmad_orchestrator.runtime.worker_spawn import (
    WorkerHaltPrespawnError,
    spawn_worker,
)


def _git_repo(path: Path) -> None:
    """Initialise ``path`` as a git repo with one ``README`` commit."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "t@t"], check=True
    )
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)
    (path / "README").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "seed"], check=True)


def _dirty(path: Path) -> None:
    """Add uncommitted residue: an untracked file + a modified tracked file."""
    (path / "residue.txt").write_text("leftover from aborted run\n", encoding="utf-8")
    (path / "README").write_text("seed\nMODIFIED\n", encoding="utf-8")


def _porcelain(path: Path) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(path), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [ln for ln in out.splitlines() if ln.strip()]


def _read_events(jsonl: Path) -> list[dict[str, object]]:
    if not jsonl.exists():
        return []
    out: list[dict[str, object]] = []
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def _clear_jsonl(worktree: Path) -> None:
    pre = worker_jsonl_path(str(worktree))
    if pre.exists():
        pre.write_text("", encoding="utf-8")


@pytest.mark.asyncio
async def test_dirty_worktree_auto_clean_resets_and_spawns(tmp_path: Path) -> None:
    """auto_clean=True (default) → residue discarded, worker spawns, no halt."""
    wt = tmp_path / "wt"
    _git_repo(wt)
    _dirty(wt)
    assert _porcelain(wt), "precondition: worktree must be dirty"
    _clear_jsonl(wt)

    handle = await spawn_worker(
        worktree=str(wt),
        story_id="1.3",
        branch="feature/1-3",
        mock=True,
    )

    assert handle.story_id == "1.3"
    assert not (wt / "residue.txt").exists(), "git clean -fd must drop untracked residue"
    assert (wt / "README").read_text(encoding="utf-8") == "seed\n", (
        "git reset --hard must restore the modified tracked file"
    )
    events = _read_events(worker_jsonl_path(str(wt)))
    assert not any(e.get("event_type") == "worker_halt_prespawn" for e in events)


@pytest.mark.asyncio
async def test_dirty_worktree_safe_mode_halts(tmp_path: Path) -> None:
    """auto_clean=False → WORKER_HALT_PRESPAWN reason=dirty_worktree, no cleanup."""
    wt = tmp_path / "wt"
    _git_repo(wt)
    _dirty(wt)
    _clear_jsonl(wt)

    with pytest.raises(WorkerHaltPrespawnError) as excinfo:
        await spawn_worker(
            worktree=str(wt),
            story_id="1.3",
            branch="feature/1-3",
            mock=True,
            auto_clean_dirty_worktree=False,
        )

    assert excinfo.value.reason == "dirty_worktree"
    assert excinfo.value.story_id == "1.3"
    # Safe mode must NOT touch the worktree — operator inspects residue by hand.
    assert (wt / "residue.txt").exists()
    assert _porcelain(wt), "safe mode must leave the worktree dirty"

    events = _read_events(worker_jsonl_path(str(wt)))
    halt = [e for e in events if e.get("event_type") == "worker_halt_prespawn"]
    assert len(halt) == 1
    assert halt[0]["reason"] == "dirty_worktree"
    assert halt[0]["story_id"] == "1.3"
    assert int(halt[0]["dirty_count"]) >= 1


@pytest.mark.asyncio
async def test_clean_worktree_noop_auto_clean(tmp_path: Path) -> None:
    """Clean git worktree + auto_clean=True → spawns, no halt event."""
    wt = tmp_path / "wt"
    _git_repo(wt)
    assert not _porcelain(wt)
    _clear_jsonl(wt)

    handle = await spawn_worker(
        worktree=str(wt),
        story_id="2.1",
        branch="feature/2-1",
        mock=True,
    )

    assert handle.story_id == "2.1"
    events = _read_events(worker_jsonl_path(str(wt)))
    assert not any(e.get("event_type") == "worker_halt_prespawn" for e in events)


@pytest.mark.asyncio
async def test_clean_worktree_noop_safe_mode(tmp_path: Path) -> None:
    """Clean git worktree + auto_clean=False → spawns normally, no halt."""
    wt = tmp_path / "wt"
    _git_repo(wt)
    _clear_jsonl(wt)

    handle = await spawn_worker(
        worktree=str(wt),
        story_id="2.2",
        branch="feature/2-2",
        mock=True,
        auto_clean_dirty_worktree=False,
    )

    assert handle.story_id == "2.2"
    events = _read_events(worker_jsonl_path(str(wt)))
    assert not any(e.get("event_type") == "worker_halt_prespawn" for e in events)


@pytest.mark.asyncio
async def test_non_git_worktree_skips_gate(tmp_path: Path) -> None:
    """Non-git path → gate not applicable (git status fails), spawn proceeds."""
    wt = tmp_path / "plain"
    wt.mkdir()
    (wt / "residue.txt").write_text("not a git repo\n", encoding="utf-8")
    _clear_jsonl(wt)

    handle = await spawn_worker(
        worktree=str(wt),
        story_id="3.1",
        branch="feature/3-1",
        mock=True,
        auto_clean_dirty_worktree=False,
    )

    assert handle.story_id == "3.1"
    events = _read_events(worker_jsonl_path(str(wt)))
    assert not any(e.get("event_type") == "worker_halt_prespawn" for e in events)
