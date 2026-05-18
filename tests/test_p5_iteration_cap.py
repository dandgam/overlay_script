"""P5 Evaluator-Optimizer formalisation — max_review_iterations cap.

Covers:
  * CodeReviewGates.max_review_iterations field (defaults + validation).
  * Pure gate function _gate_iteration_cap edge cases.
  * code_review_subscriber: review_iteration propagation from event payload
    into CODE_REVIEW_VERDICT emit_payload + override → reject when cap tripped.
  * Default single-pass behaviour stays unchanged (review_iteration=1, cap=3
    → no trip, verdict pass-through).
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from bmad_orchestrator.agent.run import (
    _gate_iteration_cap,
    code_review_subscriber,
    configure_code_review_gate,
)
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.runtime.worker_spawn import WorkerHandle
from bmad_orchestrator.skills_repo import CodeReviewGates

# ── Helpers ────────────────────────────────────────────────────────────────


def _make_handle(worktree: str, story_id: str, jsonl_path: Path) -> WorkerHandle:
    return WorkerHandle(
        worktree=worktree,
        story_id=story_id,
        branch=f"feature/{story_id}",
        pid=0,
        jsonl_path=jsonl_path,
        process=None,
        mock=True,
        sandbox_kind="n/a-mock",
    )


def _write_jsonl(path: Path, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(ev) for ev in events) + "\n", encoding="utf-8"
    )


def _collect_emitted(bus: EventLoop) -> list[Event]:
    out: list[Event] = []
    while True:
        try:
            out.append(bus.queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    # Phase 4 hardening #5 adds MERGE_GATE_STAGE_COMPLETED observability events;
    # filter them so pre-split assertions remain valid.
    return [e for e in out if e.type != EventType.MERGE_GATE_STAGE_COMPLETED]


def _success_event(
    story_id: str, worktree: Path, *, review_iteration: int | None = None
) -> Event:
    payload: dict[str, Any] = {
        "story_id": story_id,
        "worktree": str(worktree),
        "status": "success",
    }
    if review_iteration is not None:
        payload["review_iteration"] = review_iteration
    return Event(type=EventType.WORKER_COMPLETED, payload=payload)


@pytest.fixture
def reset_gate_config() -> Iterator[None]:
    configure_code_review_gate(target_project=None, wave=None)
    yield
    configure_code_review_gate(target_project=None, wave=None)


# ── A. Schema field ────────────────────────────────────────────────────────


def test_max_review_iterations_default_is_three() -> None:
    gates = CodeReviewGates()
    assert gates.max_review_iterations == 3


def test_max_review_iterations_rejects_zero_and_negative() -> None:
    with pytest.raises(Exception):  # pydantic ValidationError
        CodeReviewGates(max_review_iterations=0)
    with pytest.raises(Exception):
        CodeReviewGates(max_review_iterations=-1)


def test_max_review_iterations_accepts_custom_value() -> None:
    gates = CodeReviewGates(max_review_iterations=5)
    assert gates.max_review_iterations == 5


# ── B. Pure gate function ──────────────────────────────────────────────────


def test_gate_iteration_cap_no_trip_within_cap() -> None:
    assert _gate_iteration_cap(1, 3) is None
    assert _gate_iteration_cap(3, 3) is None


def test_gate_iteration_cap_trips_when_exceeded() -> None:
    reason = _gate_iteration_cap(4, 3)
    assert reason is not None
    assert "exceeds cap 3" in reason
    assert "runaway loop" in reason


def test_gate_iteration_cap_disabled_when_cap_le_zero() -> None:
    assert _gate_iteration_cap(99, 0) is None
    assert _gate_iteration_cap(99, -1) is None


def test_gate_iteration_cap_no_op_for_missing_iteration() -> None:
    assert _gate_iteration_cap(0, 3) is None


# ── C. Subscriber wiring ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_subscriber_default_single_pass_preserves_verdict(
    reset_gate_config: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No review_iteration in payload → defaults to 1; cap=3 not tripped.

    Backward-compat: existing pilots that never reported the iteration field
    must still pass through unchanged.
    """
    review_jsonl = tmp_path / "review.jsonl"
    _write_jsonl(
        review_jsonl,
        [
            {"event_type": "claude_event", "verdict": "approve", "summary": "LGTM"},
            {"event_type": "worker_completed", "story_id": "s1", "status": "success"},
        ],
    )

    async def fake_spawn(*, worktree: str, story_id: str, wave: str) -> WorkerHandle:
        return _make_handle(worktree, story_id, review_jsonl)

    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker", fake_spawn
    )
    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_merge_gate_quality_worker", fake_spawn
    )

    configure_code_review_gate(
        target_project=tmp_path, wave="1a", gates_override=CodeReviewGates()
    )
    bus = EventLoop()
    await code_review_subscriber(_success_event("s1", tmp_path / "wt"), bus)
    await bus.stop()

    emitted = _collect_emitted(bus)
    assert len(emitted) == 1
    assert emitted[0].type == EventType.CODE_REVIEW_VERDICT
    payload = emitted[0].payload
    assert payload["verdict"] == "approve"
    assert payload["review_iteration"] == 1
    assert "gate_reasons" not in payload


