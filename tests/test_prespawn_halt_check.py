"""Initiative pilot_findings_closure S6 (#7 P2) — halt-reason pre-flight.

Covers :func:`runtime.worker_spawn.spawn_worker`'s pre-Popen halt gate:

  * 3 unit — file present blocks spawn, absent allows spawn, auto_clear
    drops file then proceeds.
  * 2 integration — payload of the WORKER_HALT_PRESPAWN JSONL audit,
    semantics of the error fields (story_id propagation).

Spec target: +5 tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bmad_orchestrator.agent.tools._common import worker_jsonl_path
from bmad_orchestrator.runtime.worker_spawn import (
    HALT_REASON_RELPATH,
    WorkerHaltPrespawnError,
    spawn_worker,
)


def _seed_halt_reason(worktree: Path, reason: str) -> Path:
    halt = worktree / HALT_REASON_RELPATH
    halt.parent.mkdir(parents=True, exist_ok=True)
    halt.write_text(reason, encoding="utf-8")
    return halt


def _read_events(jsonl: Path) -> list[dict[str, object]]:
    if not jsonl.exists():
        return []
    out: list[dict[str, object]] = []
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        out.append(json.loads(line))
    return out


@pytest.mark.asyncio
async def test_halt_reason_present_blocks_spawn(tmp_path: Path) -> None:
    """halt-reason.txt present → WorkerHaltPrespawnError + JSONL event."""
    halt = _seed_halt_reason(tmp_path, "loc_cap_exceeded by Stage 4\nlater line")

    pre_jsonl = worker_jsonl_path(str(tmp_path))
    if pre_jsonl.exists():
        pre_jsonl.write_text("", encoding="utf-8")

    with pytest.raises(WorkerHaltPrespawnError) as excinfo:
        await spawn_worker(
            worktree=str(tmp_path),
            story_id="3.7",
            branch="feature/3-7",
            mock=True,
        )

    err = excinfo.value
    assert err.story_id == "3.7"
    assert err.halt_path == str(halt)
    assert err.reason == "loc_cap_exceeded by Stage 4"
    assert halt.exists(), "halt file must NOT be cleared on default path"

    events = _read_events(worker_jsonl_path(str(tmp_path)))
    halt_events = [e for e in events if e.get("event_type") == "worker_halt_prespawn"]
    assert len(halt_events) == 1
    assert halt_events[0]["story_id"] == "3.7"
    assert halt_events[0]["reason"] == "loc_cap_exceeded by Stage 4"


@pytest.mark.asyncio
async def test_no_halt_file_allows_spawn(tmp_path: Path) -> None:
    """No halt-reason.txt → mock worker spawns normally, no halt event."""
    pre_jsonl = worker_jsonl_path(str(tmp_path))
    if pre_jsonl.exists():
        pre_jsonl.write_text("", encoding="utf-8")

    handle = await spawn_worker(
        worktree=str(tmp_path),
        story_id="3.7",
        branch="feature/3-7",
        mock=True,
    )

    assert handle.mock is True
    assert handle.story_id == "3.7"
    events = _read_events(worker_jsonl_path(str(tmp_path)))
    assert not any(e.get("event_type") == "worker_halt_prespawn" for e in events)


@pytest.mark.asyncio
async def test_auto_clear_halt_removes_file_and_spawns(tmp_path: Path) -> None:
    """auto_clear_halt=True → file deleted, spawn proceeds, no halt event."""
    halt = _seed_halt_reason(tmp_path, "stale_marker_from_prior_run")

    pre_jsonl = worker_jsonl_path(str(tmp_path))
    if pre_jsonl.exists():
        pre_jsonl.write_text("", encoding="utf-8")

    handle = await spawn_worker(
        worktree=str(tmp_path),
        story_id="3.7",
        branch="feature/3-7",
        mock=True,
        auto_clear_halt=True,
    )

    assert handle.mock is True
    assert not halt.exists(), "auto_clear_halt must wipe the halt file"
    events = _read_events(worker_jsonl_path(str(tmp_path)))
    assert not any(e.get("event_type") == "worker_halt_prespawn" for e in events)


@pytest.mark.asyncio
async def test_halt_event_payload_carries_full_path(tmp_path: Path) -> None:
    """JSONL halt event must include worktree, halt_path, and trimmed reason."""
    halt = _seed_halt_reason(tmp_path, "  budget_halt scope=story  ")

    pre_jsonl = worker_jsonl_path(str(tmp_path))
    if pre_jsonl.exists():
        pre_jsonl.write_text("", encoding="utf-8")

    with pytest.raises(WorkerHaltPrespawnError):
        await spawn_worker(
            worktree=str(tmp_path),
            story_id="9.1",
            branch="feature/9-1",
            mock=True,
        )

    events = _read_events(worker_jsonl_path(str(tmp_path)))
    halt_events = [e for e in events if e.get("event_type") == "worker_halt_prespawn"]
    assert len(halt_events) == 1
    payload = halt_events[0]
    assert payload["worktree"] == str(tmp_path)
    assert payload["halt_path"] == str(halt)
    assert payload["reason"] == "budget_halt scope=story"


@pytest.mark.asyncio
async def test_empty_halt_file_still_blocks(tmp_path: Path) -> None:
    """Zero-byte halt-reason.txt is still a halt — empty reason, blocked spawn."""
    halt = _seed_halt_reason(tmp_path, "")

    pre_jsonl = worker_jsonl_path(str(tmp_path))
    if pre_jsonl.exists():
        pre_jsonl.write_text("", encoding="utf-8")

    with pytest.raises(WorkerHaltPrespawnError) as excinfo:
        await spawn_worker(
            worktree=str(tmp_path),
            story_id="5.2",
            branch="feature/5-2",
            mock=True,
        )

    assert excinfo.value.reason == ""
    assert halt.exists()
