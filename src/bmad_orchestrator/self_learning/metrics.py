"""Wave-over-wave metrics for regression detection.

Compares metrics across N waves (measure_window_waves) to decide whether
applied proposals improved or degraded the system.

See spec/spec_self_learning_loop.md §3.1 Step 7.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class WaveMetrics:
    """Aggregated metrics for one wave."""

    wave_id: str
    escalation_rate: float = 0.0   # fraction of stories that required human escalation
    pass_rate: float = 1.0         # fraction of stories that passed code review
    cost_per_story_usd: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RegressionResult:
    """Outcome of comparing metrics windows."""

    has_regression: bool
    degraded_metrics: list[str]
    max_degradation_pct: float
    baseline_window: list[str]    # wave IDs used as baseline
    comparison_window: list[str]  # wave IDs used as comparison


def compare_windows(
    baseline: list[WaveMetrics],
    comparison: list[WaveMetrics],
    threshold_pct: float,
) -> RegressionResult:
    """Compare ``comparison`` metrics against ``baseline``.

    A metric regresses if the average degrades by more than ``threshold_pct``
    percent relative to baseline.

    Returns RegressionResult with has_regression=True if any metric exceeds
    the threshold.
    """
    if not baseline or not comparison:
        return RegressionResult(
            has_regression=False,
            degraded_metrics=[],
            max_degradation_pct=0.0,
            baseline_window=[m.wave_id for m in baseline],
            comparison_window=[m.wave_id for m in comparison],
        )

    def avg(metrics: list[WaveMetrics], attr: str) -> float:
        vals: list[float] = [float(getattr(m, attr)) for m in metrics]
        return sum(vals) / len(vals)

    degraded: list[str] = []
    max_deg = 0.0

    # escalation_rate: higher is worse
    base_esc = avg(baseline, "escalation_rate")
    comp_esc = avg(comparison, "escalation_rate")
    if base_esc > 0:
        esc_deg = ((comp_esc - base_esc) / base_esc) * 100
        if esc_deg > threshold_pct:
            degraded.append("escalation_rate")
            max_deg = max(max_deg, esc_deg)

    # pass_rate: lower is worse
    base_pass = avg(baseline, "pass_rate")
    comp_pass = avg(comparison, "pass_rate")
    if base_pass > 0:
        pass_deg = ((base_pass - comp_pass) / base_pass) * 100
        if pass_deg > threshold_pct:
            degraded.append("pass_rate")
            max_deg = max(max_deg, pass_deg)

    # cost_per_story_usd: higher is worse
    base_cost = avg(baseline, "cost_per_story_usd")
    comp_cost = avg(comparison, "cost_per_story_usd")
    if base_cost > 0:
        cost_deg = ((comp_cost - base_cost) / base_cost) * 100
        if cost_deg > threshold_pct:
            degraded.append("cost_per_story_usd")
            max_deg = max(max_deg, cost_deg)

    return RegressionResult(
        has_regression=bool(degraded),
        degraded_metrics=degraded,
        max_degradation_pct=max_deg,
        baseline_window=[m.wave_id for m in baseline],
        comparison_window=[m.wave_id for m in comparison],
    )


def slice_windows(
    all_metrics: list[WaveMetrics],
    window_size: int,
) -> tuple[list[WaveMetrics], list[WaveMetrics]]:
    """Split ``all_metrics`` into baseline and comparison windows.

    Requires at least 2*window_size entries. Returns (baseline, comparison)
    where comparison = last window_size entries and baseline = preceding ones.
    If not enough data, returns (all_metrics, []) indicating no comparison possible.
    """
    if len(all_metrics) < 2 * window_size:
        return all_metrics, []
    split = len(all_metrics) - window_size
    return all_metrics[:split][-window_size:], all_metrics[split:]


__all__ = [
    "RegressionResult",
    "WaveMetrics",
    "compare_windows",
    "slice_windows",
]
