"""Phase 4 hardening #5 — Two-stage merge-gate split tests.

Spec: spec_phase4_hardening §2.5.

Coverage (9 tests):

Unit:
  * spec stage runs first (emits MERGE_GATE_STAGE_COMPLETED with stage="spec")
  * quality only runs when spec verdict is approve
  * spec fail skips quality stage entirely
  * both stages emit MERGE_GATE_STAGE_COMPLETED events

Verdict merge:
  * approve + approve = approve
  * approve + request_changes = request_changes
  * fail + anything = request_changes (worst wins)

Integration:
  * e2e with mock subagent — spec pass + quality pass → approve
  * e2e with mock subagent — spec fail → request_changes (quality skipped)
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from bmad_orchestrator.agent.run import (
    _merge_verdicts,
    _run_merge_gate_quality_stage,
    _run_merge_gate_spec_stage,
    code_review_subscriber,
    configure_code_review_gate,
)
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType

# ── Helpers ──────────────────────────────────────────────────────────────────


def _jsonl_events(events: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
    )


class _FakeHandle:
    def __init__(self, jsonl_path: Path) -> None:
        self.jsonl_path = jsonl_path


def _drain_bus(bus: EventLoop) -> list[Event]:
    """Drain all queued events from bus without dispatching."""
    out: list[Event] = []
    while True:
        try:
            out.append(bus.queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return out


@pytest.fixture(autouse=True)
def reset_gate() -> Iterator[None]:
    configure_code_review_gate(target_project=None, wave=None)
    yield
    configure_code_review_gate(target_project=None, wave=None)


# ── Unit tests: verdict merge logic ──────────────────────────────────────────


def test_verdict_merge_approve_approve() -> None:
    assert _merge_verdicts("approve", "approve") == "approve"


def test_verdict_merge_approve_request_changes() -> None:
    result = _merge_verdicts("approve", "request_changes")
    assert result == "request_changes"


def test_verdict_merge_request_changes_approve() -> None:
    result = _merge_verdicts("request_changes", "approve")
    assert result == "request_changes"


# ── Unit tests: stage ordering and skip logic ─────────────────────────────────


@pytest.mark.asyncio
async def test_spec_stage_emits_merge_gate_stage_completed(
    tmp_path: Path,
) -> None:
    """Spec stage must emit MERGE_GATE_STAGE_COMPLETED with stage='spec'."""
    bus = EventLoop()

    jsonl_path = tmp_path / "spec_review.jsonl"
    _jsonl_events(
        [
            {"verdict": "approve", "summary": "all AC covered"},
            {"event_type": "worker_completed", "status": "success"},
        ],
        jsonl_path,
    )

    fake_handle = _FakeHandle(jsonl_path)

    with patch(
        "bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker",
        new_callable=AsyncMock,
        return_value=fake_handle,
    ):
        verdict, _summary, _metrics = await _run_merge_gate_spec_stage(
            worktree="/tmp/wt",
            story_id="1.1",
            wave="w1",
            bus=bus,
        )

    assert verdict == "approve"
    emitted = _drain_bus(bus)
    stage_events = [e for e in emitted if e.type == EventType.MERGE_GATE_STAGE_COMPLETED]
    assert len(stage_events) == 1
    assert stage_events[0].payload["stage"] == "spec"
    assert stage_events[0].payload["verdict"] == "approve"


@pytest.mark.asyncio
async def test_quality_stage_emits_merge_gate_stage_completed(
    tmp_path: Path,
) -> None:
    """Quality stage must emit MERGE_GATE_STAGE_COMPLETED with stage='quality'."""
    bus = EventLoop()

    jsonl_path = tmp_path / "quality_review.jsonl"
    _jsonl_events(
        [
            {"verdict": "approve", "summary": "lints clean"},
            {"event_type": "worker_completed", "status": "success"},
        ],
        jsonl_path,
    )

    fake_handle = _FakeHandle(jsonl_path)

    with patch(
        "bmad_orchestrator.agent.run._spawn_merge_gate_quality_worker",
        new_callable=AsyncMock,
        return_value=fake_handle,
    ):
        verdict, _summary, _metrics = await _run_merge_gate_quality_stage(
            worktree="/tmp/wt",
            story_id="1.1",
            wave="w1",
            bus=bus,
        )

    assert verdict == "approve"
    emitted = _drain_bus(bus)
    stage_events = [e for e in emitted if e.type == EventType.MERGE_GATE_STAGE_COMPLETED]
    assert len(stage_events) == 1
    assert stage_events[0].payload["stage"] == "quality"
    assert stage_events[0].payload["verdict"] == "approve"


@pytest.mark.asyncio
async def test_spec_fail_skips_quality_stage(tmp_path: Path) -> None:
    """When spec stage fails, quality stage is NOT invoked."""
    bus = EventLoop()
    spec_jsonl = tmp_path / "spec.jsonl"
    _jsonl_events(
        [
            {"verdict": "request_changes", "summary": "AC-3 missing"},
            {"event_type": "worker_completed", "status": "success"},
        ],
        spec_jsonl,
    )

    fake_spec_handle = _FakeHandle(spec_jsonl)
    quality_spawn_called = False

    async def fake_quality_spawn(**kwargs: Any) -> Any:
        nonlocal quality_spawn_called
        quality_spawn_called = True
        raise AssertionError("quality stage must not be called when spec fails")

    with (
        patch(
            "bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker",
            new_callable=AsyncMock,
            return_value=fake_spec_handle,
        ),
        patch(
            "bmad_orchestrator.agent.run._spawn_merge_gate_quality_worker",
            side_effect=fake_quality_spawn,
        ),
    ):
        event = Event(
            type=EventType.WORKER_COMPLETED,
            payload={"status": "success", "story_id": "1.1", "worktree": "/tmp/wt"},
        )
        await code_review_subscriber(event, bus)

    assert not quality_spawn_called


@pytest.mark.asyncio
async def test_both_stages_emit_stage_completed_events(tmp_path: Path) -> None:
    """When spec passes, both stages emit MERGE_GATE_STAGE_COMPLETED."""
    bus = EventLoop()

    spec_jsonl = tmp_path / "spec.jsonl"
    quality_jsonl = tmp_path / "quality.jsonl"
    _jsonl_events(
        [
            {"verdict": "approve", "summary": "AC ok"},
            {"event_type": "worker_completed", "status": "success"},
        ],
        spec_jsonl,
    )
    _jsonl_events(
        [
            {"verdict": "approve", "summary": "tests pass"},
            {"event_type": "worker_completed", "status": "success"},
        ],
        quality_jsonl,
    )

    with (
        patch(
            "bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker",
            new_callable=AsyncMock,
            return_value=_FakeHandle(spec_jsonl),
        ),
        patch(
            "bmad_orchestrator.agent.run._spawn_merge_gate_quality_worker",
            new_callable=AsyncMock,
            return_value=_FakeHandle(quality_jsonl),
        ),
    ):
        event = Event(
            type=EventType.WORKER_COMPLETED,
            payload={"status": "success", "story_id": "1.2", "worktree": "/tmp/wt2"},
        )
        await code_review_subscriber(event, bus)

    emitted = _drain_bus(bus)
    stage_events = [e for e in emitted if e.type == EventType.MERGE_GATE_STAGE_COMPLETED]
    stages = [e.payload["stage"] for e in stage_events]
    assert "spec" in stages
    assert "quality" in stages
    assert len(stage_events) == 2


# ── Integration tests: e2e with mock subagents ────────────────────────────────


@pytest.mark.asyncio
async def test_e2e_both_pass_final_verdict_approve(tmp_path: Path) -> None:
    """E2E: spec=approve + quality=approve → CODE_REVIEW_VERDICT(approve)."""
    bus = EventLoop()

    spec_jsonl = tmp_path / "spec.jsonl"
    quality_jsonl = tmp_path / "quality.jsonl"
    _jsonl_events(
        [
            {"verdict": "approve", "summary": "all ACs green"},
            {"event_type": "worker_completed", "status": "success"},
        ],
        spec_jsonl,
    )
    _jsonl_events(
        [
            {"verdict": "approve", "summary": "code quality ok"},
            {"event_type": "worker_completed", "status": "success"},
        ],
        quality_jsonl,
    )

    with (
        patch(
            "bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker",
            new_callable=AsyncMock,
            return_value=_FakeHandle(spec_jsonl),
        ),
        patch(
            "bmad_orchestrator.agent.run._spawn_merge_gate_quality_worker",
            new_callable=AsyncMock,
            return_value=_FakeHandle(quality_jsonl),
        ),
    ):
        event = Event(
            type=EventType.WORKER_COMPLETED,
            payload={"status": "success", "story_id": "2.1", "worktree": "/tmp/wt3"},
        )
        await code_review_subscriber(event, bus)

    emitted = _drain_bus(bus)
    verdict_events = [e for e in emitted if e.type == EventType.CODE_REVIEW_VERDICT]
    assert verdict_events, "expected CODE_REVIEW_VERDICT event"
    assert verdict_events[-1].payload["verdict"] == "approve"


@pytest.mark.asyncio
async def test_e2e_spec_fail_final_verdict_request_changes(tmp_path: Path) -> None:
    """E2E: spec=request_changes → CODE_REVIEW_VERDICT(request_changes), quality skipped."""
    bus = EventLoop()

    spec_jsonl = tmp_path / "spec.jsonl"
    _jsonl_events(
        [
            {"verdict": "request_changes", "summary": "AC-2 not implemented"},
            {"event_type": "worker_completed", "status": "success"},
        ],
        spec_jsonl,
    )

    quality_called = False

    async def _fake_quality_spawn(**kwargs: Any) -> Any:
        nonlocal quality_called
        quality_called = True
        raise AssertionError("quality stage should not be called")

    with (
        patch(
            "bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker",
            new_callable=AsyncMock,
            return_value=_FakeHandle(spec_jsonl),
        ),
        patch(
            "bmad_orchestrator.agent.run._spawn_merge_gate_quality_worker",
            side_effect=_fake_quality_spawn,
        ),
    ):
        event = Event(
            type=EventType.WORKER_COMPLETED,
            payload={"status": "success", "story_id": "2.2", "worktree": "/tmp/wt4"},
        )
        await code_review_subscriber(event, bus)

    assert not quality_called, "quality stage must not be invoked when spec fails"
    emitted = _drain_bus(bus)
    verdict_events = [e for e in emitted if e.type == EventType.CODE_REVIEW_VERDICT]
    assert verdict_events, "expected CODE_REVIEW_VERDICT event"
    assert verdict_events[-1].payload["verdict"] == "request_changes"
