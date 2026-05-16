"""W3 acceptance tests — worker cost tracker + adaptive story reserve.

Spec: spec/spec_wave_1a_pilot_wiring.md §W3.

Coverage:
- :class:`WorkerCostTracker` parses SDK-shape and message-wrapped usage blocks,
  accumulates :class:`TokenUsage`, computes ``cache_hit_ratio`` and exact
  Decimal ``total_cost``.
- :class:`BudgetGuard` adaptive reservation (``_recent_story_costs`` + window
  ``adaptive_story_reserve``).
- ``_tail_and_emit_completion`` end-to-end: usage events drive
  ``budget.attribute_usd``; on terminal events the final cost is recorded back
  via ``record_story_cost`` and a structured ``worker_cost_final`` log is
  emitted.

Grep validations are asserted at the bottom of this module.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from bmad_orchestrator.agent.run import _tail_and_emit_completion
from bmad_orchestrator.agent.safety.budget_guard import BudgetGuard
from bmad_orchestrator.config import BudgetConfig
from bmad_orchestrator.runtime.budget import TokenUsage, usd_cost
from bmad_orchestrator.runtime.cost_tracker import WorkerCostTracker
from bmad_orchestrator.runtime.event_loop import EventLoop, EventType
from bmad_orchestrator.runtime.worker_spawn import WorkerHandle

SONNET = "claude-sonnet-4-6"


def _usage(**kwargs: int) -> Mapping[str, Any]:
    """Build an Anthropic-shaped usage dict with non-default fields filled in."""
    base = {
        "input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 0,
    }
    base.update(kwargs)
    return base


def _write_jsonl(path: Path, events: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(ev) for ev in events) + "\n", encoding="utf-8"
    )


# ── WorkerCostTracker.feed parsing ───────────────────────────────────────────


def test_w3_tracker_parses_sdk_shape_usage() -> None:
    """``{"usage": {...}}`` (streaming delta) → delta cost > 0."""
    tracker = WorkerCostTracker(model=SONNET)
    delta = tracker.feed({"event_type": "claude_event", "usage": _usage(input_tokens=1000, output_tokens=500)})
    expected = usd_cost(SONNET, TokenUsage(input_tokens=1000, output_tokens=500))
    assert delta == expected
    assert delta > Decimal("0")


def test_w3_tracker_parses_message_wrapped_usage() -> None:
    """``{"message": {"usage": {...}}}`` (final assistant message)."""
    tracker = WorkerCostTracker(model=SONNET)
    event = {
        "event_type": "claude_event",
        "message": {"role": "assistant", "usage": _usage(input_tokens=2000, output_tokens=1000)},
    }
    delta = tracker.feed(event)
    assert delta == usd_cost(SONNET, TokenUsage(input_tokens=2000, output_tokens=1000))


def test_w3_tracker_missing_usage_returns_zero() -> None:
    tracker = WorkerCostTracker(model=SONNET)
    assert tracker.feed({"event_type": "stdout_line", "text": "hi"}) == Decimal("0")


def test_w3_tracker_empty_usage_returns_zero() -> None:
    tracker = WorkerCostTracker(model=SONNET)
    assert tracker.feed({"event_type": "x", "usage": {}}) == Decimal("0")


def test_w3_tracker_non_mapping_event_returns_zero() -> None:
    tracker = WorkerCostTracker(model=SONNET)
    # `feed` is typed Mapping but we still defend at runtime.
    assert tracker.feed({}) == Decimal("0")  # type: ignore[arg-type]


def test_w3_tracker_unknown_model_returns_zero() -> None:
    tracker = WorkerCostTracker(model="claude-imaginary-9-9")
    assert tracker.feed({"usage": _usage(input_tokens=1000)}) == Decimal("0")
    # Cumulative was NOT mutated when the model lookup failed.
    assert tracker.cumulative.input_tokens == 0


def test_w3_tracker_coerces_junk_values_to_zero() -> None:
    """Booleans, strings, and negative ints in the JSONL usage block → ignored."""
    tracker = WorkerCostTracker(model=SONNET)
    delta = tracker.feed(
        {
            "usage": {
                "input_tokens": True,  # bool — must be coerced to 0
                "cache_read_input_tokens": "junk",  # str — coerced to 0
                "output_tokens": -123,  # negative — coerced to 0
                "cache_creation_input_tokens": 500,
            }
        }
    )
    expected = usd_cost(SONNET, TokenUsage(cache_creation_input_tokens=500))
    assert delta == expected


# ── WorkerCostTracker cumulative accumulation ────────────────────────────────


def test_w3_tracker_cumulative_across_five_events() -> None:
    tracker = WorkerCostTracker(model=SONNET)
    for _ in range(5):
        tracker.feed({"usage": _usage(input_tokens=100, output_tokens=50)})
    assert tracker.cumulative.input_tokens == 500
    assert tracker.cumulative.output_tokens == 250


def test_w3_tracker_cumulative_handles_mixed_event_shapes() -> None:
    tracker = WorkerCostTracker(model=SONNET)
    tracker.feed({"usage": _usage(input_tokens=400, cache_read_input_tokens=600)})
    tracker.feed({"message": {"usage": _usage(output_tokens=200, cache_creation_input_tokens=100)}})
    tracker.feed({"event_type": "stdout_line", "text": "no usage here"})
    assert tracker.cumulative.input_tokens == 400
    assert tracker.cumulative.cache_read_input_tokens == 600
    assert tracker.cumulative.output_tokens == 200
    assert tracker.cumulative.cache_creation_input_tokens == 100


def test_w3_tracker_total_cost_matches_usd_cost_of_cumulative() -> None:
    tracker = WorkerCostTracker(model=SONNET)
    tracker.feed({"usage": _usage(input_tokens=1000, output_tokens=500)})
    tracker.feed({"usage": _usage(input_tokens=500, output_tokens=250)})
    assert tracker.total_cost == usd_cost(
        SONNET, TokenUsage(input_tokens=1500, output_tokens=750)
    )


def test_w3_tracker_total_cost_unknown_model_returns_zero() -> None:
    tracker = WorkerCostTracker(model="claude-not-priced")
    # Even when feed swallowed the cost, total_cost must also surface 0
    # without raising.
    assert tracker.total_cost == Decimal("0")


# ── cache_hit_ratio ──────────────────────────────────────────────────────────


def test_w3_tracker_cache_hit_ratio_empty_is_zero() -> None:
    tracker = WorkerCostTracker(model=SONNET)
    assert tracker.cache_hit_ratio == 0.0


def test_w3_tracker_cache_hit_ratio_formula() -> None:
    tracker = WorkerCostTracker(model=SONNET)
    tracker.feed({"usage": _usage(input_tokens=200, cache_read_input_tokens=800)})
    # 800 / (200 + 800) = 0.8
    assert math.isclose(tracker.cache_hit_ratio, 0.8, abs_tol=1e-6)


def test_w3_tracker_cache_hit_ratio_zero_when_no_cache_read() -> None:
    tracker = WorkerCostTracker(model=SONNET)
    tracker.feed({"usage": _usage(input_tokens=1000)})
    assert tracker.cache_hit_ratio == 0.0


# ── BudgetGuard.record_story_cost ────────────────────────────────────────────


def _guard(daily: float = 500.0, story_alarm: float = 30.0) -> BudgetGuard:
    cfg = BudgetConfig(
        story_alarm_usd=story_alarm,
        story_halt_usd=story_alarm * 2,
        batch_alarm_usd=200.0,
        batch_halt_usd=300.0,
        daily_limit_usd=daily,
    )
    return BudgetGuard(cfg)


def test_w3_record_story_cost_appends_valid_decimal() -> None:
    guard = _guard()
    guard.record_story_cost(Decimal("4.50"))
    assert list(guard._recent_story_costs) == [Decimal("4.50")]


def test_w3_record_story_cost_rejects_nan() -> None:
    guard = _guard()
    guard.record_story_cost(float("nan"))
    assert len(guard._recent_story_costs) == 0


def test_w3_record_story_cost_rejects_negative() -> None:
    guard = _guard()
    guard.record_story_cost(Decimal("-1.0"))
    assert len(guard._recent_story_costs) == 0


def test_w3_record_story_cost_rejects_zero() -> None:
    guard = _guard()
    guard.record_story_cost(Decimal("0"))
    assert len(guard._recent_story_costs) == 0


def test_w3_record_story_cost_maxlen_three_evicts_oldest() -> None:
    guard = _guard()
    for v in ("1", "2", "3", "4"):
        guard.record_story_cost(Decimal(v))
    assert list(guard._recent_story_costs) == [Decimal("2"), Decimal("3"), Decimal("4")]


# ── BudgetGuard.adaptive_story_reserve ───────────────────────────────────────


def test_w3_adaptive_reserve_empty_returns_half_cap() -> None:
    guard = _guard(story_alarm=30.0)
    assert guard.adaptive_story_reserve() == Decimal("15.0")


def test_w3_adaptive_reserve_single_cost_uses_observed_when_under_cap() -> None:
    guard = _guard(story_alarm=30.0)
    guard.record_story_cost(Decimal("4.20"))
    assert guard.adaptive_story_reserve() == Decimal("4.20")


def test_w3_adaptive_reserve_caps_at_story_alarm_usd() -> None:
    """Even if the realised cost spikes, the reserve is capped at story_alarm."""
    guard = _guard(story_alarm=30.0)
    guard.record_story_cost(Decimal("99.0"))
    assert guard.adaptive_story_reserve() == Decimal("30.0")


def test_w3_adaptive_reserve_uses_max_of_window() -> None:
    """With 3 observed costs the reserve is the worst-case (max) — never average."""
    guard = _guard(story_alarm=30.0)
    for v in ("3.0", "1.0", "5.0"):
        guard.record_story_cost(Decimal(v))
    assert guard.adaptive_story_reserve() == Decimal("5.0")


# ── _tail_and_emit_completion integration ────────────────────────────────────


@pytest.mark.asyncio
async def test_w3_tail_attributes_usd_per_usage_event(tmp_path: Path) -> None:
    """Each event with a usage payload triggers ``budget.attribute_usd``."""
    jsonl_path = tmp_path / "s1.jsonl"
    _write_jsonl(
        jsonl_path,
        [
            {"event_type": "claude_event", "usage": _usage(input_tokens=1000, output_tokens=500)},
            {"event_type": "claude_event", "message": {"usage": _usage(input_tokens=2000, output_tokens=1000)}},
            {"event_type": "worker_completed", "story_id": "s1", "exit_code": 0, "status": "success"},
        ],
    )
    handle = WorkerHandle(
        worktree=str(tmp_path),
        story_id="s1",
        branch="feature/s1",
        pid=0,
        jsonl_path=jsonl_path,
        process=None,
        mock=False,
        sandbox_kind="bwrap",
    )
    bus = EventLoop()
    guard = _guard()

    await _tail_and_emit_completion(handle, bus, budget=guard, model=SONNET)
    await bus.stop()

    expected = usd_cost(SONNET, TokenUsage(input_tokens=3000, output_tokens=1500))
    assert guard.attributed_for("worker:s1") == expected
    assert guard.attributed_total() == expected


@pytest.mark.asyncio
async def test_w3_tail_records_story_cost_on_completion(tmp_path: Path) -> None:
    """On ``worker_completed`` the final cost is pushed into the rolling window."""
    jsonl_path = tmp_path / "s1.jsonl"
    _write_jsonl(
        jsonl_path,
        [
            {"event_type": "claude_event", "usage": _usage(input_tokens=1000, output_tokens=500)},
            {"event_type": "worker_completed", "story_id": "s1", "exit_code": 0, "status": "success"},
        ],
    )
    handle = WorkerHandle(
        worktree=str(tmp_path), story_id="s1", branch="feature/s1", pid=0,
        jsonl_path=jsonl_path, process=None, mock=False, sandbox_kind="bwrap",
    )
    bus = EventLoop()
    guard = _guard()

    await _tail_and_emit_completion(handle, bus, budget=guard, model=SONNET)
    await bus.stop()

    realised = usd_cost(SONNET, TokenUsage(input_tokens=1000, output_tokens=500))
    assert list(guard._recent_story_costs) == [realised]


@pytest.mark.asyncio
async def test_w3_tail_emits_worker_cost_final_log(
    tmp_path: Path, capfd: pytest.CaptureFixture[str],
) -> None:
    """A ``worker_cost_final`` structured log entry is produced on completion."""
    jsonl_path = tmp_path / "s1.jsonl"
    _write_jsonl(
        jsonl_path,
        [
            {"event_type": "claude_event", "usage": _usage(input_tokens=400, cache_read_input_tokens=600)},
            {"event_type": "worker_completed", "story_id": "s1", "exit_code": 0, "status": "success"},
        ],
    )
    handle = WorkerHandle(
        worktree=str(tmp_path), story_id="s1", branch="feature/s1", pid=0,
        jsonl_path=jsonl_path, process=None, mock=False, sandbox_kind="bwrap",
    )
    bus = EventLoop()
    guard = _guard()

    await _tail_and_emit_completion(handle, bus, budget=guard, model=SONNET)
    await bus.stop()

    captured = capfd.readouterr()
    combined = captured.out + captured.err
    assert "worker_cost_final" in combined, (
        f"expected worker_cost_final log; got stdout={captured.out!r} stderr={captured.err!r}"
    )
    assert "story_id=s1" in combined
    assert "cache_hit_ratio=" in combined


@pytest.mark.asyncio
async def test_w3_tail_legacy_path_without_budget_still_bridges(tmp_path: Path) -> None:
    """Back-compat — no ``budget`` / ``model`` ⇒ no attribution, still emits WORKER_COMPLETED."""
    jsonl_path = tmp_path / "s1.jsonl"
    _write_jsonl(
        jsonl_path,
        [
            {"event_type": "claude_event", "usage": _usage(input_tokens=1000)},
            {"event_type": "worker_completed", "story_id": "s1", "exit_code": 0, "status": "success"},
        ],
    )
    handle = WorkerHandle(
        worktree=str(tmp_path), story_id="s1", branch="feature/s1", pid=0,
        jsonl_path=jsonl_path, process=None, mock=False, sandbox_kind="bwrap",
    )
    bus = EventLoop()
    captured: list[Any] = []

    async def _capture(ev: Any) -> None:
        captured.append(ev)

    bus.on(_capture)
    await _tail_and_emit_completion(handle, bus)
    while await bus.dispatch_one(timeout=0.01) is not None:
        pass
    await bus.stop()

    assert any(ev.type == EventType.WORKER_COMPLETED for ev in captured)


@pytest.mark.asyncio
async def test_w3_tail_records_cost_on_halt_file(tmp_path: Path) -> None:
    """``worker_halt_file`` is also a terminal event and must record cost."""
    jsonl_path = tmp_path / "s1.jsonl"
    _write_jsonl(
        jsonl_path,
        [
            {"event_type": "claude_event", "usage": _usage(input_tokens=500, output_tokens=250)},
            {"event_type": "worker_halt_file", "story_id": "s1"},
        ],
    )
    handle = WorkerHandle(
        worktree=str(tmp_path), story_id="s1", branch="feature/s1", pid=0,
        jsonl_path=jsonl_path, process=None, mock=False, sandbox_kind="bwrap",
    )
    bus = EventLoop()
    guard = _guard()

    await _tail_and_emit_completion(handle, bus, budget=guard, model=SONNET)
    await bus.stop()

    assert len(guard._recent_story_costs) == 1


# ── Synthetic 3-story stream — reservations adapt ────────────────────────────


@pytest.mark.asyncio
async def test_w3_synthetic_three_story_stream_adapts_reserve(tmp_path: Path) -> None:
    """Drive three workers sequentially; verify the adaptive reserve evolves.

    - Story 1 (empty history) reserves ``story_alarm_usd / 2`` = $15.
    - After realising e.g. $2 of cost, reserve drops to $2.
    - After a third story spikes to $25, reserve = max(observed) capped at cap.
    """
    bus = EventLoop()
    guard = _guard(story_alarm=30.0)

    # Empty history — exactly cap/2.
    assert guard.adaptive_story_reserve() == Decimal("15.0")

    # Drive worker 1 with a small spend.
    small_jsonl = tmp_path / "s1.jsonl"
    _write_jsonl(
        small_jsonl,
        [
            {"event_type": "claude_event", "usage": _usage(input_tokens=500, output_tokens=100)},
            {"event_type": "worker_completed", "story_id": "s1", "exit_code": 0, "status": "success"},
        ],
    )
    await _tail_and_emit_completion(
        WorkerHandle(
            worktree=str(tmp_path), story_id="s1", branch="feature/s1", pid=0,
            jsonl_path=small_jsonl, process=None, mock=False, sandbox_kind="bwrap",
        ),
        bus, budget=guard, model=SONNET,
    )
    small_cost = usd_cost(SONNET, TokenUsage(input_tokens=500, output_tokens=100))
    assert guard.adaptive_story_reserve() == small_cost

    # Drive worker 2 with a big spend that exceeds the cap → reserve clamps to cap.
    big_jsonl = tmp_path / "s2.jsonl"
    _write_jsonl(
        big_jsonl,
        [
            {"event_type": "claude_event", "usage": _usage(input_tokens=10_000_000, output_tokens=5_000_000)},
            {"event_type": "worker_completed", "story_id": "s2", "exit_code": 0, "status": "success"},
        ],
    )
    await _tail_and_emit_completion(
        WorkerHandle(
            worktree=str(tmp_path), story_id="s2", branch="feature/s2", pid=0,
            jsonl_path=big_jsonl, process=None, mock=False, sandbox_kind="bwrap",
        ),
        bus, budget=guard, model=SONNET,
    )
    # The big cost vastly exceeds story_alarm_usd ($30) so reserve must clamp.
    assert guard.adaptive_story_reserve() == Decimal("30.0")
    await bus.stop()


# ── Grep validations (DoD) ───────────────────────────────────────────────────


def test_w3_grep_validations() -> None:
    cost_src = Path("src/bmad_orchestrator/runtime/cost_tracker.py").read_text(encoding="utf-8")
    run_src = Path("src/bmad_orchestrator/agent/run.py").read_text(encoding="utf-8")
    guard_src = Path("src/bmad_orchestrator/agent/safety/budget_guard.py").read_text(encoding="utf-8")

    assert cost_src.count("class WorkerCostTracker") == 1
    # run.py wires the tracker — feed call + class import token.
    assert run_src.count("WorkerCostTracker") >= 1
    assert run_src.count("attribute_usd") >= 2  # W2 dispatch + W3 worker attribution
    assert guard_src.count("_recent_story_costs") >= 1
    assert guard_src.count("adaptive_story_reserve") >= 1
