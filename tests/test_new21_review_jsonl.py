"""NEW-21 — review runner systematically returned verdict=error.

Root cause (pilot run #6 diagnosis): code-review / security-review / merge-gate
workers were spawned WITHOUT ``isolated_home=True``. Under the bwrap sandbox the
host ``~/.claude.json`` is then bind-mounted such that the inner ``claude -p``
aborts on startup with ``EROFS: read-only file system`` — ``num_turns=0``,
``is_error=true``, and the event stream carries no verdict → verdict=error every
run (#4/#5/#6). The dev worker survives because it spawns with a writable HOME
snapshot; the review workers never did.

Fix: the four review-type spawn helpers pass ``isolated_home=True`` (writable
``~/.claude*`` snapshot, same mechanism the dev worker uses).

Observability: the review worker events.jsonl path is threaded through
``_MergeGateStageResult`` so ``code_review_dispatched`` / ``CODE_REVIEW_VERDICT``
no longer report an empty ``review_jsonl=`` (it was hardcoded "").

5 tests (4 unit + 1 integration). See spec/spec_pilot_findings_closure_v7.md §1.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

import bmad_orchestrator.agent.run as run
from bmad_orchestrator.agent.run import (
    _run_merge_gate_spec_stage,
    _spawn_code_review_worker,
    _spawn_merge_gate_quality_worker,
    _spawn_merge_gate_spec_worker,
    _spawn_security_review_worker,
    code_review_subscriber,
    configure_code_review_gate,
)
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType

# ── Helpers ──────────────────────────────────────────────────────────────────


class _FakeHandle:
    def __init__(self, jsonl_path: Path) -> None:
        self.jsonl_path = jsonl_path


def _jsonl_events(events: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


def _drain(bus: EventLoop) -> list[Event]:
    out: list[Event] = []
    while True:
        try:
            out.append(bus.queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return out


def _worker_completed(worktree: Path, story_id: str = "1.5") -> Event:
    return Event(
        type=EventType.WORKER_COMPLETED,
        payload={"story_id": story_id, "worktree": str(worktree), "status": "success"},
    )


@pytest.fixture(autouse=True)
def reset_gate() -> Iterator[None]:
    configure_code_review_gate(target_project=None, wave=None)
    yield
    configure_code_review_gate(target_project=None, wave=None)


# ════════════════════════════════════════════════════════════════════════════
# Unit (4)
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_code_review_worker_spawns_with_isolated_home(tmp_path: Path) -> None:
    """ROOT FIX — the code-review worker must request a writable HOME snapshot.

    Fails on `main` (helper passed no `isolated_home` → default False → EROFS).
    """
    fake = AsyncMock(return_value=_FakeHandle(tmp_path / "x.jsonl"))
    with patch.object(run, "runtime_spawn_worker", fake):
        await _spawn_code_review_worker(
            worktree=str(tmp_path), story_id="1.5", wave="1a"
        )
    assert fake.call_args.kwargs.get("isolated_home") is True


@pytest.mark.asyncio
async def test_security_and_gate_workers_spawn_with_isolated_home(
    tmp_path: Path,
) -> None:
    """ROOT FIX — security-review + both merge-gate spawn helpers also request
    a writable HOME snapshot. Each fails on `main` for the same reason."""
    for helper in (
        _spawn_security_review_worker,
        _spawn_merge_gate_spec_worker,
        _spawn_merge_gate_quality_worker,
    ):
        fake = AsyncMock(return_value=_FakeHandle(tmp_path / "x.jsonl"))
        with patch.object(run, "runtime_spawn_worker", fake):
            await helper(worktree=str(tmp_path), story_id="1.5", wave="1a")
        assert fake.call_args.kwargs.get("isolated_home") is True, helper.__name__


@pytest.mark.asyncio
async def test_spec_stage_returns_review_jsonl_path(tmp_path: Path) -> None:
    """Observability — the spec stage now returns the real events.jsonl path
    as the 4th tuple element (was a 3-tuple → caller hardcoded "")."""
    jsonl_path = tmp_path / "1a__gate_spec_1.5" / "wt-1.5.events.jsonl"
    _jsonl_events(
        [
            {"verdict": "approve", "summary": "AC covered"},
            {"event_type": "worker_completed", "status": "success"},
        ],
        jsonl_path,
    )
    with patch.object(
        run,
        "_spawn_merge_gate_spec_worker",
        new_callable=AsyncMock,
        return_value=_FakeHandle(jsonl_path),
    ):
        verdict, _summary, _metrics, jsonl_str = await _run_merge_gate_spec_stage(
            worktree="/tmp/wt", story_id="1.5", wave="1a", bus=EventLoop()
        )
    assert verdict == "approve"
    assert jsonl_str == str(jsonl_path)


@pytest.mark.asyncio
async def test_eros_style_stream_yields_no_verdict(tmp_path: Path) -> None:
    """Documents the pre-fix symptom: a `claude -p` that aborted on EROFS emits
    only a `result/error_during_execution` event with num_turns=0 — no verdict
    token anywhere → the stage verdict stays "error" (exactly what every pilot
    run produced). A healthy stream with a verdict event extracts it."""
    eros = tmp_path / "eros.events.jsonl"
    _jsonl_events(
        [
            {
                "event_type": "claude_event",
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "num_turns": 0,
                "errors": ["EROFS: read-only file system, open '~/.claude.json'"],
            },
            {"event_type": "worker_completed", "status": "failure"},
        ],
        eros,
    )
    with patch.object(
        run,
        "_spawn_merge_gate_spec_worker",
        new_callable=AsyncMock,
        return_value=_FakeHandle(eros),
    ):
        verdict, _s, _m, jsonl_str = await _run_merge_gate_spec_stage(
            worktree="/tmp/wt", story_id="1.5", wave="1a", bus=EventLoop()
        )
    assert verdict == "error"  # no verdict in an aborted-claude stream
    assert jsonl_str == str(eros)  # path still surfaced for diagnosis


# ════════════════════════════════════════════════════════════════════════════
# Integration (1)
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_code_review_verdict_event_carries_real_review_jsonl(
    tmp_path: Path,
) -> None:
    """End-to-end — when both stages run on a healthy review stream, the
    emitted CODE_REVIEW_VERDICT carries the real `review_jsonl` path (was
    hardcoded "" → empty in every `code_review_dispatched` log line)."""
    spec_jsonl = tmp_path / "1a__gate_spec_1.5" / "wt-1.5.events.jsonl"
    quality_jsonl = tmp_path / "1a__gate_quality_1.5" / "wt-1.5.events.jsonl"
    for p in (spec_jsonl, quality_jsonl):
        _jsonl_events(
            [
                {"verdict": "approve", "summary": "ok"},
                {"event_type": "worker_completed", "status": "success"},
            ],
            p,
        )
    configure_code_review_gate(target_project=tmp_path, wave="1a")

    with (
        patch.object(
            run,
            "_spawn_merge_gate_spec_worker",
            new_callable=AsyncMock,
            return_value=_FakeHandle(spec_jsonl),
        ),
        patch.object(
            run,
            "_spawn_merge_gate_quality_worker",
            new_callable=AsyncMock,
            return_value=_FakeHandle(quality_jsonl),
        ),
    ):
        bus = EventLoop()
        await code_review_subscriber(_worker_completed(tmp_path), bus)

    verdicts = [
        e for e in _drain(bus) if e.type == EventType.CODE_REVIEW_VERDICT
    ]
    assert len(verdicts) == 1
    assert verdicts[0].payload["verdict"] == "approve"
    # NEW-21: quality stage is last → its jsonl path is surfaced (was "").
    assert verdicts[0].payload["review_jsonl"] == str(quality_jsonl)
