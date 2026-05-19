"""S2 — spec_pilot_findings_closure §1 #2: verdict event emission + fallback.

Closes pilot finding from Antares smoke runs 1-7: when the merge-gate spec
worker fails to emit a parseable ``verdict`` event in its JSONL stream, the
orchestrator escalates to HUMAN_QUERY even though the bmad-auto-dev runner's
own Stage 6 review already wrote a verdict to disk. Variant B (orchestrator
falls back to reading the runner's review log) is wired into
``code_review_subscriber`` and emits ``source=runner_log_fallback`` for
observability.

Coverage (9 tests):
  Unit (parse_runner_review_log):
    1. PASS token → approve
    2. NEEDS-FIX token → request_changes
    3. BLOCKED token → reject
    4. missing reviews/ directory → None
    5. multi-log: most-recent mtime wins (autofix re-review supersedes)
    6. empty log (no token) → None

  Subscriber wiring (code_review_subscriber):
    7. spec verdict=error + PASS log → CODE_REVIEW_VERDICT(approve),
       source=runner_log_fallback, quality stage IS invoked
    8. spec verdict=error + BLOCKED log → CODE_REVIEW_VERDICT(reject),
       source=runner_log_fallback, quality stage NOT invoked

  Regression:
    9. spec verdict=approve from parseable event → fallback NOT used,
       source=merge_gate_spec (existing path unchanged)
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from bmad_orchestrator.agent.run import (
    code_review_subscriber,
    configure_code_review_gate,
)
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.runtime.verdict_fallback import parse_runner_review_log

# ── Helpers ──────────────────────────────────────────────────────────────────


def _write_review_log(worktree: Path, story_id: str, body: str, *, suffix: str = "") -> Path:
    """Write a Stage 6 review log under the runner's canonical path."""
    reviews_dir = worktree / "_bmad" / "auto-dev-state" / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    path = reviews_dir / f"{story_id}{suffix}.log"
    path.write_text(body, encoding="utf-8")
    return path


def _jsonl_events(events: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


class _FakeHandle:
    def __init__(self, jsonl_path: Path) -> None:
        self.jsonl_path = jsonl_path


def _drain_bus(bus: EventLoop) -> list[Event]:
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


# ── Unit tests: parse_runner_review_log ──────────────────────────────────────


def test_parse_runner_log_pass_maps_to_approve(tmp_path: Path) -> None:
    body = "Stage 6 — code-review\n... narrative ...\nVerdict on last line: PASS\n"
    _write_review_log(tmp_path, "1.2", body)
    result = parse_runner_review_log(tmp_path, "1.2")
    assert result is not None
    verdict, summary = result
    assert verdict == "approve"
    assert "PASS" in summary
    assert "1.2.log" in summary


def test_parse_runner_log_needs_fix_maps_to_request_changes(tmp_path: Path) -> None:
    body = "review found 3 findings.\nFinal verdict: NEEDS-FIX\n"
    _write_review_log(tmp_path, "1.3", body)
    result = parse_runner_review_log(tmp_path, "1.3")
    assert result is not None
    verdict, _summary = result
    assert verdict == "request_changes"


def test_parse_runner_log_blocked_maps_to_reject(tmp_path: Path) -> None:
    body = "Stage 6 blocked: hard error in build.\nVerdict: BLOCKED\n"
    _write_review_log(tmp_path, "2.1", body)
    result = parse_runner_review_log(tmp_path, "2.1")
    assert result is not None
    verdict, _summary = result
    assert verdict == "reject"


def test_parse_runner_log_no_reviews_dir_returns_none(tmp_path: Path) -> None:
    # tmp_path exists but has no _bmad/auto-dev-state/reviews/ subtree.
    assert parse_runner_review_log(tmp_path, "1.1") is None


def test_parse_runner_log_multi_log_most_recent_mtime_wins(tmp_path: Path) -> None:
    """Initial review NEEDS-FIX, retry re-review PASS — retry wins by mtime."""
    initial = _write_review_log(tmp_path, "1.4", "Verdict: NEEDS-FIX\n")
    # Force older mtime on the initial log.
    old = time.time() - 600
    os.utime(initial, (old, old))
    _write_review_log(tmp_path, "1.4", "Verdict: PASS\n", suffix="-retry-1")
    result = parse_runner_review_log(tmp_path, "1.4")
    assert result is not None
    verdict, summary = result
    assert verdict == "approve"
    assert "retry-1" in summary


def test_parse_runner_log_empty_file_returns_none(tmp_path: Path) -> None:
    _write_review_log(tmp_path, "1.5", "review failed — no verdict written\n")
    assert parse_runner_review_log(tmp_path, "1.5") is None


# ── Subscriber wiring tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_subscriber_fallback_approve_invokes_quality_stage(
    tmp_path: Path,
) -> None:
    """Spec worker emits no parseable verdict → fallback to runner log PASS
    → spec_verdict becomes approve → quality stage IS invoked, final
    CODE_REVIEW_VERDICT carries source=runner_log_fallback.
    """
    worktree = tmp_path / "wt"
    worktree.mkdir()
    _write_review_log(worktree, "3.1", "Verdict: PASS\n")

    spec_jsonl = tmp_path / "spec.jsonl"
    _jsonl_events(
        [
            {"event_type": "stdout_line", "text": "review running"},
            {"event_type": "worker_completed", "status": "success"},
        ],
        spec_jsonl,
    )
    quality_jsonl = tmp_path / "quality.jsonl"
    _jsonl_events(
        [
            {"verdict": "approve", "summary": "quality ok"},
            {"event_type": "worker_completed", "status": "success"},
        ],
        quality_jsonl,
    )

    quality_called = False

    async def fake_quality_spawn(**_kwargs: Any) -> Any:
        nonlocal quality_called
        quality_called = True
        return _FakeHandle(quality_jsonl)

    bus = EventLoop()
    with (
        patch(
            "bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker",
            new_callable=AsyncMock,
            return_value=_FakeHandle(spec_jsonl),
        ),
        patch(
            "bmad_orchestrator.agent.run._spawn_merge_gate_quality_worker",
            side_effect=fake_quality_spawn,
        ),
    ):
        event = Event(
            type=EventType.WORKER_COMPLETED,
            payload={"status": "success", "story_id": "3.1", "worktree": str(worktree)},
        )
        await code_review_subscriber(event, bus)

    assert quality_called, "fallback approve must let quality stage run"
    verdicts = [e for e in _drain_bus(bus) if e.type == EventType.CODE_REVIEW_VERDICT]
    assert verdicts, "expected CODE_REVIEW_VERDICT event"
    payload = verdicts[-1].payload
    assert payload["verdict"] == "approve"
    assert payload["source"] == "runner_log_fallback"


