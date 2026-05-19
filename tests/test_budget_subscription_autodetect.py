"""Initiative pilot_findings_closure S6 (#6 P2) — subscription auto-disable.

Covers :mod:`runtime.budget_autodetect`:

  * 3 unit tests — env permutations + idempotency.
  * 2 integration-style tests — manual flag suppression + downstream
    budget gate skip semantics via the same helper the run loop uses.

Spec target: +5 tests.
"""

from __future__ import annotations

import pytest

from bmad_orchestrator.runtime.budget_autodetect import (
    BudgetAutoDisableState,
    evaluate_budget_disabled,
)
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType


async def _drain(bus: EventLoop) -> list[Event]:
    captured: list[Event] = []

    async def _capture(ev: Event) -> None:
        captured.append(ev)

    bus.on(_capture)
    while await bus.dispatch_one(timeout=0.01) is not None:
        pass
    return captured


@pytest.mark.asyncio
async def test_subscription_auto_disable_emits_event_once() -> None:
    """Auto-path: no API key → disabled=True + BUDGET_AUTO_DISABLED emitted."""
    bus = EventLoop()
    state = BudgetAutoDisableState()
    env: dict[str, str] = {}

    disabled = await evaluate_budget_disabled(state, bus, env=env)

    assert disabled is True
    assert state.triggered is True
    events = await _drain(bus)
    auto_disabled = [e for e in events if e.type == EventType.BUDGET_AUTO_DISABLED]
    assert len(auto_disabled) == 1
    assert auto_disabled[0].payload["reason"] == "subscription_mode"


@pytest.mark.asyncio
async def test_subscription_auto_disable_idempotent_on_repeat_calls() -> None:
    """Two evaluator calls share one state → exactly one event emission."""
    bus = EventLoop()
    state = BudgetAutoDisableState()
    env: dict[str, str] = {}

    first = await evaluate_budget_disabled(state, bus, env=env)
    second = await evaluate_budget_disabled(state, bus, env=env)

    assert first is True
    assert second is True
    events = await _drain(bus)
    auto_disabled = [e for e in events if e.type == EventType.BUDGET_AUTO_DISABLED]
    assert len(auto_disabled) == 1, "second call must not re-emit"


@pytest.mark.asyncio
async def test_api_key_present_keeps_budget_enabled() -> None:
    """API key in env + no manual override → disabled=False, no event."""
    bus = EventLoop()
    state = BudgetAutoDisableState()
    env = {"ANTHROPIC_API_KEY": "sk-real"}

    disabled = await evaluate_budget_disabled(state, bus, env=env)

    assert disabled is False
    assert state.triggered is False
    events = await _drain(bus)
    assert not any(e.type == EventType.BUDGET_AUTO_DISABLED for e in events)


@pytest.mark.asyncio
async def test_manual_disable_flag_skips_emission() -> None:
    """BMAD_DISABLE_BUDGET=1 honoured but NOT eventified (manual operator path)."""
    bus = EventLoop()
    state = BudgetAutoDisableState()
    env = {"ANTHROPIC_API_KEY": "sk-real", "BMAD_DISABLE_BUDGET": "1"}

    disabled = await evaluate_budget_disabled(state, bus, env=env)

    assert disabled is True
    assert state.triggered is False, "manual path must not flip auto-trigger"
    events = await _drain(bus)
    assert not any(e.type == EventType.BUDGET_AUTO_DISABLED for e in events)


@pytest.mark.asyncio
async def test_manual_disable_then_auto_subscription_in_same_run() -> None:
    """Manual flag set mid-run alongside missing API key → still no event.

    Guards the integration semantics: if the operator launches with both
    knobs (belt-and-braces), the auto-detect emission stays suppressed —
    the event is reserved for the genuine auto-path that the operator
    didn't anticipate.
    """
    bus = EventLoop()
    state = BudgetAutoDisableState()
    env = {"BMAD_DISABLE_BUDGET": "1"}

    disabled = await evaluate_budget_disabled(state, bus, env=env)

    assert disabled is True
    assert state.triggered is False
    events = await _drain(bus)
    assert not any(e.type == EventType.BUDGET_AUTO_DISABLED for e in events)
