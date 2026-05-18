"""Tests for elicitation_routing subscriber + load_engine."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from bmad_orchestrator.elicitation import ElicitationEngine, ElicitationPolicy
from bmad_orchestrator.runtime.elicitation_routing import (
    load_engine,
    make_elicitation_subscriber,
)
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType


def test_load_engine_with_example_yaml():
    engine = load_engine(Path("examples/elicitation-policy.example.yaml"))
    assert isinstance(engine, ElicitationEngine)
    assert len(engine.policy.rules) > 0


def test_load_engine_missing_path_falls_back_to_empty(tmp_path: Path):
    engine = load_engine(tmp_path / "nope.yaml")
    assert isinstance(engine, ElicitationEngine)
    # Empty policy still has hard-override defaults.
    assert "crypto" in {k.lower() for k in engine.policy.defaults.hard_escalate_keywords}


def test_load_engine_default_fallback_when_none():
    # No path → falls back to DEFAULT_POLICY_PATH (examples/ default exists)
    engine = load_engine(None)
    assert isinstance(engine, ElicitationEngine)


async def _drain_queue(bus: EventLoop) -> list[Event]:
    """Pop all currently-queued events without running subscribers."""
    out: list[Event] = []
    while not bus.queue.empty():
        out.append(bus.queue.get_nowait())
    return out


@pytest.mark.asyncio
async def test_subscriber_ignores_other_event_types():
    engine = ElicitationEngine(ElicitationPolicy(version=1))
    subscriber = make_elicitation_subscriber(engine)
    bus = EventLoop()

    other = Event(type=EventType.WORKER_COMPLETED, payload={})
    await subscriber(other, bus)
    queued = await _drain_queue(bus)
    assert queued == []


@pytest.mark.asyncio
async def test_subscriber_escalates_security_to_human_query(tmp_path: Path):
    engine = ElicitationEngine(ElicitationPolicy(version=1))
    subscriber = make_elicitation_subscriber(engine)
    bus = EventLoop()

    with patch("bmad_orchestrator.runtime.elicitation_routing.runs_dir", return_value=tmp_path):
        event = Event(
            type=EventType.WORKER_ELICITATION,
            payload={"question": "which crypto?", "topics": [], "story_id": "s1"},
        )
        await subscriber(event, bus)

    queued = await _drain_queue(bus)
    human_queries = [e for e in queued if e.type is EventType.HUMAN_QUERY]
    assert len(human_queries) == 1
    payload = human_queries[0].payload
    assert payload["source"] == "elicitation_router"
    assert payload["tier"] == 0
    assert payload["risk"] == "high"
    assert (tmp_path / "control.events.jsonl").is_file()


@pytest.mark.asyncio
async def test_subscriber_auto_resolve_does_not_emit_human_query(tmp_path: Path):
    from bmad_orchestrator.elicitation.policy import Rule, RuleMatch

    rule = Rule(
        id="naming",
        match=RuleMatch(topics=["naming"]),
        risk="low",
        action="auto_resolve",
        default_answer="snake_case",
        reason="house style",
    )
    engine = ElicitationEngine(ElicitationPolicy(version=1, rules=[rule]))
    subscriber = make_elicitation_subscriber(engine)
    bus = EventLoop()

    with patch("bmad_orchestrator.runtime.elicitation_routing.runs_dir", return_value=tmp_path):
        event = Event(
            type=EventType.WORKER_ELICITATION,
            payload={"question": "var name?", "topics": ["naming"], "story_id": "s2"},
        )
        await subscriber(event, bus)

    queued = await _drain_queue(bus)
    human_queries = [e for e in queued if e.type is EventType.HUMAN_QUERY]
    assert human_queries == []
    assert (tmp_path / "control.events.jsonl").is_file()
