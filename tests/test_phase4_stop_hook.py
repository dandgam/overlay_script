"""Phase 4 hardening #6 — Stop-hook cost + learning consolidation tests.

Spec: spec_phase4_hardening §2.6.

Coverage (10 tests):

Unit — aggregation correctness:
  * single story with all fields → correct STORY_METRICS_AGGREGATED
  * multiple latency events → p95 computed correctly
  * story with no token/latency fields → defaults to zero
  * retry count aggregated correctly
  * edge case: cost_usd with invalid value defaults to 0.0

Unit — self-learning extract trigger:
  * extract_lessons=True triggers extraction (flag respected)
  * extract_lessons=False → NOT triggered
  * consolidator invoked when provided and flag=True

Integration:
  * full STORY_COMPLETED → STORY_METRICS_AGGREGATED flow via bus
  * make_stop_hook_subscriber factory returns working subscriber
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.runtime.stop_hook_subscriber import (
    _p95,
    make_stop_hook_subscriber,
    stop_hook_subscriber,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _drain(bus: EventLoop) -> list[Event]:
    out: list[Event] = []
    while True:
        try:
            out.append(bus.queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return out


def _story_completed_event(**kwargs: Any) -> Event:
    payload: dict[str, Any] = {"story_id": "1.1", "status": "success"}
    payload.update(kwargs)
    return Event(type=EventType.STORY_COMPLETED, payload=payload)


# ── Unit tests: aggregation correctness ──────────────────────────────────────


@pytest.mark.asyncio
async def test_single_story_all_fields_aggregated_correctly() -> None:
    """All fields from STORY_COMPLETED payload appear correctly in STORY_METRICS_AGGREGATED."""
    bus = EventLoop()
    event = _story_completed_event(
        tokens={"input": 100, "cached": 50, "output": 30},
        cost_usd=0.42,
        retry_count=2,
        turn_latencies_ms=[10.0, 20.0, 30.0, 200.0, 100.0],
    )
    await stop_hook_subscriber(event, bus)

    emitted = _drain(bus)
    agg = [e for e in emitted if e.type == EventType.STORY_METRICS_AGGREGATED]
    assert len(agg) == 1
    p = agg[0].payload
    assert p["story_id"] == "1.1"
    assert p["status"] == "success"
    assert p["total_input_tokens"] == 100
    assert p["total_cached_tokens"] == 50
    assert p["total_output_tokens"] == 30
    assert abs(p["total_cost_usd"] - 0.42) < 1e-6
    assert p["retry_count"] == 2
    # p95 of [10, 20, 30, 100, 200] sorted = [10, 20, 30, 100, 200]
    assert p["p95_turn_latency_ms"] > 100.0  # should be close to 200


@pytest.mark.asyncio
async def test_multiple_latencies_p95_computed() -> None:
    """p95 latency is computed correctly over a list of turn latencies."""
    bus = EventLoop()
    # 20 values: 1..20 ms
    latencies = list(range(1, 21))
    event = _story_completed_event(turn_latencies_ms=[float(v) for v in latencies])
    await stop_hook_subscriber(event, bus)

    emitted = _drain(bus)
    agg = [e for e in emitted if e.type == EventType.STORY_METRICS_AGGREGATED]
    assert len(agg) == 1
    # p95 of 1..20 should be 19.05 (interpolated)
    assert agg[0].payload["p95_turn_latency_ms"] >= 19.0


@pytest.mark.asyncio
async def test_no_optional_fields_defaults_to_zero() -> None:
    """Story with no token/latency fields → metrics default to zero."""
    bus = EventLoop()
    event = Event(
        type=EventType.STORY_COMPLETED,
        payload={"story_id": "2.1", "status": "failed"},
    )
    await stop_hook_subscriber(event, bus)

    emitted = _drain(bus)
    agg = [e for e in emitted if e.type == EventType.STORY_METRICS_AGGREGATED]
    assert len(agg) == 1
    p = agg[0].payload
    assert p["total_input_tokens"] == 0
    assert p["total_cached_tokens"] == 0
    assert p["total_output_tokens"] == 0
    assert p["total_cost_usd"] == 0.0
    assert p["retry_count"] == 0
    assert p["p95_turn_latency_ms"] == 0.0


@pytest.mark.asyncio
async def test_retry_count_aggregated() -> None:
    """retry_count field is passed through correctly."""
    bus = EventLoop()
    event = _story_completed_event(retry_count=5)
    await stop_hook_subscriber(event, bus)

    emitted = _drain(bus)
    agg = [e for e in emitted if e.type == EventType.STORY_METRICS_AGGREGATED]
    assert agg[0].payload["retry_count"] == 5


@pytest.mark.asyncio
async def test_invalid_cost_usd_defaults_to_zero() -> None:
    """Invalid cost_usd (string / None) defaults to 0.0 without raising."""
    bus = EventLoop()
    event = _story_completed_event(cost_usd="not-a-number")
    await stop_hook_subscriber(event, bus)

    emitted = _drain(bus)
    agg = [e for e in emitted if e.type == EventType.STORY_METRICS_AGGREGATED]
    assert agg[0].payload["total_cost_usd"] == 0.0


# ── Unit tests: self-learning extract trigger ─────────────────────────────────


@pytest.mark.asyncio
async def test_extract_lessons_flag_true_marks_extracted() -> None:
    """extract_lessons=True → lessons_extracted=True in aggregated payload."""
    bus = EventLoop()
    event = _story_completed_event(extract_lessons=True)
    await stop_hook_subscriber(event, bus)

    emitted = _drain(bus)
    agg = [e for e in emitted if e.type == EventType.STORY_METRICS_AGGREGATED]
    assert agg[0].payload["lessons_extracted"] is True


@pytest.mark.asyncio
async def test_extract_lessons_flag_false_not_triggered() -> None:
    """extract_lessons=False (default) → lessons_extracted=False."""
    bus = EventLoop()
    event = _story_completed_event(extract_lessons=False)
    await stop_hook_subscriber(event, bus)

    emitted = _drain(bus)
    agg = [e for e in emitted if e.type == EventType.STORY_METRICS_AGGREGATED]
    assert agg[0].payload["lessons_extracted"] is False


@pytest.mark.asyncio
async def test_consolidator_invoked_when_flag_and_consolidator_provided() -> None:
    """make_stop_hook_subscriber: consolidator.run() called when extract_lessons=True."""
    mock_consolidator = MagicMock()
    mock_consolidator.run = AsyncMock(return_value=None)

    subscriber = make_stop_hook_subscriber(consolidator=mock_consolidator)
    bus = EventLoop()
    event = _story_completed_event(extract_lessons=True)
    await subscriber(event, bus)

    mock_consolidator.run.assert_called_once()
    emitted = _drain(bus)
    agg = [e for e in emitted if e.type == EventType.STORY_METRICS_AGGREGATED]
    assert agg[0].payload["lessons_extracted"] is True


# ── Integration tests ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_flow_story_completed_to_metrics_aggregated() -> None:
    """Integration: STORY_COMPLETED event emits exactly 1 STORY_METRICS_AGGREGATED."""
    bus = EventLoop()

    # Wire the subscriber.


    async def wrapped(event: Event) -> None:
        await stop_hook_subscriber(event, bus)

    bus.on(wrapped)

    # Emit STORY_COMPLETED and dispatch.
    await bus.emit(
        EventType.STORY_COMPLETED,
        story_id="3.1",
        status="success",
        cost_usd=1.23,
        retry_count=1,
        turn_latencies_ms=[50.0, 60.0],
    )
    await bus.dispatch_one(timeout=1.0)

    emitted = _drain(bus)
    agg = [e for e in emitted if e.type == EventType.STORY_METRICS_AGGREGATED]
    assert len(agg) == 1, f"expected 1 STORY_METRICS_AGGREGATED, got {len(agg)}"
    p = agg[0].payload
    assert p["story_id"] == "3.1"
    assert abs(p["total_cost_usd"] - 1.23) < 1e-6
    assert p["retry_count"] == 1


@pytest.mark.asyncio
async def test_make_stop_hook_subscriber_returns_functional_subscriber() -> None:
    """make_stop_hook_subscriber() returns callable that processes STORY_COMPLETED."""
    subscriber = make_stop_hook_subscriber()
    bus = EventLoop()
    event = _story_completed_event(cost_usd=0.55, status="success")
    await subscriber(event, bus)

    emitted = _drain(bus)
    agg = [e for e in emitted if e.type == EventType.STORY_METRICS_AGGREGATED]
    assert len(agg) == 1
    assert abs(agg[0].payload["total_cost_usd"] - 0.55) < 1e-6


# ── Pure math test ────────────────────────────────────────────────────────────


def test_p95_math_correctness() -> None:
    """_p95 helper: empty=0.0, single=value, multi=interpolated."""
    assert _p95([]) == 0.0
    assert _p95([42.0]) == 42.0
    # [0, 1, 2, ..., 9] → p95 = 8.55
    vals = [float(i) for i in range(10)]
    assert abs(_p95(vals) - 8.55) < 0.01
