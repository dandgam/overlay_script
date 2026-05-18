"""Pure aggregation functions for Phase 3 eval suite.

Inputs are per-case results; outputs are aggregate metrics matched against
the thresholds zafiksated in ``spec/methodology-virgil.md`` §1:

* ``pass_rate`` ≥ 0.85
* ``escalation_rate`` ≤ 0.20
* ``review_iteration_p95`` ≤ 2
* ``cost_per_story_median`` — baseline driven (no fixed cap; regression check)

Functions are deliberately pure (no I/O, no orchestrator imports) so they can
be unit-tested in isolation without spinning a real run.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CaseResult:
    """Outcome of a single eval case run.

    Populated by :mod:`bmad_orchestrator.eval.runner` from the events.jsonl
    stream produced by the orchestrator (and the code-review verdict event).
    """

    case_id: str
    level: str  # easy | medium | hard
    passed: bool
    final_verdict: str  # approve | reject | request_changes | error | escalated
    review_iteration: int
    cost_usd: float
    latency_ms: int
    emitted_event_types: tuple[str, ...]
    failure_reasons: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AggregateMetrics:
    """Aggregate over a batch of :class:`CaseResult`."""

    total: int
    passed: int
    pass_rate: float
    escalation_rate: float
    review_iteration_p95: float
    cost_per_story_median: float
    cost_p95: float
    latency_p95_ms: float
    by_level: dict[str, dict[str, Any]]


def percentile(values: list[float], p: int) -> float:
    """Linear-interpolated percentile (no numpy dep).

    Empty input → 0.0. ``p`` is in ``[0, 100]``. Implementation matches the
    behavior of ``statistics.quantiles`` but works on a single percentile and
    handles the edge ``len(values) == 1`` cleanly.
    """
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    if p <= 0:
        return float(min(values))
    if p >= 100:
        return float(max(values))
    sorted_vals = sorted(values)
    pos = (p / 100.0) * (len(sorted_vals) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return float(sorted_vals[lo])
    frac = pos - lo
    return float(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac)


def _median(values: list[float]) -> float:
    """Median — empty → 0.0."""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return float(s[mid])
    return float((s[mid - 1] + s[mid]) / 2.0)


def case_passed(
    *,
    final_verdict: str,
    review_iteration: int,
    cost_usd: float,
    emitted_event_types: list[str],
    expected: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Apply the case's ``expected:`` block to actual run outcomes.

    Returns ``(passed, failure_reasons)``. Empty reasons list ⇔ passed.

    Rules:
      * ``expected.final_verdict`` — if "any", any verdict accepted; else strict equality.
      * ``expected.max_iterations`` — ``review_iteration ≤ max``; trip otherwise.
      * ``expected.max_cost_usd`` — ``cost_usd ≤ max``; trip otherwise.
      * ``expected.must_emit`` — every listed event type must appear at least once.
      * ``expected.may_emit`` — informational; no pass/fail impact.
    """
    reasons: list[str] = []

    expected_verdict = str(expected.get("final_verdict", "any"))
    if expected_verdict != "any" and final_verdict != expected_verdict:
        reasons.append(
            f"verdict mismatch: expected {expected_verdict!r}, got {final_verdict!r}"
        )

    max_iter = expected.get("max_iterations")
    if isinstance(max_iter, int) and review_iteration > max_iter:
        reasons.append(
            f"iteration cap exceeded: {review_iteration} > {max_iter}"
        )

    max_cost = expected.get("max_cost_usd")
    if isinstance(max_cost, (int, float)) and cost_usd > float(max_cost):
        reasons.append(
            f"cost over budget: ${cost_usd:.4f} > ${float(max_cost):.4f}"
        )

    must_emit = expected.get("must_emit") or []
    emitted_set = set(emitted_event_types)
    missing = [e for e in must_emit if e not in emitted_set]
    if missing:
        reasons.append(f"missing required events: {missing}")

    return (not reasons, reasons)


def pass_at_k(results_per_case: dict[str, list[bool]], k: int) -> float:
    """Compute pass@k: fraction of cases where at least 1 of k attempts passed.

    Standard eval metric — measures whether the system *can* solve the case
    (at least once out of k tries). Optimistic: rewards any success.

    Args:
        results_per_case: dict mapping case_id → list of pass/fail booleans.
            Each list must have at least ``k`` entries; extra entries are ignored.
        k: number of attempts to consider per case.

    Returns:
        Float in [0.0, 1.0]. Empty input → 0.0.
    """
    if not results_per_case or k <= 0:
        return 0.0
    passed = 0
    for results in results_per_case.values():
        attempts = results[:k] if len(results) >= k else results
        if any(attempts):
            passed += 1
    return passed / len(results_per_case)


def pass_consistency_at_k(results_per_case: dict[str, list[bool]], k: int) -> float:
    """Compute pass^k: fraction of cases where ALL k attempts passed.

    Stricter production-consistency metric — measures whether the system
    *reliably* solves the case (every attempt of k tries). Punishes any failure.

    pass^k ≤ pass@k always (math sanity invariant).

    Args:
        results_per_case: dict mapping case_id → list of pass/fail booleans.
            Each list must have at least ``k`` entries; extra entries are ignored.
        k: number of attempts to consider per case.

    Returns:
        Float in [0.0, 1.0]. Empty input → 0.0.
    """
    if not results_per_case or k <= 0:
        return 0.0
    passed = 0
    for results in results_per_case.values():
        attempts = results[:k] if len(results) >= k else results
        if all(attempts):
            passed += 1
    return passed / len(results_per_case)


def aggregate_results(results: list[CaseResult]) -> AggregateMetrics:
    """Roll up per-case outcomes into batch metrics."""
    total = len(results)
    if total == 0:
        return AggregateMetrics(
            total=0,
            passed=0,
            pass_rate=0.0,
            escalation_rate=0.0,
            review_iteration_p95=0.0,
            cost_per_story_median=0.0,
            cost_p95=0.0,
            latency_p95_ms=0.0,
            by_level={},
        )

    passed = sum(1 for r in results if r.passed)
    escalations = sum(
        1
        for r in results
        if r.final_verdict in {"escalated", "compliance_violation"}
        or "human_query" in r.emitted_event_types
    )
    iterations = [float(r.review_iteration) for r in results]
    costs = [r.cost_usd for r in results]
    latencies = [float(r.latency_ms) for r in results]

    by_level: dict[str, dict[str, Any]] = {}
    for level in ("easy", "medium", "hard"):
        lvl_results = [r for r in results if r.level == level]
        if not lvl_results:
            continue
        lvl_passed = sum(1 for r in lvl_results if r.passed)
        by_level[level] = {
            "total": len(lvl_results),
            "passed": lvl_passed,
            "pass_rate": lvl_passed / len(lvl_results),
        }

    return AggregateMetrics(
        total=total,
        passed=passed,
        pass_rate=passed / total,
        escalation_rate=escalations / total,
        review_iteration_p95=percentile(iterations, 95),
        cost_per_story_median=_median(costs),
        cost_p95=percentile(costs, 95),
        latency_p95_ms=percentile(latencies, 95),
        by_level=by_level,
    )