@pytest.mark.asyncio
async def test_subscriber_fallback_reject_skips_quality_stage(
    tmp_path: Path,
) -> None:
    """Spec worker emits no verdict + runner log says BLOCKED → reject via
    fallback. Quality stage MUST NOT be invoked; emitted event carries
    source=runner_log_fallback and gate_stage='spec'.
    """
    worktree = tmp_path / "wt"
    worktree.mkdir()
    _write_review_log(worktree, "3.2", "Final: BLOCKED — compile fail\n")

    spec_jsonl = tmp_path / "spec.jsonl"
    _jsonl_events(
        [{"event_type": "worker_completed", "status": "success"}],
        spec_jsonl,
    )

    async def fake_quality_spawn(**_kwargs: Any) -> Any:
        raise AssertionError("quality stage must NOT be invoked on reject fallback")

    bus = EventLoop()
    with (
        patch(
            "bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker",
            new_callable=AsyncMock,
            return_value=_FakeHandle(spec_jsonl),
        ),
        patch(
            "bmad_orchestrator.agent.run._spawn_merge_gate_quality_worker",
            side_effect=fake_quality_spawn,
        ),
    ):
        event = Event(
            type=EventType.WORKER_COMPLETED,
            payload={"status": "success", "story_id": "3.2", "worktree": str(worktree)},
        )
        await code_review_subscriber(event, bus)

    verdicts = [e for e in _drain_bus(bus) if e.type == EventType.CODE_REVIEW_VERDICT]
    assert verdicts, "expected CODE_REVIEW_VERDICT event"
    payload = verdicts[-1].payload
    assert payload["verdict"] == "reject"
    assert payload["source"] == "runner_log_fallback"
    assert payload.get("gate_stage") == "spec"


# ── Regression test: existing parseable path stays untouched ─────────────────


@pytest.mark.asyncio
async def test_subscriber_parseable_event_does_not_use_fallback(
    tmp_path: Path,
) -> None:
    """When the spec worker emits an explicit ``verdict`` event, the runner-
    log fallback must NOT fire — source stays merge_gate_spec. Even if a stale
    runner log exists with a *different* verdict, the parseable event wins.
    """
    worktree = tmp_path / "wt"
    worktree.mkdir()
    # Stale runner log says BLOCKED — must be ignored.
    _write_review_log(worktree, "4.1", "Verdict: BLOCKED\n")

    spec_jsonl = tmp_path / "spec.jsonl"
    _jsonl_events(
        [
            {"verdict": "approve", "summary": "all AC covered"},
            {"event_type": "worker_completed", "status": "success"},
        ],
        spec_jsonl,
    )
    quality_jsonl = tmp_path / "quality.jsonl"
    _jsonl_events(
        [
            {"verdict": "approve", "summary": "quality ok"},
            {"event_type": "worker_completed", "status": "success"},
        ],
        quality_jsonl,
    )

    bus = EventLoop()
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
            payload={"status": "success", "story_id": "4.1", "worktree": str(worktree)},
        )
        await code_review_subscriber(event, bus)

    verdicts = [e for e in _drain_bus(bus) if e.type == EventType.CODE_REVIEW_VERDICT]
    assert verdicts, "expected CODE_REVIEW_VERDICT event"
    payload = verdicts[-1].payload
    assert payload["verdict"] == "approve"
    assert payload["source"] == "merge_gate_spec"
