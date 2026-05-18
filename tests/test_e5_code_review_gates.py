"""E5 acceptance — 4 code-review gates + quarterly compliance sweep.

Spec: spec/spec_embed_phase45_with_selflearning.md §E5.

Coverage (35 tests):

* :class:`ReviewMetrics` extraction (8) — metrics sub-object, top-level fields,
  type coercion, accumulation across events.
* Pure gate functions (10) — `_gate_p0_threshold`, `_gate_compliance`,
  `_gate_test_coverage` edge cases.
* `_load_review_gates` (3) — override, fallback to defaults, YAML load.
* :func:`configure_code_review_gate` gates_override wiring (2).
* :func:`code_review_subscriber` integration (8) — gates override approve,
  compliance escalates to HUMAN_QUERY directly.
* :func:`quarterly_sweep_subscriber` (4) — emit on wave boundary modulo zero.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from bmad_orchestrator.agent.run import (
    ReviewMetrics,
    _extract_metrics_from_event,
    _gate_compliance,
    _gate_p0_threshold,
    _gate_test_coverage,
    _load_review_gates,
    _merge_metrics,
    code_review_subscriber,
    configure_code_review_gate,
    quarterly_sweep_subscriber,
)
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.runtime.worker_spawn import WorkerHandle
from bmad_orchestrator.skills_repo import CodeReviewGates

# ── Helpers ──────────────────────────────────────────────────────────────────


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


def _collect_verdict_events(bus: EventLoop) -> list[Event]:
    """Collect emitted events, excluding MERGE_GATE_STAGE_COMPLETED observability events.

    Phase 4 hardening #5 adds MERGE_GATE_STAGE_COMPLETED events alongside the final
    CODE_REVIEW_VERDICT/HUMAN_QUERY. Tests that predate the split check for exactly 1
    outcome event — filter out the instrumentation events so they still pass.
    """
    return [
        e for e in _collect_emitted(bus)
        if e.type != EventType.MERGE_GATE_STAGE_COMPLETED
    ]


@pytest.fixture
def reset_gate_config() -> Iterator[None]:
    configure_code_review_gate(target_project=None, wave=None)
    yield
    configure_code_review_gate(target_project=None, wave=None)


# ────────────────────────────────────────────────────────────────────────────
# A. ReviewMetrics extraction (8 tests)
# ────────────────────────────────────────────────────────────────────────────


def test_e5_extract_metrics_returns_none_when_no_metrics_fields() -> None:
    assert _extract_metrics_from_event({"event_type": "claude_event", "text": "hi"}) is None
    assert _extract_metrics_from_event({}) is None
    assert _extract_metrics_from_event("not-a-dict") is None  # type: ignore[arg-type]


def test_e5_extract_metrics_from_metrics_subobject() -> None:
    res = _extract_metrics_from_event({
        "event_type": "claude_event",
        "metrics": {
            "p0_found": 3,
            "p0_fixed": 2,
            "compliance_tags": ["152-ФЗ"],
            "test_files_count": 4,
            "todo_placeholders": 0,
            "expected_n_tests": 8,
        },
    })
    assert res == ReviewMetrics(
        p0_found=3,
        p0_fixed=2,
        compliance_tags=("152-ФЗ",),
        test_files_count=4,
        todo_placeholders=0,
        expected_n_tests=8,
    )


def test_e5_extract_metrics_from_top_level_fields() -> None:
    res = _extract_metrics_from_event({
        "event_type": "claude_event",
        "p0_found": 1,
        "p0_fixed": 1,
        "expected_n_tests": 5,
    })
    assert res is not None
    assert res.p0_found == 1
    assert res.p0_fixed == 1
    assert res.expected_n_tests == 5
    assert res.compliance_tags == ()


def test_e5_extract_metrics_string_compliance_tag_becomes_tuple() -> None:
    res = _extract_metrics_from_event({"metrics": {"compliance_tags": "187-ФЗ"}})
    assert res is not None
    assert res.compliance_tags == ("187-ФЗ",)


def test_e5_extract_metrics_invalid_int_falls_back_to_zero() -> None:
    res = _extract_metrics_from_event({
        "metrics": {"p0_found": "not-a-number", "p0_fixed": None, "test_files_count": 3}
    })
    assert res is not None
    assert res.p0_found == 0
    assert res.p0_fixed == 0
    assert res.test_files_count == 3


def test_e5_extract_metrics_negative_int_clamped_to_zero() -> None:
    res = _extract_metrics_from_event({"metrics": {"p0_found": -5, "todo_placeholders": -1}})
    assert res is not None
    assert res.p0_found == 0
    assert res.todo_placeholders == 0


def test_e5_merge_metrics_accumulates_counts() -> None:
    a = ReviewMetrics(p0_found=2, p0_fixed=1, test_files_count=3, expected_n_tests=10)
    b = ReviewMetrics(p0_found=1, p0_fixed=1, test_files_count=2, todo_placeholders=4)
    merged = _merge_metrics(a, b)
    assert merged.p0_found == 3
    assert merged.p0_fixed == 2
    assert merged.test_files_count == 5
    assert merged.todo_placeholders == 4
    # expected_n_tests prefers latest non-zero — b has 0, so a (10) wins.
    assert merged.expected_n_tests == 10


def test_e5_merge_metrics_unions_compliance_tags() -> None:
    a = ReviewMetrics(compliance_tags=("152-ФЗ", "187-ФЗ"))
    b = ReviewMetrics(compliance_tags=("187-ФЗ", "GDPR"))
    merged = _merge_metrics(a, b)
    assert merged.compliance_tags == ("152-ФЗ", "187-ФЗ", "GDPR")
    # _merge_metrics(None, x) returns x as-is.
    only_b = _merge_metrics(None, b)
    assert only_b is b


# ────────────────────────────────────────────────────────────────────────────
# B. Pure gate functions (10 tests)
# ────────────────────────────────────────────────────────────────────────────


def test_e5_gate_p0_zero_found_returns_none() -> None:
    assert _gate_p0_threshold(ReviewMetrics(p0_found=0, p0_fixed=0), 0.8) is None


def test_e5_gate_p0_disabled_threshold_zero() -> None:
    # threshold=0 → gate disabled regardless of fixed/found.
    assert _gate_p0_threshold(ReviewMetrics(p0_found=5, p0_fixed=0), 0.0) is None


def test_e5_gate_p0_under_threshold_trips() -> None:
    reason = _gate_p0_threshold(ReviewMetrics(p0_found=10, p0_fixed=5), 0.8)
    assert reason is not None
    assert "50%" in reason and "80%" in reason
    assert "5/10" in reason


def test_e5_gate_p0_at_threshold_passes() -> None:
    # 8/10 = 0.8 exactly → passes (>= threshold).
    assert _gate_p0_threshold(ReviewMetrics(p0_found=10, p0_fixed=8), 0.8) is None


def test_e5_gate_compliance_no_policy_tags_returns_none() -> None:
    assert _gate_compliance(ReviewMetrics(compliance_tags=("152-ФЗ",)), []) is None


def test_e5_gate_compliance_no_findings_returns_none() -> None:
    assert _gate_compliance(ReviewMetrics(), ["152-ФЗ", "187-ФЗ"]) is None


def test_e5_gate_compliance_hits_trips() -> None:
    reason = _gate_compliance(
        ReviewMetrics(compliance_tags=("152-ФЗ", "internal-only")),
        ["152-ФЗ", "187-ФЗ"],
    )
    assert reason is not None
    assert "152-ФЗ" in reason
    # Non-policy tag ("internal-only") must NOT show up in the reason.
    assert "internal-only" not in reason


def test_e5_gate_test_coverage_no_expected_returns_none() -> None:
    # expected_n_tests=0 disables the coverage ratio check.
    assert _gate_test_coverage(ReviewMetrics(test_files_count=2), 0.5) is None


def test_e5_gate_test_coverage_under_threshold_trips() -> None:
    reason = _gate_test_coverage(
        ReviewMetrics(test_files_count=2, expected_n_tests=10), 0.5
    )
    assert reason is not None
    assert "20%" in reason and "50%" in reason
    assert "2/10" in reason


def test_e5_gate_test_coverage_todo_placeholders_trip() -> None:
    # Coverage OK but todo!() > 0 still trips.
    reason = _gate_test_coverage(
        ReviewMetrics(test_files_count=10, expected_n_tests=10, todo_placeholders=2),
        0.5,
    )
    assert reason is not None
    assert "todo!()" in reason
    assert "2" in reason


# ────────────────────────────────────────────────────────────────────────────
# C. _load_review_gates (3 tests)
# ────────────────────────────────────────────────────────────────────────────


def test_e5_load_gates_uses_override_when_provided(
    reset_gate_config: None, tmp_path: Path
) -> None:
    from bmad_orchestrator.agent.run import CodeReviewGateConfig

    override = CodeReviewGates(p0_threshold=0.99, test_coverage_threshold=0.9)
    cfg = CodeReviewGateConfig(target_project=tmp_path, wave="1a", gates_override=override)
    loaded = _load_review_gates(cfg)
    assert loaded.p0_threshold == 0.99
    assert loaded.test_coverage_threshold == 0.9


def test_e5_load_gates_falls_back_to_defaults_when_policy_missing(
    reset_gate_config: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bmad_orchestrator.skills_repo import PolicyNotFoundError

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise PolicyNotFoundError("no policy")

    monkeypatch.setattr("bmad_orchestrator.agent.run.load_policy", boom)
    gates = _load_review_gates(None)
    # Defaults from CodeReviewGates pydantic model.
    assert gates.p0_threshold == 0.8
    assert gates.test_coverage_threshold == 0.5
    assert gates.compliance_tags == ["152-ФЗ", "187-ФЗ"]
    assert gates.sweep_every_stories == 50


def test_e5_load_gates_uses_policy_yaml() -> None:
    """Default policy YAML at skills/policy/ loads cleanly via _load_review_gates."""
    # No override + real on-disk policy → matches the shipped YAML defaults.
    gates = _load_review_gates(None)
    assert isinstance(gates, CodeReviewGates)
    assert gates.sweep_every_stories >= 1


# ────────────────────────────────────────────────────────────────────────────
# D. configure_code_review_gate gates_override wiring (2 tests)
# ────────────────────────────────────────────────────────────────────────────


def test_e5_configure_accepts_gates_override(
    reset_gate_config: None, tmp_path: Path
) -> None:
    from bmad_orchestrator.agent import run as run_mod

    override = CodeReviewGates(p0_threshold=0.7)
    configure_code_review_gate(target_project=tmp_path, wave="1a", gates_override=override)
    cfg = run_mod._CODE_REVIEW_GATE
    assert cfg is not None
    assert cfg.gates_override is override
    assert cfg.gates_override.p0_threshold == 0.7


def test_e5_configure_default_gates_override_is_none(
    reset_gate_config: None, tmp_path: Path
) -> None:
    from bmad_orchestrator.agent import run as run_mod

    configure_code_review_gate(target_project=tmp_path, wave="1a")
    cfg = run_mod._CODE_REVIEW_GATE
    assert cfg is not None
    assert cfg.gates_override is None


# ────────────────────────────────────────────────────────────────────────────
# E. code_review_subscriber integration (8 tests)
# ────────────────────────────────────────────────────────────────────────────


def _success_event(story_id: str, worktree: Path) -> Event:
    return Event(
        type=EventType.WORKER_COMPLETED,
        payload={"story_id": story_id, "worktree": str(worktree), "status": "success"},
    )


@pytest.mark.asyncio
async def test_e5_subscriber_no_metrics_keeps_original_verdict(
    reset_gate_config: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No metrics emitted → verdict pass-through unchanged."""
    review_jsonl = tmp_path / "review.jsonl"
    _write_jsonl(
        review_jsonl,
        [
            {"event_type": "claude_event", "verdict": "approve", "summary": "LGTM"},
            {"event_type": "worker_completed", "story_id": "s1", "status": "success"},
        ],
    )

    async def fake_spawn(*, worktree: str, story_id: str, wave: str) -> WorkerHandle:
        return _make_handle(worktree, story_id, review_jsonl)

    monkeypatch.setattr("bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker", fake_spawn)
    monkeypatch.setattr("bmad_orchestrator.agent.run._spawn_merge_gate_quality_worker", fake_spawn)

    configure_code_review_gate(
        target_project=tmp_path,
        wave="1a",
        gates_override=CodeReviewGates(),  # defaults
    )
    bus = EventLoop()
    await code_review_subscriber(_success_event("s1", tmp_path / "wt"), bus)
    await bus.stop()

    emitted = _collect_verdict_events(bus)
    assert len(emitted) == 1
    assert emitted[0].type == EventType.CODE_REVIEW_VERDICT
    assert emitted[0].payload["verdict"] == "approve"
    assert "gate_reasons" not in emitted[0].payload


