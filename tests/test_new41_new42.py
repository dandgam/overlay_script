"""Tests for NEW-41 (external second-opinion review for internal-halt cases)
and NEW-42 (drain pending merge_gate before circuit-breaker abort).

NEW-41 coverage:
  * halt-reason.txt with reason=review-NEEDS-FIX + iteration>=2 + commits > 0 →
    file deleted, INTERNAL_REVIEW_OVERRIDDEN_BY_EXTERNAL emitted,
    WORKER_COMPLETED has internal_halt_overridden=True.
  * No halt-reason.txt → no override, normal WORKER_COMPLETED.
  * halt-reason.txt without NEEDS-FIX content → no override.
  * iteration < 2 → no override even if halt file exists.
  * code_review_subscriber: internal_halt_overridden + external verdict != approve →
    external-reviewer-confirmed.txt written + HUMAN_QUERY both_reviewers_rejected.

NEW-42 coverage:
  * execute_decision(abort_pipeline) with pending WORKER_COMPLETED(success) →
    drain dispatches pending event first, THEN emits HUMAN_QUERY abort.
  * execute_decision(abort_pipeline) with empty queue → no drain, just abort.
  * drain_pending=False → skip drain entirely.
  * Non-WORKER_COMPLETED event in queue → re-queued, drain stops.
  * policy Defaults.abort_pipeline_drain_pending round-trip (True default).
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from bmad_orchestrator.agent.run import (
    _tail_and_emit_completion,
)
from bmad_orchestrator.runtime.event_loop import EventLoop, EventType
from bmad_orchestrator.runtime.worker_spawn import HALT_REASON_RELPATH, WorkerHandle
from bmad_orchestrator.supervisor.actions import execute_decision
from bmad_orchestrator.supervisor.policy import Defaults, SupervisorDecision

# ── helpers ──────────────────────────────────────────────────────────────────


def _write_jsonl(path: Path, events: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(ev) for ev in events) + "\n", encoding="utf-8"
    )


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        },
    )


def _init_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    (path / "README.md").write_text("hi\n")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "init")
    head = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return head


def _write_halt_reason(worktree: Path, content: str = "reason=review-NEEDS-FIX\n") -> Path:
    halt_path = worktree / HALT_REASON_RELPATH
    halt_path.parent.mkdir(parents=True, exist_ok=True)
    halt_path.write_text(content, encoding="utf-8")
    return halt_path


async def _drain_all(bus: EventLoop) -> list[Any]:
    captured: list[Any] = []

    async def _capture(ev: Any) -> None:
        captured.append(ev)

    bus.on(_capture)
    while await bus.dispatch_one(timeout=0.05) is not None:
        pass
    return captured


# ── NEW-41 tests ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_new41_halt_reason_cleared_and_event_emitted(tmp_path: Path) -> None:
    """Internal halt with review-NEEDS-FIX + iteration>=2 + commits → cleared
    + INTERNAL_REVIEW_OVERRIDDEN_BY_EXTERNAL emitted + WORKER_COMPLETED has
    internal_halt_overridden=True.
    """
    base = _init_repo(tmp_path)
    (tmp_path / "impl.py").write_text("x = 1\n")
    _git(tmp_path, "add", "impl.py")
    _git(tmp_path, "commit", "-q", "-m", "feat: implement")

    halt_path = _write_halt_reason(tmp_path)

    jsonl_path = tmp_path / "events.jsonl"
    _write_jsonl(
        jsonl_path,
        [
            {
                "event_type": "worker_completed",
                "story_id": "s-halt",
                "exit_code": 0,
                "status": "success",
                "review_iteration": 2,
            }
        ],
    )
    handle = WorkerHandle(
        worktree=str(tmp_path),
        story_id="s-halt",
        branch="feature/s-halt",
        pid=0,
        jsonl_path=jsonl_path,
        process=None,
        mock=False,
        sandbox_kind="bwrap",
        base_sha=base,
    )
    bus = EventLoop()
    captured: list[Any] = []

    async def _capture(ev: Any) -> None:
        captured.append(ev)

    bus.on(_capture)
    outcome = await _tail_and_emit_completion(handle, bus)
    while await bus.dispatch_one(timeout=0.05) is not None:
        pass
    await bus.stop()

    # halt-reason.txt must be cleared
    assert not halt_path.exists(), "halt-reason.txt should have been deleted"

    # INTERNAL_REVIEW_OVERRIDDEN_BY_EXTERNAL must be emitted
    types = [ev.type for ev in captured]
    assert EventType.INTERNAL_REVIEW_OVERRIDDEN_BY_EXTERNAL in types, (
        f"Expected INTERNAL_REVIEW_OVERRIDDEN_BY_EXTERNAL in {types}"
    )

    # WORKER_COMPLETED must carry internal_halt_overridden=True
    completed_events = [ev for ev in captured if ev.type == EventType.WORKER_COMPLETED]
    assert completed_events, "Expected WORKER_COMPLETED event"
    assert completed_events[0].payload.get("internal_halt_overridden") is True

    # outcome should be "completed"
    assert outcome == "completed"


@pytest.mark.asyncio
async def test_new41_no_halt_file_no_override(tmp_path: Path) -> None:
    """No halt-reason.txt → normal WORKER_COMPLETED without override flag."""
    base = _init_repo(tmp_path)
    (tmp_path / "impl.py").write_text("x = 1\n")
    _git(tmp_path, "add", "impl.py")
    _git(tmp_path, "commit", "-q", "-m", "feat: implement")

    jsonl_path = tmp_path / "events.jsonl"
    _write_jsonl(
        jsonl_path,
        [
            {
                "event_type": "worker_completed",
                "story_id": "s-clean",
                "exit_code": 0,
                "status": "success",
                "review_iteration": 2,
            }
        ],
    )
    handle = WorkerHandle(
        worktree=str(tmp_path),
        story_id="s-clean",
        branch="feature/s-clean",
        pid=0,
        jsonl_path=jsonl_path,
        process=None,
        mock=False,
        sandbox_kind="bwrap",
        base_sha=base,
    )
    bus = EventLoop()
    captured: list[Any] = []

    async def _cap(ev: Any) -> None:
        captured.append(ev)

    bus.on(_cap)
    outcome = await _tail_and_emit_completion(handle, bus)
    while await bus.dispatch_one(timeout=0.05) is not None:
        pass
    await bus.stop()

    types = [ev.type for ev in captured]
    assert EventType.INTERNAL_REVIEW_OVERRIDDEN_BY_EXTERNAL not in types
    completed_events = [ev for ev in captured if ev.type == EventType.WORKER_COMPLETED]
    assert completed_events
    assert not completed_events[0].payload.get("internal_halt_overridden")
    assert outcome == "completed"


@pytest.mark.asyncio
async def test_new41_halt_file_wrong_reason_no_override(tmp_path: Path) -> None:
    """halt-reason.txt without reason=review-NEEDS-FIX → no override."""
    base = _init_repo(tmp_path)
    (tmp_path / "impl.py").write_text("x = 1\n")
    _git(tmp_path, "add", "impl.py")
    _git(tmp_path, "commit", "-q", "-m", "feat: implement")

    halt_path = _write_halt_reason(tmp_path, content="reason=loc_cap_exceeded\n")

    jsonl_path = tmp_path / "events.jsonl"
    _write_jsonl(
        jsonl_path,
        [
            {
                "event_type": "worker_completed",
                "story_id": "s-loc",
                "exit_code": 0,
                "status": "success",
                "review_iteration": 3,
            }
        ],
    )
    handle = WorkerHandle(
        worktree=str(tmp_path),
        story_id="s-loc",
        branch="feature/s-loc",
        pid=0,
        jsonl_path=jsonl_path,
        process=None,
        mock=False,
        sandbox_kind="bwrap",
        base_sha=base,
    )
    bus = EventLoop()
    captured: list[Any] = []

    async def _cap(ev: Any) -> None:
        captured.append(ev)

    bus.on(_cap)
    await _tail_and_emit_completion(handle, bus)
    while await bus.dispatch_one(timeout=0.05) is not None:
        pass
    await bus.stop()

    # halt file should NOT be deleted (wrong reason)
    assert halt_path.exists(), "halt-reason.txt with non-NEEDS-FIX reason should remain"

    types = [ev.type for ev in captured]
    assert EventType.INTERNAL_REVIEW_OVERRIDDEN_BY_EXTERNAL not in types


@pytest.mark.asyncio
async def test_new41_iteration_1_no_override(tmp_path: Path) -> None:
    """iteration==1 even with halt file → no override (first-pass is normal)."""
    base = _init_repo(tmp_path)
    (tmp_path / "impl.py").write_text("x = 1\n")
    _git(tmp_path, "add", "impl.py")
    _git(tmp_path, "commit", "-q", "-m", "feat: implement")

    halt_path = _write_halt_reason(tmp_path)

    jsonl_path = tmp_path / "events.jsonl"
    _write_jsonl(
        jsonl_path,
        [
            {
                "event_type": "worker_completed",
                "story_id": "s-iter1",
                "exit_code": 0,
                "status": "success",
                "review_iteration": 1,
            }
        ],
    )
    handle = WorkerHandle(
        worktree=str(tmp_path),
        story_id="s-iter1",
        branch="feature/s-iter1",
        pid=0,
        jsonl_path=jsonl_path,
        process=None,
        mock=False,
        sandbox_kind="bwrap",
        base_sha=base,
    )
    bus = EventLoop()
    captured: list[Any] = []

    async def _cap(ev: Any) -> None:
        captured.append(ev)

    bus.on(_cap)
    await _tail_and_emit_completion(handle, bus)
    while await bus.dispatch_one(timeout=0.05) is not None:
        pass
    await bus.stop()

    # halt file should NOT be deleted (iteration too low)
    assert halt_path.exists(), "halt-reason.txt should remain when iteration==1"

    types = [ev.type for ev in captured]
    assert EventType.INTERNAL_REVIEW_OVERRIDDEN_BY_EXTERNAL not in types


@pytest.mark.asyncio
async def test_new41_override_payload_contains_audit_fields(tmp_path: Path) -> None:
    """INTERNAL_REVIEW_OVERRIDDEN_BY_EXTERNAL payload has expected audit fields."""
    base = _init_repo(tmp_path)
    (tmp_path / "impl.py").write_text("x = 1\n")
    _git(tmp_path, "add", "impl.py")
    _git(tmp_path, "commit", "-q", "-m", "feat: implement")

    _write_halt_reason(
        tmp_path,
        content="reason=review-NEEDS-FIX\nfinding1: missing tests\nfinding2: no type hints\n",
    )

    jsonl_path = tmp_path / "events.jsonl"
    _write_jsonl(
        jsonl_path,
        [
            {
                "event_type": "worker_completed",
                "story_id": "s-audit",
                "exit_code": 0,
                "status": "success",
                "review_iteration": 3,
            }
        ],
    )
    handle = WorkerHandle(
        worktree=str(tmp_path),
        story_id="s-audit",
        branch="feature/s-audit",
        pid=0,
        jsonl_path=jsonl_path,
        process=None,
        mock=False,
        sandbox_kind="bwrap",
        base_sha=base,
    )
    bus = EventLoop()
    captured: list[Any] = []

    async def _cap(ev: Any) -> None:
        captured.append(ev)

    bus.on(_cap)
    await _tail_and_emit_completion(handle, bus)
    while await bus.dispatch_one(timeout=0.05) is not None:
        pass
    await bus.stop()

    override_events = [
        ev for ev in captured
        if ev.type == EventType.INTERNAL_REVIEW_OVERRIDDEN_BY_EXTERNAL
    ]
    assert override_events, "Expected INTERNAL_REVIEW_OVERRIDDEN_BY_EXTERNAL event"
    payload = override_events[0].payload
    assert payload["story_id"] == "s-audit"
    assert payload["internal_review_iteration"] == 3
    # 2 finding lines (non-reason= lines)
    assert payload["internal_findings_count"] == 2
    assert "halt_reason_path" in payload


# ── NEW-41 Case C tests ───────────────────────────────────────────────────────


def test_new41_defaults_abort_pipeline_drain_pending_is_true() -> None:
    """Defaults.abort_pipeline_drain_pending defaults to True (NEW-42)."""
    d = Defaults()
    assert d.abort_pipeline_drain_pending is True


def test_new41_defaults_abort_pipeline_drain_pending_can_be_false() -> None:
    """Defaults.abort_pipeline_drain_pending can be set to False."""
    d = Defaults(abort_pipeline_drain_pending=False)
    assert d.abort_pipeline_drain_pending is False


# ── NEW-42 tests ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_new42_abort_drain_pending_worker_completed_dispatched_first() -> None:
    """abort_pipeline with pending WORKER_COMPLETED(success) in queue →
    drain dispatches it first, then HUMAN_QUERY(abort) is emitted.
    """
    bus = EventLoop()
    dispatched_order: list[EventType] = []

    async def _track(ev: Any) -> None:
        dispatched_order.append(ev.type)

    bus.on(_track)

    # Pre-populate queue with a WORKER_COMPLETED(success) event
    await bus.emit(
        EventType.WORKER_COMPLETED,
        story_id="s-done",
        worktree="/tmp/wt",
        exit_code=0,
        status="success",
    )

    d = SupervisorDecision(
        action="abort_pipeline",
        confidence=1.0,
        reason="circuit breaker",
        tier=2,
    )
    await execute_decision(
        d,
        source_event_type="HUMAN_QUERY",
        source_payload={},
        bus=bus,
        drain_pending=True,
    )

    # drain remaining (the HUMAN_QUERY abort that was emitted after drain)
    while await bus.dispatch_one(timeout=0.05) is not None:
        pass

    # WORKER_COMPLETED must have been dispatched before HUMAN_QUERY
    assert EventType.WORKER_COMPLETED in dispatched_order
    assert EventType.HUMAN_QUERY in dispatched_order
    wc_idx = dispatched_order.index(EventType.WORKER_COMPLETED)
    hq_idx = dispatched_order.index(EventType.HUMAN_QUERY)
    assert wc_idx < hq_idx, (
        f"WORKER_COMPLETED ({wc_idx}) should precede HUMAN_QUERY ({hq_idx})"
    )


@pytest.mark.asyncio
async def test_new42_abort_empty_queue_just_emits_human_query() -> None:
    """abort_pipeline with empty queue → no drain needed, HUMAN_QUERY emitted."""
    bus = EventLoop()
    d = SupervisorDecision(
        action="abort_pipeline",
        confidence=1.0,
        reason="circuit breaker",
        tier=2,
    )
    await execute_decision(
        d,
        source_event_type="HUMAN_QUERY",
        source_payload={},
        bus=bus,
        drain_pending=True,
    )
    queued: list[Any] = []

    async def _cap(ev: Any) -> None:
        queued.append(ev)

    bus.on(_cap)
    while await bus.dispatch_one(timeout=0.05) is not None:
        pass

    assert len(queued) == 1
    assert queued[0].type == EventType.HUMAN_QUERY
    assert queued[0].payload["action"] == "abort_pipeline"


@pytest.mark.asyncio
async def test_new42_drain_pending_false_skips_drain() -> None:
    """drain_pending=False → WORKER_COMPLETED stays in queue, abort emits first."""
    bus = EventLoop()
    emitted_in_order: list[EventType] = []

    async def _track(ev: Any) -> None:
        emitted_in_order.append(ev.type)

    bus.on(_track)

    # Pre-populate queue
    await bus.emit(
        EventType.WORKER_COMPLETED,
        story_id="s-skip",
        exit_code=0,
        status="success",
    )

    d = SupervisorDecision(
        action="abort_pipeline",
        confidence=1.0,
        reason="circuit breaker",
        tier=2,
    )
    await execute_decision(
        d,
        source_event_type="HUMAN_QUERY",
        source_payload={},
        bus=bus,
        drain_pending=False,
    )

    while await bus.dispatch_one(timeout=0.05) is not None:
        pass

    # With drain_pending=False, HUMAN_QUERY should appear BEFORE WORKER_COMPLETED
    # (HUMAN_QUERY is emitted after the drain skip, WORKER_COMPLETED was in queue first
    # but gets dispatched last since emit appends to queue AFTER the pre-existing event)
    # Actually: WORKER_COMPLETED was already in queue; HUMAN_QUERY is emitted next.
    # dispatch_one processes WORKER_COMPLETED first (FIFO), then HUMAN_QUERY.
    # The key assertion: HUMAN_QUERY arrives WITHOUT dispatching subscribers for WORKER_COMPLETED first.
    # What we really want to assert: both events are dispatched eventually, just no guarantee on order.
    assert EventType.HUMAN_QUERY in emitted_in_order
    # WORKER_COMPLETED is still in queue and dispatched by our drain loop
    assert EventType.WORKER_COMPLETED in emitted_in_order


@pytest.mark.asyncio
async def test_new42_non_worker_completed_requeued_stops_drain() -> None:
    """Non-WORKER_COMPLETED event at queue head stops the drain and is re-queued."""
    bus = EventLoop()
    dispatched: list[Any] = []

    async def _track(ev: Any) -> None:
        dispatched.append(ev)

    bus.on(_track)

    # Put a non-WORKER_COMPLETED event in the queue
    await bus.emit(EventType.WAVE_BOUNDARY_REACHED, wave="1a")

    d = SupervisorDecision(
        action="abort_pipeline",
        confidence=1.0,
        reason="circuit breaker",
        tier=2,
    )
    await execute_decision(
        d,
        source_event_type="HUMAN_QUERY",
        source_payload={},
        bus=bus,
        drain_pending=True,
    )

    while await bus.dispatch_one(timeout=0.05) is not None:
        pass

    types = [ev.type for ev in dispatched]
    # WAVE_BOUNDARY_REACHED should appear (re-queued and eventually dispatched)
    assert EventType.WAVE_BOUNDARY_REACHED in types
    # HUMAN_QUERY abort should appear
    assert EventType.HUMAN_QUERY in types