@pytest.mark.asyncio
async def test_subscriber_propagates_review_iteration_into_verdict(
    reset_gate_config: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the worker reports review_iteration=2, the verdict event carries it."""
    review_jsonl = tmp_path / "review.jsonl"
    _write_jsonl(
        review_jsonl,
        [
            {"event_type": "claude_event", "verdict": "approve", "summary": "LGTM"},
            {"event_type": "worker_completed", "story_id": "s1", "status": "success"},
        ],
    )

    async def fake_spawn(*, worktree: str, story_id: str, wave: str) -> WorkerHandle:
        return _make_handle(worktree, story_id, review_jsonl)

    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker", fake_spawn
    )
    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_merge_gate_quality_worker", fake_spawn
    )

    configure_code_review_gate(
        target_project=tmp_path, wave="1a", gates_override=CodeReviewGates()
    )
    bus = EventLoop()
    await code_review_subscriber(
        _success_event("s1", tmp_path / "wt", review_iteration=2), bus
    )
    await bus.stop()

    emitted = _collect_emitted(bus)
    assert len(emitted) == 1
    payload = emitted[0].payload
    assert payload["verdict"] == "approve"
    assert payload["review_iteration"] == 2
    assert "gate_reasons" not in payload  # within cap


@pytest.mark.asyncio
async def test_subscriber_iteration_cap_trips_force_reject(
    reset_gate_config: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """review_iteration > cap → override approve to reject + record reason."""
    review_jsonl = tmp_path / "review.jsonl"
    _write_jsonl(
        review_jsonl,
        [
            {"event_type": "claude_event", "verdict": "approve", "summary": "LGTM"},
            {"event_type": "worker_completed", "story_id": "s1", "status": "success"},
        ],
    )

    async def fake_spawn(*, worktree: str, story_id: str, wave: str) -> WorkerHandle:
        return _make_handle(worktree, story_id, review_jsonl)

    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker", fake_spawn
    )
    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_merge_gate_quality_worker", fake_spawn
    )

    # Tight cap so iteration=2 trips it.
    configure_code_review_gate(
        target_project=tmp_path,
        wave="1a",
        gates_override=CodeReviewGates(max_review_iterations=1),
    )
    bus = EventLoop()
    await code_review_subscriber(
        _success_event("s1", tmp_path / "wt", review_iteration=2), bus
    )
    await bus.stop()

    emitted = _collect_emitted(bus)
    assert len(emitted) == 1
    payload = emitted[0].payload
    assert payload["verdict"] == "reject", (
        "Iteration cap must override approve to reject — P5 runaway guard"
    )
    assert payload["review_iteration"] == 2
    assert "gate_reasons" in payload
    reasons_concat = "; ".join(payload["gate_reasons"])
    assert "exceeds cap 1" in reasons_concat


@pytest.mark.asyncio
async def test_subscriber_handles_malformed_review_iteration(
    reset_gate_config: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-int review_iteration in payload defaults safely to 1 — no crash."""
    review_jsonl = tmp_path / "review.jsonl"
    _write_jsonl(
        review_jsonl,
        [
            {"event_type": "claude_event", "verdict": "approve", "summary": "LGTM"},
            {"event_type": "worker_completed", "story_id": "s1", "status": "success"},
        ],
    )

    async def fake_spawn(*, worktree: str, story_id: str, wave: str) -> WorkerHandle:
        return _make_handle(worktree, story_id, review_jsonl)

    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker", fake_spawn
    )
    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_merge_gate_quality_worker", fake_spawn
    )

    configure_code_review_gate(
        target_project=tmp_path, wave="1a", gates_override=CodeReviewGates()
    )
    bus = EventLoop()
    bad_event = Event(
        type=EventType.WORKER_COMPLETED,
        payload={
            "story_id": "s1",
            "worktree": str(tmp_path / "wt"),
            "status": "success",
            "review_iteration": "garbage",
        },
    )
    await code_review_subscriber(bad_event, bus)
    await bus.stop()

    emitted = _collect_emitted(bus)
    assert len(emitted) == 1
    payload = emitted[0].payload
    assert payload["review_iteration"] == 1
    assert payload["verdict"] == "approve"
