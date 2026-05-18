"""Unit tests for bmad_orchestrator.eval.metrics — pure aggregation."""
from __future__ import annotations

import pytest

from bmad_orchestrator.eval.metrics import (
    AggregateMetrics,
    CaseResult,
    _median,
    aggregate_results,
    case_passed,
    percentile,
)

# ── percentile ─────────────────────────────────────────────────────────────


def test_percentile_empty_returns_zero() -> None:
    assert percentile([], 95) == 0.0


def test_percentile_single_value_returns_it() -> None:
    assert percentile([0.42], 95) == 0.42


def test_percentile_p100_returns_max() -> None:
    assert percentile([1.0, 2.0, 3.0], 100) == 3.0


def test_percentile_p0_returns_min() -> None:
    assert percentile([1.0, 2.0, 3.0], 0) == 1.0


def test_percentile_p50_returns_median_interp() -> None:
    # 3 values → p50 lands exactly on middle value
    assert percentile([1.0, 5.0, 9.0], 50) == 5.0


def test_percentile_p95_linear_interp() -> None:
    # 5 values, sorted: 1, 2, 3, 4, 5; p95 → 4.8 (linear interp pos=3.8)
    result = percentile([1.0, 2.0, 3.0, 4.0, 5.0], 95)
    assert abs(result - 4.8) < 1e-9


# ── _median ────────────────────────────────────────────────────────────────


def test_median_empty_returns_zero() -> None:
    assert _median([]) == 0.0


def test_median_odd_count() -> None:
    assert _median([3.0, 1.0, 2.0]) == 2.0


def test_median_even_count_averages_middle_pair() -> None:
    assert _median([1.0, 2.0, 3.0, 4.0]) == 2.5


# ── case_passed ────────────────────────────────────────────────────────────


def test_case_passed_happy_path() -> None:
    passed, reasons = case_passed(
        final_verdict="approve",
        review_iteration=1,
        cost_usd=0.05,
        emitted_event_types=["worker_spawned", "worker_completed", "code_review_verdict"],
        expected={
            "final_verdict": "approve",
            "max_iterations": 2,
            "max_cost_usd": 0.10,
            "must_emit": ["worker_completed", "code_review_verdict"],
        },
    )
    assert passed is True
    assert reasons == []


def test_case_passed_verdict_mismatch_trips() -> None:
    passed, reasons = case_passed(
        final_verdict="reject",
        review_iteration=1,
        cost_usd=0.0,
        emitted_event_types=["worker_completed"],
        expected={"final_verdict": "approve"},
    )
    assert passed is False
    assert any("verdict mismatch" in r for r in reasons)


def test_case_passed_any_verdict_accepts_anything() -> None:
    passed, _ = case_passed(
        final_verdict="reject",
        review_iteration=1,
        cost_usd=0.0,
        emitted_event_types=[],
        expected={"final_verdict": "any"},
    )
    assert passed is True


def test_case_passed_iteration_cap_exceeded_trips() -> None:
    passed, reasons = case_passed(
        final_verdict="approve",
        review_iteration=4,
        cost_usd=0.0,
        emitted_event_types=[],
        expected={"final_verdict": "approve", "max_iterations": 2},
    )
    assert passed is False
    assert any("iteration cap" in r for r in reasons)


def test_case_passed_cost_over_budget_trips() -> None:
    passed, reasons = case_passed(
        final_verdict="approve",
        review_iteration=1,
        cost_usd=0.5,
        emitted_event_types=[],
        expected={"final_verdict": "approve", "max_cost_usd": 0.10},
    )
    assert passed is False
    assert any("cost over budget" in r for r in reasons)


def test_case_passed_missing_required_events_trips() -> None:
    passed, reasons = case_passed(
        final_verdict="approve",
        review_iteration=1,
        cost_usd=0.0,
        emitted_event_types=["worker_completed"],
        expected={
            "final_verdict": "approve",
            "must_emit": ["worker_completed", "code_review_verdict"],
        },
    )
    assert passed is False
    assert any("missing required events" in r for r in reasons)


def test_case_passed_multiple_failures_accumulate() -> None:
    passed, reasons = case_passed(
        final_verdict="reject",
        review_iteration=5,
        cost_usd=1.0,
        emitted_event_types=[],
        expected={
            "final_verdict": "approve",
            "max_iterations": 2,
            "max_cost_usd": 0.05,
            "must_emit": ["worker_completed"],
        },
    )
    assert passed is False
    assert len(reasons) >= 3  # verdict + iteration + cost (+ events)


# ── aggregate_results ──────────────────────────────────────────────────────


def _mk_result(
    *,
    case_id: str = "TC-X",
    level: str = "easy",
    passed: bool = True,
    final_verdict: str = "approve",
    review_iteration: int = 1,
    cost_usd: float = 0.10,
    latency_ms: int = 1000,
    emitted: tuple[str, ...] = ("worker_completed",),
) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        level=level,
        passed=passed,
        final_verdict=final_verdict,
        review_iteration=review_iteration,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        emitted_event_types=emitted,
    )


def test_aggregate_empty_returns_zero_metrics() -> None:
    agg = aggregate_results([])
    assert isinstance(agg, AggregateMetrics)
    assert agg.total == 0
    assert agg.pass_rate == 0.0
    assert agg.by_level == {}


def test_aggregate_all_pass_yields_100_pct() -> None:
    results = [_mk_result(case_id=f"TC-{i}") for i in range(5)]
    agg = aggregate_results(results)
    assert agg.total == 5
    assert agg.passed == 5
    assert agg.pass_rate == 1.0


def test_aggregate_mixed_pass_rate() -> None:
    results = [
        _mk_result(case_id="A", passed=True),
        _mk_result(case_id="B", passed=False),
        _mk_result(case_id="C", passed=True),
        _mk_result(case_id="D", passed=True),
    ]
    agg = aggregate_results(results)
    assert agg.pass_rate == pytest.approx(0.75)


def test_aggregate_escalation_rate_counts_human_query() -> None:
    results = [
        _mk_result(case_id="A", emitted=("worker_completed", "human_query")),
        _mk_result(case_id="B"),
        _mk_result(case_id="C", final_verdict="escalated"),
    ]
    agg = aggregate_results(results)
    assert agg.escalation_rate == pytest.approx(2 / 3)


def test_aggregate_review_iteration_p95() -> None:
    results = [
        _mk_result(case_id=f"TC-{i}", review_iteration=it)
        for i, it in enumerate([1, 1, 1, 2, 3])
    ]
    agg = aggregate_results(results)
    # sorted iterations: 1,1,1,2,3 → p95 pos=3.8 → 1 + 0.8*(3-2)=2.8
    assert abs(agg.review_iteration_p95 - 2.8) < 1e-9


def test_aggregate_by_level_buckets_results() -> None:
    results = [
        _mk_result(case_id="E1", level="easy", passed=True),
        _mk_result(case_id="E2", level="easy", passed=False),
        _mk_result(case_id="M1", level="medium", passed=True),
        _mk_result(case_id="H1", level="hard", passed=True),
    ]
    agg = aggregate_results(results)
    assert agg.by_level["easy"]["total"] == 2
    assert agg.by_level["easy"]["passed"] == 1
    assert agg.by_level["easy"]["pass_rate"] == 0.5
    assert agg.by_level["medium"]["pass_rate"] == 1.0
    assert agg.by_level["hard"]["pass_rate"] == 1.0
