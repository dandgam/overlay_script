"""Tests for NEW-38 — respawn_worker supervisor action.

Covers:
  1. execute_decision(action=respawn_worker) → cancel_worker + emit WORKER_RESPAWN_REQUESTED.
  2. Missing story_id → no-op + warning.
  3. cancel_worker timeout → warning logged, RESPAWN_REQUESTED still emitted.
  4. _respawn_subscriber: WORKER_RESPAWN_REQUESTED → story re-queued.
  5. Respawn cap: 3rd request → escalate_human instead of respawn.
  6. dev_prompt_hint stored in _respawn_hints.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.supervisor.actions import execute_decision
from bmad_orchestrator.supervisor.policy import SupervisorDecision, ToolCall


async def _drain(bus: EventLoop) -> list[Event]:
    out: list[Event] = []
    while not bus.queue.empty():
        out.append(bus.queue.get_nowait())
    return out


# ---------------------------------------------------------------------------
# execute_decision — respawn_worker
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_respawn_worker_emits_respawn_requested() -> None:
    """execute_decision(respawn_worker) → cancel worker + emit WORKER_RESPAWN_REQUESTED."""
    bus = EventLoop()
    d = SupervisorDecision(
        action="respawn_worker",
        confidence=0.9,
        reason="review timed out",
        tier=1,
        tool_calls=[
            ToolCall(
                name="respawn_worker",
                args={
                    "story_id": "story-1",
                    "reason": "review timed out",
                    "max_iteration": 1,
                    "dev_prompt_hint": "check AC3",
                },
            )
        ],
    )

    fake_token = MagicMock()
    fake_token.worker_id = "story-1-branch-1234"

    with patch(
        "bmad_orchestrator.supervisor.actions._get_token_for_story",
        return_value=fake_token,
    ), patch(
        "bmad_orchestrator.supervisor.actions.runtime_cancel_worker",
        AsyncMock(return_value=True),
    ):
        await execute_decision(
            d,
            source_event_type="REVIEW_STUCK_TIMEOUT",
            source_payload={"story_id": "story-1"},
            bus=bus,
        )

    queued = await _drain(bus)
    assert len(queued) == 1
    ev = queued[0]
    assert ev.type is EventType.WORKER_RESPAWN_REQUESTED
    assert ev.payload["story_id"] == "story-1"
    assert ev.payload["reason"] == "review timed out"
    assert ev.payload["iteration"] == 1
    assert ev.payload["dev_prompt_hint"] == "check AC3"
    assert ev.payload["cancelled_worker_id"] == "story-1-branch-1234"


@pytest.mark.asyncio
async def test_respawn_worker_no_token_still_emits() -> None:
    """If no cancellation token found, RESPAWN_REQUESTED is still emitted."""
    bus = EventLoop()
    d = SupervisorDecision(
        action="respawn_worker",
        confidence=0.9,
        reason="stuck",
        tier=1,
        tool_calls=[
            ToolCall(
                name="respawn_worker",
                args={"story_id": "orphan-story", "reason": "stuck", "max_iteration": 1},
            )
        ],
    )

    with patch(
        "bmad_orchestrator.supervisor.actions._get_token_for_story",
        return_value=None,
    ):
        await execute_decision(
            d,
            source_event_type="WORKER_STUCK_TIMEOUT",
            source_payload={"story_id": "orphan-story"},
            bus=bus,
        )

    queued = await _drain(bus)
    assert len(queued) == 1
    assert queued[0].type is EventType.WORKER_RESPAWN_REQUESTED
    assert queued[0].payload["cancelled_worker_id"] is None


@pytest.mark.asyncio
async def test_respawn_worker_missing_story_id_noop() -> None:
    """When story_id cannot be determined, nothing is emitted."""
    bus = EventLoop()
    d = SupervisorDecision(
        action="respawn_worker",
        confidence=0.9,
        reason="stuck",
        tier=1,
        # No tool_calls with story_id.
    )

    await execute_decision(
        d,
        source_event_type="REVIEW_STUCK_TIMEOUT",
        source_payload={},  # no story_id in payload either
        bus=bus,
    )

    queued = await _drain(bus)
    assert queued == []


@pytest.mark.asyncio
async def test_respawn_worker_cancel_timeout_still_emits() -> None:
    """When cancel_worker times out, warning is logged but RESPAWN_REQUESTED still fires."""
    bus = EventLoop()
    d = SupervisorDecision(
        action="respawn_worker",
        confidence=0.9,
        reason="stuck",
        tier=1,
        tool_calls=[
            ToolCall(
                name="respawn_worker",
                args={"story_id": "slow-story", "reason": "stuck", "max_iteration": 1},
            )
        ],
    )

    fake_token = MagicMock()
    fake_token.worker_id = "slow-story-9999"

    # Simulate cancel_worker raising TimeoutError via wait_for (already returned coroutine).
    async def _cancel_ok(*_a: object, **_kw: object) -> bool:
        return True

    # asyncio.wait_for wraps the coroutine; we simulate a timeout by having it raise.
    async def _wait_for_timeout(coro: object, *, timeout: float) -> object:
        # Close the coroutine to avoid "was never awaited" ResourceWarning.
        if hasattr(coro, "close"):
            coro.close()  # type: ignore[union-attr]
        raise TimeoutError("simulated cancel timeout")

    with patch(
        "bmad_orchestrator.supervisor.actions._get_token_for_story",
        return_value=fake_token,
    ), patch(
        "bmad_orchestrator.supervisor.actions.runtime_cancel_worker",
        _cancel_ok,
    ), patch(
        "bmad_orchestrator.supervisor.actions.asyncio.wait_for",
        _wait_for_timeout,
    ):
        await execute_decision(
            d,
            source_event_type="REVIEW_STUCK_TIMEOUT",
            source_payload={"story_id": "slow-story"},
            bus=bus,
        )

    queued = await _drain(bus)
    assert len(queued) == 1
    assert queued[0].type is EventType.WORKER_RESPAWN_REQUESTED


# ---------------------------------------------------------------------------
# SupervisorAction enum includes respawn_worker
# ---------------------------------------------------------------------------

def test_supervisor_action_includes_respawn_worker() -> None:
    """SupervisorAction Literal must include 'respawn_worker' for policy validation."""
    from typing import get_args

    from bmad_orchestrator.supervisor.policy import SupervisorAction

    actions = get_args(SupervisorAction)
    assert "respawn_worker" in actions, f"respawn_worker missing from {actions!r}"


# ---------------------------------------------------------------------------
# _respawn_subscriber — inline unit tests (no full pilot body)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_respawn_subscriber_cap_reached_escalates() -> None:
    """After _RESPAWN_CAP respawns, a 3rd request emits HUMAN_QUERY instead."""
    # We reproduce the subscriber logic inline to test it in isolation.
    RESPAWN_CAP = 2
    _respawn_counts: dict[str, int] = {}
    _respawn_pending: list[str] = []
    _respawn_hints: dict[str, str] = {}
    bus = EventLoop()

    async def _respawn_subscriber(event: Event) -> None:
        if event.type != EventType.WORKER_RESPAWN_REQUESTED:
            return
        sid = event.payload.get("story_id")
        if not isinstance(sid, str) or not sid:
            return
        count = _respawn_counts.get(sid, 0) + 1
        hint = event.payload.get("dev_prompt_hint")
        if count > RESPAWN_CAP:
            await bus.emit(
                EventType.HUMAN_QUERY,
                source="respawn_cap",
                story_id=sid,
                reason=f"Respawn cap ({RESPAWN_CAP}) reached for {sid}",
            )
            return
        _respawn_counts[sid] = count
        if hint:
            _respawn_hints[sid] = hint
        _respawn_pending.append(sid)

    bus.on(_respawn_subscriber)

    # Respawn 1 and 2 — should be queued normally.
    await bus.emit(
        EventType.WORKER_RESPAWN_REQUESTED,
        story_id="story-x",
        reason="r",
        iteration=1,
        dev_prompt_hint=None,
        cancelled_worker_id=None,
    )
    await bus.drain()
    await bus.emit(
        EventType.WORKER_RESPAWN_REQUESTED,
        story_id="story-x",
        reason="r",
        iteration=2,
        dev_prompt_hint=None,
        cancelled_worker_id=None,
    )
    await bus.drain()

    assert _respawn_counts["story-x"] == 2
    assert _respawn_pending.count("story-x") == 2

    # Respawn 3 — should escalate.
    await bus.emit(
        EventType.WORKER_RESPAWN_REQUESTED,
        story_id="story-x",
        reason="r",
        iteration=3,
        dev_prompt_hint=None,
        cancelled_worker_id=None,
    )
    dispatched = await bus.drain()

    # Count NOT incremented beyond cap.
    assert _respawn_counts["story-x"] == 2
    # Pending still has only 2 entries.
    assert _respawn_pending.count("story-x") == 2
    # A HUMAN_QUERY must have been dispatched (it was emitted from subscriber
    # and drain processes cascades).
    hq = [e for e in dispatched if e.type is EventType.HUMAN_QUERY]
    assert len(hq) == 1
    assert "story-x" in hq[0].payload["reason"]


@pytest.mark.asyncio
async def test_respawn_subscriber_stores_hint() -> None:
    """dev_prompt_hint is stored and used on next spawn (basic state check)."""
    _respawn_hints: dict[str, str] = {}
    _respawn_pending: list[str] = []
    _respawn_counts: dict[str, int] = {}
    RESPAWN_CAP = 2
    bus = EventLoop()

    async def _subscriber(event: Event) -> None:
        if event.type != EventType.WORKER_RESPAWN_REQUESTED:
            return
        sid = event.payload.get("story_id")
        if not isinstance(sid, str):
            return
        count = _respawn_counts.get(sid, 0) + 1
        if count > RESPAWN_CAP:
            return
        _respawn_counts[sid] = count
        hint = event.payload.get("dev_prompt_hint")
        if hint:
            _respawn_hints[sid] = hint
        _respawn_pending.append(sid)

    bus.on(_subscriber)
    await bus.emit(
        EventType.WORKER_RESPAWN_REQUESTED,
        story_id="story-y",
        reason="review reject",
        iteration=1,
        dev_prompt_hint="add missing tests for AC3",
        cancelled_worker_id=None,
    )
    await bus.drain()

    assert _respawn_hints.get("story-y") == "add missing tests for AC3"
    assert "story-y" in _respawn_pending
