"""Tests for self_learning.metrics — M2."""

from __future__ import annotations

from bmad_orchestrator.self_learning.metrics import (
    WaveMetrics,
    compare_windows,
    slice_windows,
)


def _wave(
    wave_id: str,
    escalation_rate: float = 0.1,
    pass_rate: float = 0.9,
    cost_per_story_usd: float = 10.0,
) -> WaveMetrics:
    return WaveMetrics(
        wave_id=wave_id,
        escalation_rate=escalation_rate,
        pass_rate=pass_rate,
        cost_per_story_usd=cost_per_story_usd,
    )


def test_no_regression_stable_metrics() -> None:
    baseline = [_wave("1a"), _wave("1b")]
    comparison = [_wave("2a"), _wave("2b")]
    result = compare_windows(baseline, comparison, threshold_pct=5.0)
    assert result.has_regression is False
    assert result.degraded_metrics == []


def test_regression_detected_escalation_spike() -> None:
    baseline = [_wave("1a", escalation_rate=0.1), _wave("1b", escalation_rate=0.1)]
    comparison = [_wave("2a", escalation_rate=0.3), _wave("2b", escalation_rate=0.3)]
    result = compare_windows(baseline, comparison, threshold_pct=5.0)
    assert result.has_regression is True
    assert "escalation_rate" in result.degraded_metrics


def test_regression_detected_pass_rate_drop() -> None:
    baseline = [_wave("1a", pass_rate=0.95), _wave("1b", pass_rate=0.95)]
    comparison = [_wave("2a", pass_rate=0.7), _wave("2b", pass_rate=0.7)]
    result = compare_windows(baseline, comparison, threshold_pct=5.0)
    assert result.has_regression is True
    assert "pass_rate" in result.degraded_metrics


def test_regression_detected_cost_increase() -> None:
    baseline = [_wave("1a", cost_per_story_usd=10.0), _wave("1b", cost_per_story_usd=10.0)]
    comparison = [_wave("2a", cost_per_story_usd=20.0), _wave("2b", cost_per_story_usd=20.0)]
    result = compare_windows(baseline, comparison, threshold_pct=5.0)
    assert result.has_regression is True
    assert "cost_per_story_usd" in result.degraded_metrics


def test_empty_baseline_no_regression() -> None:
    result = compare_windows([], [_wave("1a")], threshold_pct=5.0)
    assert result.has_regression is False


def test_empty_comparison_no_regression() -> None:
    result = compare_windows([_wave("1a")], [], threshold_pct=5.0)
    assert result.has_regression is False


def test_slice_windows_enough_data() -> None:
    waves = [_wave(f"{i}") for i in range(4)]
    base, comp = slice_windows(waves, window_size=2)
    assert len(base) == 2
    assert len(comp) == 2
    assert comp[0].wave_id == "2"
    assert comp[1].wave_id == "3"


def test_slice_windows_insufficient_data() -> None:
    waves = [_wave("1a"), _wave("1b")]
    base, comp = slice_windows(waves, window_size=2)
    assert len(comp) == 0
    assert len(base) == 2


def test_max_degradation_pct_populated() -> None:
    baseline = [_wave("1a", pass_rate=1.0)]
    comparison = [_wave("2a", pass_rate=0.5)]
    result = compare_windows(baseline, comparison, threshold_pct=5.0)
    assert result.max_degradation_pct > 0.0


def test_regression_result_windows_recorded() -> None:
    baseline = [_wave("1a"), _wave("1b")]
    comparison = [_wave("2a")]
    result = compare_windows(baseline, comparison, threshold_pct=5.0)
    assert "1a" in result.baseline_window
    assert "2a" in result.comparison_window
