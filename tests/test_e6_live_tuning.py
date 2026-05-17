"""E6 acceptance — L2 live tuning of code-review gate thresholds.

Spec: spec/spec_embed_phase45_with_selflearning.md §E6.

Coverage (25 tests):

* :class:`BudgetGuard` record_review_metrics (5) — coverage ratio derivation,
  zero-found / zero-expected gates, deque rollover, iterations counter.
* :func:`evaluate_threshold` (6) — min-samples gate, median, IQR, clamping,
  within-bounds + out-of-bounds proposals, zero-current edge case.
* :func:`apply_proposals` (3) — safe-only updates, mixed batch, all-escalated.
* :func:`atomic_write_gates_yaml` (4) — round-trip, tempfile cleanup on
  raise, atomic replace, schema preservation.
* :func:`code_review_subscriber` live-tuning hook (5) — metrics flow into
  BudgetGuard, YAML persisted at threshold tuning, HUMAN_QUERY on bounds
  breach, no-op when budget unconfigured, no-op when min_samples not met.
* Bounds guard arithmetic (2) — boundary inclusivity + zero-current scaling.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterator
from pathlib import Path
from statistics import median
from typing import Any

import pytest
import yaml

from bmad_orchestrator.agent.run import (
    code_review_subscriber,
    configure_code_review_gate,
)
from bmad_orchestrator.agent.safety.budget_guard import BudgetGuard
from bmad_orchestrator.config import BudgetConfig
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.runtime.live_tuning import (
    MAX_MOVEMENT_FRACTION_DEFAULT,
    MIN_SAMPLES_FOR_TUNING,
    TuningProposal,
    apply_proposals,
    atomic_write_gates_yaml,
    evaluate_threshold,
)
from bmad_orchestrator.runtime.worker_spawn import WorkerHandle
from bmad_orchestrator.skills_repo import CodeReviewGates

# ── Fixtures ────────────────────────────────────────────────────────────────


def _budget() -> BudgetGuard:
    return BudgetGuard(BudgetConfig())


def _make_handle(worktree: str, story_id: str, jsonl_path: Path) -> WorkerHandle:
    return WorkerHandle(
        worktree=worktree,
        story_id=story_id,
        branch=f"feature/{story_id}",
        pid=0,
        jsonl_path=jsonl_path,
        process=None,
        mock=True,
        sandbox_kind="n/a-mock",
    )


def _write_jsonl(path: Path, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(ev) for ev in events) + "\n", encoding="utf-8"
    )


def _collect_emitted(bus: EventLoop) -> list[Event]:
    out: list[Event] = []
    while True:
        try:
            out.append(bus.queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return out


@pytest.fixture
def reset_gate_config() -> Iterator[None]:
    configure_code_review_gate(target_project=None, wave=None)
    yield
    configure_code_review_gate(target_project=None, wave=None)


# ────────────────────────────────────────────────────────────────────────────
# A. BudgetGuard.record_review_metrics (5 tests)
# ────────────────────────────────────────────────────────────────────────────


def test_e6_record_metrics_pushes_p0_coverage_ratio() -> None:
    bg = _budget()
    bg.record_review_metrics(
        p0_found=10,
        p0_fixed=8,
        test_files_count=0,
        expected_n_tests=0,
    )
    assert bg.recent_p0_coverages() == (0.8,)
    # test_files arm skipped (expected=0).
    assert bg.recent_test_coverages() == ()


def test_e6_record_metrics_skips_zero_p0_found() -> None:
    bg = _budget()
    bg.record_review_metrics(
        p0_found=0,
        p0_fixed=0,
        test_files_count=5,
        expected_n_tests=10,
    )
    assert bg.recent_p0_coverages() == ()
    assert bg.recent_test_coverages() == (0.5,)


def test_e6_record_metrics_clamps_overshoot_to_one() -> None:
    # p0_fixed > p0_found shouldn't happen in practice but the guard still clamps.
    bg = _budget()
    bg.record_review_metrics(
        p0_found=2,
        p0_fixed=10,
        test_files_count=20,
        expected_n_tests=4,
    )
    assert bg.recent_p0_coverages() == (1.0,)
    assert bg.recent_test_coverages() == (1.0,)


def test_e6_record_metrics_negative_inputs_clamped_to_zero() -> None:
    bg = _budget()
    bg.record_review_metrics(
        p0_found=5,
        p0_fixed=-3,
        test_files_count=-2,
        expected_n_tests=4,
    )
    assert bg.recent_p0_coverages() == (0.0,)
    assert bg.recent_test_coverages() == (0.0,)


def test_e6_record_metrics_deque_rolls_over_at_maxlen_ten() -> None:
    bg = _budget()
    # Push 12 stories; only the last 10 should survive.
    for i in range(12):
        bg.record_review_metrics(
            p0_found=10,
            p0_fixed=i,  # 0..11
            test_files_count=0,
            expected_n_tests=0,
            iterations=i + 1,
        )
    p0s = bg.recent_p0_coverages()
    assert len(p0s) == 10
    # Oldest two (i=0, i=1) dropped; i=2 came in first → 0.2.
    assert p0s[0] == 0.2
    assert p0s[-1] == 1.0  # i=11 clamped from 1.1
    iters = bg.recent_review_iterations()
    assert len(iters) == 10
    assert iters == tuple(range(3, 13))


# ────────────────────────────────────────────────────────────────────────────
# B. evaluate_threshold (6 tests)
# ────────────────────────────────────────────────────────────────────────────


def test_e6_evaluate_returns_none_below_min_samples() -> None:
    samples = tuple(0.5 for _ in range(MIN_SAMPLES_FOR_TUNING - 1))
    assert (
        evaluate_threshold(
            metric="p0_threshold",
            samples=samples,
            current_value=0.8,
        )
        is None
    )


def test_e6_evaluate_returns_proposal_at_min_samples() -> None:
    samples = (0.6, 0.7, 0.7, 0.8, 0.9)
    prop = evaluate_threshold(
        metric="p0_threshold", samples=samples, current_value=0.7
    )
    assert prop is not None
    assert prop.proposed_value == pytest.approx(median(samples))
    # Δ = |0.7 - 0.7| = 0 → within bounds.
    assert prop.within_bounds is True


def test_e6_evaluate_clamps_proposed_to_unit_interval() -> None:
    samples = (1.5, 1.5, 1.5, 1.5, 1.5)  # synthetic out-of-band data
    prop = evaluate_threshold(
        metric="test_coverage_threshold",
        samples=samples,
        current_value=0.5,
    )
    assert prop is not None
    assert prop.proposed_value == 1.0
    # P1-3: scale now anchored on current (0.5), not max(current, proposed).
    # Movement 0.5 vs 0.5 * 0.5 = 0.25 → 100 % drift, out of bounds.
    assert prop.within_bounds is False


def test_e6_evaluate_out_of_bounds_marks_not_within() -> None:
    samples = (0.05, 0.06, 0.07, 0.08, 0.09)  # median 0.07
    prop = evaluate_threshold(
        metric="p0_threshold",
        samples=samples,
        current_value=0.8,
    )
    assert prop is not None
    assert prop.proposed_value == pytest.approx(0.07)
    # Δ = 0.73, scale=0.8, 0.73 > 0.4 → out of bounds.
    assert prop.within_bounds is False


def test_e6_evaluate_iqr_zero_for_uniform_samples() -> None:
    samples = (0.5,) * 5
    prop = evaluate_threshold(
        metric="p0_threshold", samples=samples, current_value=0.5
    )
    assert prop is not None
    assert prop.iqr == 0.0


def test_e6_evaluate_custom_movement_fraction_tightens_bound() -> None:
    samples = (0.5,) * 5
    prop = evaluate_threshold(
        metric="p0_threshold",
        samples=samples,
        current_value=0.6,
        max_movement_fraction=0.1,
    )
    assert prop is not None
    # Δ = 0.1, scale = max(0.5, 0.6) = 0.6; bound = 0.06. Δ > bound → escalate.
    assert prop.within_bounds is False
    assert prop.max_movement_fraction == 0.1


# ────────────────────────────────────────────────────────────────────────────
# C. apply_proposals (3 tests)
# ────────────────────────────────────────────────────────────────────────────


def test_e6_apply_proposals_updates_only_safe_metrics() -> None:
    gates = CodeReviewGates.model_validate({})
    safe = TuningProposal(
        metric="p0_threshold",
        current_value=gates.p0_threshold,
        proposed_value=0.85,
        samples=(0.85,) * 5,
        median_value=0.85,
        iqr=0.0,
        within_bounds=True,
        max_movement_fraction=MAX_MOVEMENT_FRACTION_DEFAULT,
    )
    new_gates, escalations = apply_proposals(gates, (safe,))
    assert new_gates.p0_threshold == 0.85
    assert new_gates.test_coverage_threshold == gates.test_coverage_threshold
    assert escalations == ()


def test_e6_apply_proposals_collects_escalations_keeps_old_value() -> None:
    gates = CodeReviewGates.model_validate({"p0_threshold": 0.8})
    breach = TuningProposal(
        metric="p0_threshold",
        current_value=0.8,
        proposed_value=0.1,
        samples=(0.1,) * 5,
        median_value=0.1,
        iqr=0.0,
        within_bounds=False,
        max_movement_fraction=MAX_MOVEMENT_FRACTION_DEFAULT,
    )
    new_gates, escalations = apply_proposals(gates, (breach,))
    assert new_gates.p0_threshold == 0.8  # unchanged
    assert escalations == (breach,)


def test_e6_apply_proposals_mixed_batch_writes_safe_escalates_other() -> None:
    gates = CodeReviewGates.model_validate(
        {"p0_threshold": 0.8, "test_coverage_threshold": 0.5}
    )
    safe = TuningProposal(
        metric="p0_threshold",
        current_value=0.8,
        proposed_value=0.75,
        samples=(0.75,) * 5,
        median_value=0.75,
        iqr=0.0,
        within_bounds=True,
        max_movement_fraction=MAX_MOVEMENT_FRACTION_DEFAULT,
    )
    breach = TuningProposal(
        metric="test_coverage_threshold",
        current_value=0.5,
        proposed_value=0.05,
        samples=(0.05,) * 5,
        median_value=0.05,
        iqr=0.0,
        within_bounds=False,
        max_movement_fraction=MAX_MOVEMENT_FRACTION_DEFAULT,
    )
    new_gates, escalations = apply_proposals(gates, (safe, breach))
    assert new_gates.p0_threshold == 0.75
    assert new_gates.test_coverage_threshold == 0.5
    assert escalations == (breach,)


# ────────────────────────────────────────────────────────────────────────────
# D. atomic_write_gates_yaml (4 tests)
# ────────────────────────────────────────────────────────────────────────────


def test_e6_atomic_write_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "code-review-gates.yaml"
    gates = CodeReviewGates.model_validate(
        {
            "p0_threshold": 0.75,
            "test_coverage_threshold": 0.6,
            "compliance_tags": ["152-ФЗ", "187-ФЗ"],
            "sweep_every_stories": 25,
        }
    )
    atomic_write_gates_yaml(gates, target)
    raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert raw == {
        "p0_threshold": 0.75,
        "test_coverage_threshold": 0.6,
        "compliance_tags": ["152-ФЗ", "187-ФЗ"],
        "sweep_every_stories": 25,
    }


def test_e6_atomic_write_overwrites_existing_without_partial_state(
    tmp_path: Path,
) -> None:
    target = tmp_path / "gates.yaml"
    target.write_text("p0_threshold: 0.1\nsweep_every_stories: 1\n", encoding="utf-8")
    gates = CodeReviewGates.model_validate({"p0_threshold": 0.9})
    atomic_write_gates_yaml(gates, target)
    raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert raw["p0_threshold"] == 0.9
    # No leftover tempfiles in the parent.
    leftovers = [
        p.name for p in tmp_path.iterdir()
        if p.name != target.name and p.name.startswith(target.name)
    ]
    assert leftovers == []


def test_e6_atomic_write_missing_parent_raises(tmp_path: Path) -> None:
    target = tmp_path / "does-not-exist" / "gates.yaml"
    gates = CodeReviewGates.model_validate({})
    with pytest.raises(FileNotFoundError):
        atomic_write_gates_yaml(gates, target)


def test_e6_atomic_write_cleans_tempfile_on_replace_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "gates.yaml"
    target.write_text("p0_threshold: 0.5\n", encoding="utf-8")

    def boom(_src: str, _dst: str) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="simulated replace failure"):
        atomic_write_gates_yaml(
            CodeReviewGates.model_validate({"p0_threshold": 0.9}), target
        )
    # Pre-existing target preserved.
    raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert raw == {"p0_threshold": 0.5}
    # Temp files cleaned up (sidecar flock lockfile is expected — F3 P1-4).
    leftovers = [
        p.name
        for p in tmp_path.iterdir()
        if p.name != target.name and p.name != f".{target.name}.lock"
    ]
    assert leftovers == []


# ────────────────────────────────────────────────────────────────────────────
# E. code_review_subscriber live-tuning hook (5 tests)
# ────────────────────────────────────────────────────────────────────────────


def _approve_event(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_type": "claude_event",
        "type": "tool_use_result",
        "text": "verdict: approve",
        "metrics": metrics,
    }


_TERMINAL_EVENT: dict[str, Any] = {
    "event_type": "worker_completed",
    "story_id": "test",
    "status": "success",
}


def test_e6_subscriber_records_metrics_into_budget_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reset_gate_config: None,
) -> None:
    bg = _budget()
    gates = CodeReviewGates.model_validate(
        {"p0_threshold": 0.8, "test_coverage_threshold": 0.5}
    )
    gates_yaml = tmp_path / "code-review-gates.yaml"
    atomic_write_gates_yaml(gates, gates_yaml)

    configure_code_review_gate(
        target_project=tmp_path,
        wave="w1",
        gates_override=gates,
        budget=bg,
        gates_path=gates_yaml,
    )

    review_jsonl = tmp_path / "review.jsonl"
    _write_jsonl(
        review_jsonl,
        [
            _approve_event({"p0_found": 5, "p0_fixed": 4, "test_files_count": 4, "expected_n_tests": 8}),
            _TERMINAL_EVENT,
        ],
    )
    handle = _make_handle(str(tmp_path / "wt"), "S-1", review_jsonl)

    async def fake_spawn(**_kw: Any) -> WorkerHandle:
        return handle

    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_code_review_worker", fake_spawn
    )

    bus = EventLoop()
    event = Event(
        type=EventType.WORKER_COMPLETED,
        payload={"story_id": "S-1", "worktree": str(tmp_path / "wt"), "status": "success"},
    )
    asyncio.run(code_review_subscriber(event, bus))

    assert bg.recent_p0_coverages() == (0.8,)
    assert bg.recent_test_coverages() == (0.5,)


def test_e6_subscriber_persists_yaml_after_min_samples(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reset_gate_config: None,
) -> None:
    bg = _budget()
    # Pre-seed 4 samples at 0.7 — current threshold is 0.8 — so median 0.7 is a
    # LOOSEN (proposed < current) → silent apply is allowed.  Tightening
    # (e.g. 0.8 → 0.9) now escalates HUMAN_QUERY instead of writing (P1-7), so
    # this test was rewritten to exercise the loosening branch where YAML
    # persistence is still automatic.
    for _ in range(MIN_SAMPLES_FOR_TUNING - 1):
        bg.record_review_metrics(
            p0_found=10, p0_fixed=7, test_files_count=0, expected_n_tests=0
        )

    gates = CodeReviewGates.model_validate({"p0_threshold": 0.8})
    gates_yaml = tmp_path / "code-review-gates.yaml"
    atomic_write_gates_yaml(gates, gates_yaml)

    configure_code_review_gate(
        target_project=tmp_path,
        wave="w1",
        gates_override=gates,
        budget=bg,
        gates_path=gates_yaml,
    )

    review_jsonl = tmp_path / "review.jsonl"
    _write_jsonl(
        review_jsonl,
        [_approve_event({"p0_found": 10, "p0_fixed": 7}), _TERMINAL_EVENT],
    )
    handle = _make_handle(str(tmp_path / "wt"), "S-tune", review_jsonl)

    async def fake_spawn(**_kw: Any) -> WorkerHandle:
        return handle

    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_code_review_worker", fake_spawn
    )

    bus = EventLoop()
    event = Event(
        type=EventType.WORKER_COMPLETED,
        payload={"story_id": "S-tune", "worktree": str(tmp_path / "wt"), "status": "success"},
    )
    asyncio.run(code_review_subscriber(event, bus))

    # Loosen 0.8 → 0.7 is silent-applied; yaml reflects new median.
    persisted = yaml.safe_load(gates_yaml.read_text(encoding="utf-8"))
    assert persisted["p0_threshold"] == pytest.approx(0.7)


def test_e6_subscriber_escalates_human_query_on_bounds_breach(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reset_gate_config: None,
) -> None:
    bg = _budget()
    # Pre-seed 4 stories with low coverage so the 5th triggers a bounds breach
    # (median ≈ 0.1, current ≈ 0.8 → Δ 0.7 > 0.4 bound).
    for _ in range(MIN_SAMPLES_FOR_TUNING - 1):
        bg.record_review_metrics(
            p0_found=10, p0_fixed=1, test_files_count=0, expected_n_tests=0
        )

    gates = CodeReviewGates.model_validate({"p0_threshold": 0.8})
    gates_yaml = tmp_path / "code-review-gates.yaml"
    atomic_write_gates_yaml(gates, gates_yaml)

    configure_code_review_gate(
        target_project=tmp_path,
        wave="w1",
        gates_override=gates,
        budget=bg,
        gates_path=gates_yaml,
        escalation_chat_id=42,
    )

    review_jsonl = tmp_path / "review.jsonl"
    _write_jsonl(
        review_jsonl,
        [_approve_event({"p0_found": 10, "p0_fixed": 1}), _TERMINAL_EVENT],
    )
    handle = _make_handle(str(tmp_path / "wt"), "S-breach", review_jsonl)

    async def fake_spawn(**_kw: Any) -> WorkerHandle:
        return handle

    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_code_review_worker", fake_spawn
    )

    bus = EventLoop()
    event = Event(
        type=EventType.WORKER_COMPLETED,
        payload={"story_id": "S-breach", "worktree": str(tmp_path / "wt"), "status": "success"},
    )
    asyncio.run(code_review_subscriber(event, bus))

    emitted = _collect_emitted(bus)
    human_queries = [e for e in emitted if e.type == EventType.HUMAN_QUERY]
    assert len(human_queries) == 1
    payload = human_queries[0].payload
    assert payload["verdict"] == "live_tuning_bounds"
    assert payload["metric"] == "p0_threshold"
    assert payload["proposed_value"] == pytest.approx(0.1)
    assert payload["actions"] == ["approve_update", "keep_current"]
    # Out-of-bounds proposal → YAML stays at original 0.8.
    persisted = yaml.safe_load(gates_yaml.read_text(encoding="utf-8"))
    assert persisted["p0_threshold"] == 0.8


def test_e6_subscriber_skips_tuning_when_budget_unconfigured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reset_gate_config: None,
) -> None:
    gates = CodeReviewGates.model_validate({"p0_threshold": 0.8})
    gates_yaml = tmp_path / "code-review-gates.yaml"
    atomic_write_gates_yaml(gates, gates_yaml)

    # No budget kwarg → live tuning is a no-op.
    configure_code_review_gate(
        target_project=tmp_path,
        wave="w1",
        gates_override=gates,
        gates_path=gates_yaml,
    )

    review_jsonl = tmp_path / "review.jsonl"
    _write_jsonl(
        review_jsonl,
        [_approve_event({"p0_found": 10, "p0_fixed": 9}), _TERMINAL_EVENT],
    )
    handle = _make_handle(str(tmp_path / "wt"), "S-nobg", review_jsonl)

    async def fake_spawn(**_kw: Any) -> WorkerHandle:
        return handle

    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_code_review_worker", fake_spawn
    )

    bus = EventLoop()
    event = Event(
        type=EventType.WORKER_COMPLETED,
        payload={"story_id": "S-nobg", "worktree": str(tmp_path / "wt"), "status": "success"},
    )
    asyncio.run(code_review_subscriber(event, bus))

    # YAML should be untouched (sole content from the seed write).
    persisted = yaml.safe_load(gates_yaml.read_text(encoding="utf-8"))
    assert persisted["p0_threshold"] == 0.8


def test_e6_subscriber_holds_threshold_when_below_min_samples(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reset_gate_config: None,
) -> None:
    bg = _budget()
    gates = CodeReviewGates.model_validate({"p0_threshold": 0.8})
    gates_yaml = tmp_path / "code-review-gates.yaml"
    atomic_write_gates_yaml(gates, gates_yaml)

    configure_code_review_gate(
        target_project=tmp_path,
        wave="w1",
        gates_override=gates,
        budget=bg,
        gates_path=gates_yaml,
    )

    # Drive a single story. After this BudgetGuard has 1 sample — not enough.
    review_jsonl = tmp_path / "review.jsonl"
    _write_jsonl(
        review_jsonl,
        [_approve_event({"p0_found": 10, "p0_fixed": 2}), _TERMINAL_EVENT],
    )
    handle = _make_handle(str(tmp_path / "wt"), "S-1", review_jsonl)

    async def fake_spawn(**_kw: Any) -> WorkerHandle:
        return handle

    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_code_review_worker", fake_spawn
    )

    bus = EventLoop()
    event = Event(
        type=EventType.WORKER_COMPLETED,
        payload={"story_id": "S-1", "worktree": str(tmp_path / "wt"), "status": "success"},
    )
    asyncio.run(code_review_subscriber(event, bus))

    # YAML untouched (only the seed write).
    persisted = yaml.safe_load(gates_yaml.read_text(encoding="utf-8"))
    assert persisted["p0_threshold"] == 0.8


# ────────────────────────────────────────────────────────────────────────────
# F. Bounds guard arithmetic (2 tests)
# ────────────────────────────────────────────────────────────────────────────


def test_e6_bounds_guard_exact_50_percent_movement_is_within() -> None:
    # current=0.4, proposed=0.6 — Δ=0.2, scale=0.6, 0.2/0.6 ≈ 0.333 < 0.5 → safe.
    samples = (0.6,) * 5
    prop = evaluate_threshold(
        metric="p0_threshold", samples=samples, current_value=0.4
    )
    assert prop is not None
    assert prop.within_bounds is True


def test_e6_bounds_guard_zero_current_scales_to_proposed() -> None:
    # current=0.0, proposed=0.4 — Δ=0.4, scale=max(0,0.4)=0.4 → exactly bound 0.2.
    # 0.4 > 0.2 → out of bounds.
    samples = (0.4,) * 5
    prop = evaluate_threshold(
        metric="p0_threshold", samples=samples, current_value=0.0
    )
    assert prop is not None
    assert prop.within_bounds is False