@pytest.mark.asyncio
async def test_e5_subscriber_compliance_gate_emits_human_query_no_verdict(
    reset_gate_config: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compliance tag in findings → HUMAN_QUERY directly, no CODE_REVIEW_VERDICT."""
    review_jsonl = tmp_path / "review.jsonl"
    _write_jsonl(
        review_jsonl,
        [
            {"event_type": "claude_event", "verdict": "approve", "summary": "LGTM"},
            {
                "event_type": "claude_event",
                "metrics": {"compliance_tags": ["152-ФЗ"]},
            },
            {"event_type": "worker_completed", "story_id": "s1", "status": "success"},
        ],
    )

    async def fake_spawn(*, worktree: str, story_id: str, wave: str) -> WorkerHandle:
        return _make_handle(worktree, story_id, review_jsonl)

    monkeypatch.setattr("bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker", fake_spawn)
    monkeypatch.setattr("bmad_orchestrator.agent.run._spawn_merge_gate_quality_worker", fake_spawn)

    configure_code_review_gate(
        target_project=tmp_path,
        wave="1a",
        escalation_chat_id=42,
        gates_override=CodeReviewGates(),
    )
    bus = EventLoop()
    await code_review_subscriber(_success_event("s1", tmp_path / "wt"), bus)
    await bus.stop()

    emitted = _collect_verdict_events(bus)
    assert len(emitted) == 1
    assert emitted[0].type == EventType.HUMAN_QUERY
    payload = emitted[0].payload
    assert payload["verdict"] == "compliance_violation"
    assert payload["chat_id"] == 42
    assert "mandatory_fix" in payload["actions"]
    assert "abandon" in payload["actions"]
    assert "approve_override" not in payload["actions"]


@pytest.mark.asyncio
async def test_e5_subscriber_p0_gate_overrides_approve_to_reject(
    reset_gate_config: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review_jsonl = tmp_path / "review.jsonl"
    _write_jsonl(
        review_jsonl,
        [
            {"event_type": "claude_event", "verdict": "approve", "summary": "looked ok"},
            {
                "event_type": "claude_event",
                "metrics": {"p0_found": 5, "p0_fixed": 1},
            },
            {"event_type": "worker_completed", "story_id": "s1", "status": "success"},
        ],
    )

    async def fake_spawn(*, worktree: str, story_id: str, wave: str) -> WorkerHandle:
        return _make_handle(worktree, story_id, review_jsonl)

    monkeypatch.setattr("bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker", fake_spawn)
    monkeypatch.setattr("bmad_orchestrator.agent.run._spawn_merge_gate_quality_worker", fake_spawn)

    configure_code_review_gate(
        target_project=tmp_path,
        wave="1a",
        gates_override=CodeReviewGates(p0_threshold=0.8),
    )
    bus = EventLoop()
    await code_review_subscriber(_success_event("s1", tmp_path / "wt"), bus)
    await bus.stop()

    emitted = _collect_verdict_events(bus)
    assert len(emitted) == 1
    assert emitted[0].type == EventType.CODE_REVIEW_VERDICT
    assert emitted[0].payload["verdict"] == "reject"
    assert any("P0" in r for r in emitted[0].payload["gate_reasons"])


@pytest.mark.asyncio
async def test_e5_subscriber_test_coverage_gate_overrides_approve_to_reject(
    reset_gate_config: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review_jsonl = tmp_path / "review.jsonl"
    _write_jsonl(
        review_jsonl,
        [
            {"event_type": "claude_event", "verdict": "approve", "summary": "ok"},
            {
                "event_type": "claude_event",
                "metrics": {
                    "test_files_count": 2,
                    "expected_n_tests": 10,
                    "todo_placeholders": 0,
                },
            },
            {"event_type": "worker_completed", "story_id": "s1", "status": "success"},
        ],
    )

    async def fake_spawn(*, worktree: str, story_id: str, wave: str) -> WorkerHandle:
        return _make_handle(worktree, story_id, review_jsonl)

    monkeypatch.setattr("bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker", fake_spawn)
    monkeypatch.setattr("bmad_orchestrator.agent.run._spawn_merge_gate_quality_worker", fake_spawn)

    configure_code_review_gate(
        target_project=tmp_path,
        wave="1a",
        gates_override=CodeReviewGates(test_coverage_threshold=0.5),
    )
    bus = EventLoop()
    await code_review_subscriber(_success_event("s1", tmp_path / "wt"), bus)
    await bus.stop()

    emitted = _collect_verdict_events(bus)
    assert emitted[0].payload["verdict"] == "reject"
    assert any("coverage" in r for r in emitted[0].payload["gate_reasons"])


@pytest.mark.asyncio
async def test_e5_subscriber_gates_skip_when_verdict_not_approve(
    reset_gate_config: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """request_changes verdict + failing P0 → keeps request_changes (gates only override approve)."""
    review_jsonl = tmp_path / "review.jsonl"
    _write_jsonl(
        review_jsonl,
        [
            {"event_type": "claude_event", "verdict": "request_changes", "summary": "rename"},
            {
                "event_type": "claude_event",
                "metrics": {"p0_found": 10, "p0_fixed": 0},
            },
            {"event_type": "worker_completed", "story_id": "s1", "status": "success"},
        ],
    )

    async def fake_spawn(*, worktree: str, story_id: str, wave: str) -> WorkerHandle:
        return _make_handle(worktree, story_id, review_jsonl)

    monkeypatch.setattr("bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker", fake_spawn)
    monkeypatch.setattr("bmad_orchestrator.agent.run._spawn_merge_gate_quality_worker", fake_spawn)

    configure_code_review_gate(
        target_project=tmp_path,
        wave="1a",
        gates_override=CodeReviewGates(),
    )
    bus = EventLoop()
    await code_review_subscriber(_success_event("s1", tmp_path / "wt"), bus)
    await bus.stop()

    emitted = _collect_verdict_events(bus)
    assert emitted[0].payload["verdict"] == "request_changes"
    # Verdict not overridden → no gate_reasons attached.
    assert "gate_reasons" not in emitted[0].payload


@pytest.mark.asyncio
async def test_e5_subscriber_gates_combined_in_summary(
    reset_gate_config: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both P0 and test-coverage trip → both reasons appear in summary."""
    review_jsonl = tmp_path / "review.jsonl"
    _write_jsonl(
        review_jsonl,
        [
            {"event_type": "claude_event", "verdict": "approve", "summary": "shipping"},
            {
                "event_type": "claude_event",
                "metrics": {
                    "p0_found": 4,
                    "p0_fixed": 1,
                    "test_files_count": 1,
                    "expected_n_tests": 10,
                },
            },
            {"event_type": "worker_completed", "story_id": "s1", "status": "success"},
        ],
    )

    async def fake_spawn(*, worktree: str, story_id: str, wave: str) -> WorkerHandle:
        return _make_handle(worktree, story_id, review_jsonl)

    monkeypatch.setattr("bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker", fake_spawn)
    monkeypatch.setattr("bmad_orchestrator.agent.run._spawn_merge_gate_quality_worker", fake_spawn)

    configure_code_review_gate(
        target_project=tmp_path,
        wave="1a",
        gates_override=CodeReviewGates(p0_threshold=0.8, test_coverage_threshold=0.5),
    )
    bus = EventLoop()
    await code_review_subscriber(_success_event("s1", tmp_path / "wt"), bus)
    await bus.stop()

    emitted = _collect_verdict_events(bus)
    payload = emitted[0].payload
    assert payload["verdict"] == "reject"
    assert len(payload["gate_reasons"]) == 2
    assert "P0" in payload["summary"]
    assert "coverage" in payload["summary"]
    # Original summary preserved in parens.
    assert "shipping" in payload["summary"]


@pytest.mark.asyncio
async def test_e5_subscriber_gate_reasons_in_payload(
    reset_gate_config: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gate_reasons list present whenever any gate trips and verdict was approve."""
    review_jsonl = tmp_path / "review.jsonl"
    _write_jsonl(
        review_jsonl,
        [
            {"event_type": "claude_event", "verdict": "approve", "summary": "ok"},
            {"metrics": {"p0_found": 2, "p0_fixed": 0}},
            {"event_type": "worker_completed", "story_id": "s1", "status": "success"},
        ],
    )

    async def fake_spawn(*, worktree: str, story_id: str, wave: str) -> WorkerHandle:
        return _make_handle(worktree, story_id, review_jsonl)

    monkeypatch.setattr("bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker", fake_spawn)
    monkeypatch.setattr("bmad_orchestrator.agent.run._spawn_merge_gate_quality_worker", fake_spawn)

    configure_code_review_gate(
        target_project=tmp_path,
        wave="1a",
        gates_override=CodeReviewGates(p0_threshold=0.8),
    )
    bus = EventLoop()
    await code_review_subscriber(_success_event("s1", tmp_path / "wt"), bus)
    await bus.stop()

    emitted = _collect_verdict_events(bus)
    assert isinstance(emitted[0].payload["gate_reasons"], list)
    assert len(emitted[0].payload["gate_reasons"]) == 1


@pytest.mark.asyncio
async def test_e5_subscriber_compliance_actions_list_mandatory_fix(
    reset_gate_config: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compliance violation HUMAN_QUERY MUST NOT offer approve_override."""
    review_jsonl = tmp_path / "review.jsonl"
    _write_jsonl(
        review_jsonl,
        [
            {"event_type": "claude_event", "verdict": "approve"},
            {"event_type": "claude_event", "metrics": {"compliance_tags": ["187-ФЗ"]}},
            {"event_type": "worker_completed", "story_id": "s1", "status": "success"},
        ],
    )

    async def fake_spawn(*, worktree: str, story_id: str, wave: str) -> WorkerHandle:
        return _make_handle(worktree, story_id, review_jsonl)

    monkeypatch.setattr("bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker", fake_spawn)
    monkeypatch.setattr("bmad_orchestrator.agent.run._spawn_merge_gate_quality_worker", fake_spawn)

    configure_code_review_gate(
        target_project=tmp_path,
        wave="1a",
        gates_override=CodeReviewGates(compliance_tags=["187-ФЗ"]),
    )
    bus = EventLoop()
    await code_review_subscriber(_success_event("s1", tmp_path / "wt"), bus)
    await bus.stop()

    emitted = _collect_verdict_events(bus)
    actions = emitted[0].payload["actions"]
    assert "approve_override" not in actions  # defer запрещён
    assert "mandatory_fix" in actions
    assert emitted[0].payload["compliance_tags"] == ["187-ФЗ"]


# ────────────────────────────────────────────────────────────────────────────
# F. quarterly_sweep_subscriber (4 tests)
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_e5_quarterly_sweep_emits_at_modulo_zero(
    reset_gate_config: None, tmp_path: Path
) -> None:
    configure_code_review_gate(
        target_project=tmp_path,
        wave="1a",
        gates_override=CodeReviewGates(sweep_every_stories=50),
    )
    bus = EventLoop()
    ev = Event(
        type=EventType.WAVE_BOUNDARY_REACHED,
        payload={"wave": "1a", "completed_stories": 50},
    )
    await quarterly_sweep_subscriber(ev, bus)
    await bus.stop()

    emitted = _collect_verdict_events(bus)
    assert len(emitted) == 1
    assert emitted[0].type == EventType.COMPLIANCE_SWEEP_NEEDED
    assert emitted[0].payload["wave"] == "1a"
    assert emitted[0].payload["completed_stories"] == 50
    assert emitted[0].payload["threshold"] == 50


@pytest.mark.asyncio
async def test_e5_quarterly_sweep_skips_non_modulo(
    reset_gate_config: None, tmp_path: Path
) -> None:
    configure_code_review_gate(
        target_project=tmp_path,
        wave="1a",
        gates_override=CodeReviewGates(sweep_every_stories=50),
    )
    bus = EventLoop()
    ev = Event(
        type=EventType.WAVE_BOUNDARY_REACHED,
        payload={"wave": "1a", "completed_stories": 47},
    )
    await quarterly_sweep_subscriber(ev, bus)
    await bus.stop()
    assert _collect_emitted(bus) == []


@pytest.mark.asyncio
async def test_e5_quarterly_sweep_skips_zero_stories(
    reset_gate_config: None, tmp_path: Path
) -> None:
    """0 % 50 == 0 mathematically but emitting at zero is meaningless → skip."""
    configure_code_review_gate(
        target_project=tmp_path,
        wave="1a",
        gates_override=CodeReviewGates(sweep_every_stories=50),
    )
    bus = EventLoop()
    ev = Event(
        type=EventType.WAVE_BOUNDARY_REACHED,
        payload={"wave": "1a", "completed_stories": 0},
    )
    await quarterly_sweep_subscriber(ev, bus)
    await bus.stop()
    assert _collect_emitted(bus) == []


@pytest.mark.asyncio
async def test_e5_quarterly_sweep_skips_non_wave_boundary(
    reset_gate_config: None, tmp_path: Path
) -> None:
    """Subscriber must ignore unrelated event types (e.g., WORKER_COMPLETED)."""
    bus = EventLoop()
    ev = Event(
        type=EventType.WORKER_COMPLETED,
        payload={"completed_stories": 50, "story_id": "s1"},
    )
    await quarterly_sweep_subscriber(ev, bus)
    await bus.stop()
    assert _collect_emitted(bus) == []


