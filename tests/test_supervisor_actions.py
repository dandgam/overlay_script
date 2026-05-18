"""Tests for SupervisorEngine action executor."""

from __future__ import annotations

import pytest

from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.supervisor.actions import execute_decision
from bmad_orchestrator.supervisor.policy import SupervisorDecision, ToolCall


async def _drain(bus: EventLoop) -> list[Event]:
    out: list[Event] = []
    while not bus.queue.empty():
        out.append(bus.queue.get_nowait())
    return out


@pytest.mark.asyncio
async def test_no_op_emits_nothing():
    bus = EventLoop()
    d = SupervisorDecision(action="no_op", confidence=1.0, reason="r", tier=0)
    await execute_decision(d, source_event_type="HUMAN_QUERY", source_payload={}, bus=bus)
    assert await _drain(bus) == []


@pytest.mark.asyncio
async def test_escalate_human_emits_human_query():
    bus = EventLoop()
    d = SupervisorDecision(
        action="escalate_human",
        confidence=0.4,
        reason="unclear",
        tier=1,
        escalation_text="show this to operator",
    )
    await execute_decision(
        d,
        source_event_type="HUMAN_QUERY",
        source_payload={"story_id": "1.1"},
        bus=bus,
    )
    queued = await _drain(bus)
    assert len(queued) == 1
    payload = queued[0].payload
    assert queued[0].type is EventType.HUMAN_QUERY
    assert payload["source"] == "supervisor"
    assert payload["story_id"] == "1.1"
    assert payload["tier"] == 1
    assert payload["escalation_text"] == "show this to operator"


@pytest.mark.asyncio
async def test_pause_workers_emits_with_action_marker():
    bus = EventLoop()
    d = SupervisorDecision(
        action="pause_workers",
        confidence=1.0,
        reason="budget hit",
        tier=0,
    )
    await execute_decision(
        d, source_event_type="BUDGET_THRESHOLD_HIT", source_payload={}, bus=bus
    )
    queued = await _drain(bus)
    assert len(queued) == 1
    assert queued[0].payload["action"] == "pause_workers"
    assert queued[0].payload["source"] == "supervisor"


@pytest.mark.asyncio
async def test_abort_pipeline_emits():
    bus = EventLoop()
    d = SupervisorDecision(
        action="abort_pipeline",
        confidence=1.0,
        reason="circuit breaker",
        tier=2,
    )
    await execute_decision(d, source_event_type="HUMAN_QUERY", source_payload={}, bus=bus)
    queued = await _drain(bus)
    assert len(queued) == 1
    assert queued[0].payload["action"] == "abort_pipeline"


@pytest.mark.asyncio
async def test_auto_respond_emits_tool_calls():
    bus = EventLoop()
    d = SupervisorDecision(
        action="auto_respond",
        confidence=0.95,
        reason="clear",
        tier=1,
        tool_calls=[ToolCall(name="trigger_compliance_sweep", args={"wave": "1a"})],
    )
    await execute_decision(
        d,
        source_event_type="COMPLIANCE_SWEEP_NEEDED",
        source_payload={"story_id": "x"},
        bus=bus,
    )
    queued = await _drain(bus)
    assert len(queued) == 1
    payload = queued[0].payload
    assert payload["action"] == "auto_respond"
    assert payload["tool_calls"] == [
        {"name": "trigger_compliance_sweep", "args": {"wave": "1a"}}
    ]
    assert payload["confidence"] == 0.95
