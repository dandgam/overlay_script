"""Tests for per-worker cancellation token (Initiative pilot_findings_closure S4).

Spec section: spec/spec_pilot_findings_closure.md §1 #4.

Layout:

* 3 unit — token mechanics (idempotent set / wait / reason capture).
* 2 unit — cancel_worker idempotent + handles unknown/already-dead worker.
* 2 integration — supervisor decision → cancel_worker → WORKER_CANCELLED
  emitted on bus + JSONL audit.
* 1 regression — real subprocess (sleep 9999) killed within 5 s wall-clock.

Total: +8 tests (matches spec acceptance).
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from bmad_orchestrator.runtime.event_loop import EventLoop
from bmad_orchestrator.runtime.worker_cancellation import (
    CancellationToken,
    active_worker_ids,
    build_worker_id,
    cancel_worker,
    get_token,
    get_token_for_story,
    register_worker,
    unregister_worker,
)
from bmad_orchestrator.supervisor.actions import execute_decision
from bmad_orchestrator.supervisor.policy import SupervisorDecision, ToolCall


@pytest.fixture(autouse=True)
def _clear_registry() -> None:
    """Each test starts with an empty registry."""
    for wid in list(active_worker_ids()):
        unregister_worker(wid)


# ── 1) Unit — token mechanics ──────────────────────────────────────────────────


def test_cancellation_token_initial_state() -> None:
    """A fresh token is not set and carries no reason."""
    tok = CancellationToken(worker_id="w1")
    assert tok.is_set is False
    assert tok.reason is None
    assert tok.cancelled_by is None


def test_cancellation_token_set_records_metadata_and_is_idempotent() -> None:
    """First set wins; subsequent calls do not overwrite reason / cancelled_by."""
    tok = CancellationToken(worker_id="w1")
    assert tok.set(reason="stuck", cancelled_by="supervisor") is True
    assert tok.is_set is True
    assert tok.reason == "stuck"
    assert tok.cancelled_by == "supervisor"
    # Second set is a no-op and returns False.
    assert tok.set(reason="other", cancelled_by="user") is False
    assert tok.reason == "stuck"
    assert tok.cancelled_by == "supervisor"


@pytest.mark.asyncio
async def test_cancellation_token_wait_unblocks_after_set() -> None:
    """``wait`` returns once the token is flipped from a sibling task."""
    tok = CancellationToken(worker_id="w1")

    async def _flip() -> None:
        await asyncio.sleep(0.01)
        tok.set(reason="t", cancelled_by="user")

    flip_task = asyncio.create_task(_flip())
    await asyncio.wait_for(tok.wait(), timeout=1.0)
    assert tok.is_set is True
    await flip_task


# ── 2) Unit — cancel_worker semantics ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_worker_idempotent_and_emits_event_once(tmp_path: Path) -> None:
    """First cancel_worker emits a WORKER_CANCELLED line; second returns False."""
    jsonl = tmp_path / "events.jsonl"
    jsonl.touch()
    wid = build_worker_id(story_id="1.1", branch="feature/1-1", pid=0)
    register_worker(
        worker_id=wid,
        story_id="1.1",
        worktree=str(tmp_path),
        branch="feature/1-1",
        jsonl_path=jsonl,
        process=None,
    )

    first = await cancel_worker(wid, reason="stuck", cancelled_by="supervisor")
    second = await cancel_worker(wid, reason="again", cancelled_by="user")

    assert first is True
    assert second is False

    lines = [
        json.loads(line)
        for line in jsonl.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    cancelled = [ln for ln in lines if ln.get("event_type") == "worker_cancelled"]
    assert len(cancelled) == 1
    assert cancelled[0]["reason"] == "stuck"
    assert cancelled[0]["cancelled_by"] == "supervisor"
    assert cancelled[0]["worker_id"] == wid

    unregister_worker(wid)


@pytest.mark.asyncio
async def test_cancel_worker_unknown_id_is_noop(tmp_path: Path) -> None:
    """Calling cancel on an unknown worker_id returns False — never raises."""
    result = await cancel_worker(
        "does-not-exist",
        reason="stuck",
        cancelled_by="supervisor",
    )
    assert result is False


# ── 3) Integration — supervisor decision → cancel_worker → event ──────────────


@pytest.mark.asyncio
async def test_supervisor_cancel_worker_action_flips_token_by_worker_id(
    tmp_path: Path,
) -> None:
    """A ``cancel_worker`` decision with explicit worker_id flips the token."""
    jsonl = tmp_path / "events.jsonl"
    jsonl.touch()
    wid = build_worker_id(story_id="1.2", branch="feature/1-2", pid=0)
    register_worker(
        worker_id=wid,
        story_id="1.2",
        worktree=str(tmp_path),
        branch="feature/1-2",
        jsonl_path=jsonl,
        process=None,
    )

    bus = EventLoop()
    decision = SupervisorDecision(
        action="cancel_worker",
        confidence=1.0,
        reason="rule: worker silent for >300s",
        tier=0,
        rule_id="silent-failure-cancel",
        tool_calls=[ToolCall(name="cancel_worker", args={"worker_id": wid})],
    )
    await execute_decision(
        decision,
        source_event_type="WORKER_SILENT_FAILURE",
        source_payload={"story_id": "1.2"},
        bus=bus,
    )

    tok = get_token(wid)
    assert tok is not None
    assert tok.is_set is True
    assert tok.cancelled_by == "supervisor"
    assert "silent" in (tok.reason or "")

    # WORKER_CANCELLED line landed in worker JSONL.
    lines = [
        json.loads(ln)
        for ln in jsonl.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    assert any(ln.get("event_type") == "worker_cancelled" for ln in lines)

    unregister_worker(wid)


@pytest.mark.asyncio
async def test_supervisor_cancel_worker_falls_back_to_story_id_lookup(
    tmp_path: Path,
) -> None:
    """When the decision carries only ``story_id``, registry lookup resolves
    it to the right worker_id."""
    jsonl = tmp_path / "events.jsonl"
    jsonl.touch()
    wid = build_worker_id(story_id="2.1", branch="feature/2-1", pid=4242)
    register_worker(
        worker_id=wid,
        story_id="2.1",
        worktree=str(tmp_path),
        branch="feature/2-1",
        jsonl_path=jsonl,
        process=None,
    )

    bus = EventLoop()
    decision = SupervisorDecision(
        action="cancel_worker",
        confidence=0.95,
        reason="judge: worker stuck",
        tier=1,
        tool_calls=[ToolCall(name="cancel_worker", args={})],
    )
    await execute_decision(
        decision,
        source_event_type="WORKER_SILENT_FAILURE",
        source_payload={"story_id": "2.1"},
        bus=bus,
    )

    tok = get_token_for_story("2.1")
    assert tok is not None and tok.is_set is True

    unregister_worker(wid)


# ── 4) Regression — real subprocess killed within 5 s ──────────────────────────


@pytest.mark.asyncio
async def test_cancel_worker_kills_stuck_subprocess_under_5s(tmp_path: Path) -> None:
    """A real ``sleep 9999`` subprocess registered with the registry must be
    terminated by ``cancel_worker`` in well under 5 s wall-clock.

    This exercises the SIGTERM → SIGKILL fallback path (the sleep would
    otherwise run for 9999 s).
    """
    jsonl = tmp_path / "events.jsonl"
    jsonl.touch()
    proc = await asyncio.create_subprocess_exec(
        "sleep", "9999",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    wid = build_worker_id(story_id="3.1", branch="feature/3-1", pid=proc.pid)
    register_worker(
        worker_id=wid,
        story_id="3.1",
        worktree=str(tmp_path),
        branch="feature/3-1",
        jsonl_path=jsonl,
        process=proc,
    )

    start = time.monotonic()
    try:
        result = await asyncio.wait_for(
            cancel_worker(wid, reason="stuck", cancelled_by="timeout"),
            timeout=5.0,
        )
        elapsed = time.monotonic() - start
        assert result is True
        # Process exited (returncode set after wait).
        assert proc.returncode is not None
        # Total wall-clock under the 5s spec target.
        assert elapsed < 5.0, f"cancel took {elapsed:.2f}s — spec requires <5s"
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        unregister_worker(wid)


__all__: list[str] = []
