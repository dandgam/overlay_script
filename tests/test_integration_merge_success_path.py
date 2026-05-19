"""NEW-7 — verdict→integration pipeline (spec_pilot_findings_closure_v3 §1 #1).

S2 scope: the explicit bus drain (:meth:`EventLoop.drain`) and the post-worker
reconcile step (:func:`agent.run._reconcile_success_verdicts`). Together they
guarantee that a succeeded story with commits past ``base_sha`` always gets a
``CODE_REVIEW_VERDICT`` before the pilot loop ends — the precondition for
``merge_to_integration_subscriber`` to ever create ``integration/<wave>``.

S3 extends this file with the INTEGRATION_MERGE_SKIPPED observability event
and the full real-pilot-mock integration tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bmad_orchestrator.agent import run
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.runtime.worker_spawn import WorkerHandle


def _handle(story_id: str, worktree: str = "/tmp/wt", base_sha: str | None = "base") -> WorkerHandle:
    return WorkerHandle(
        worktree=worktree,
        story_id=story_id,
        branch=f"feature/{story_id}",
        pid=0,
        jsonl_path=Path("/tmp/jsonl"),
        process=None,
        mock=True,
        sandbox_kind="n/a-mock",
        base_sha=base_sha,
    )


# ── EventLoop.drain ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_drain_dispatches_queued_events() -> None:
    """drain() runs every queued event through subscribers and returns them."""
    bus = EventLoop()
    seen: list[EventType] = []

    async def recorder(event: Event) -> None:
        seen.append(event.type)

    bus.on(recorder)
    await bus.emit(EventType.WORKER_COMPLETED, story_id="1.5", status="success")
    await bus.emit(EventType.WAVE_BOUNDARY_REACHED, wave="wave-1a")

    dispatched = await bus.drain()

    assert [e.type for e in dispatched] == [
        EventType.WORKER_COMPLETED,
        EventType.WAVE_BOUNDARY_REACHED,
    ]
    assert seen == [EventType.WORKER_COMPLETED, EventType.WAVE_BOUNDARY_REACHED]


@pytest.mark.asyncio
async def test_drain_processes_cascades() -> None:
    """A subscriber that emits a follow-up event — drain dispatches that too."""
    bus = EventLoop()

    async def cascader(event: Event) -> None:
        if event.type == EventType.WORKER_COMPLETED:
            await bus.emit(EventType.CODE_REVIEW_VERDICT, story_id="1.5", verdict="approve")

    bus.on(cascader)
    await bus.emit(EventType.WORKER_COMPLETED, story_id="1.5", status="success")

    dispatched = await bus.drain()

    assert EventType.CODE_REVIEW_VERDICT in [e.type for e in dispatched]


@pytest.mark.asyncio
async def test_drain_respects_max_events_cap() -> None:
    """A subscriber re-emitting its own trigger cannot spin drain forever."""
    bus = EventLoop()

    async def loopback(event: Event) -> None:
        if event.type == EventType.SCHEDULED_WAKEUP:
            await bus.emit(EventType.SCHEDULED_WAKEUP)

    bus.on(loopback)
    await bus.emit(EventType.SCHEDULED_WAKEUP)

    dispatched = await bus.drain(max_events=20)

    assert len(dispatched) == 20


# ── _reconcile_success_verdicts ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reconcile_emits_synthetic_verdict_for_stranded_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """success + commits + no verdict → synthetic approve; zero commits → skipped."""
    bus = EventLoop()

    async def fake_count(worktree: str, base_sha: str) -> int:
        return {"1.5": 2, "1.6": 0}[worktree.rsplit("-", 1)[-1]]

    monkeypatch.setattr(run, "_count_new_commits", fake_count)

    handles = [
        _handle("1.5", worktree="/tmp/wt-1.5"),
        _handle("1.6", worktree="/tmp/wt-1.6"),
    ]
    reconciled = await run._reconcile_success_verdicts(
        bus, succeeded=["1.5", "1.6"], handles=handles, dispatched=[]
    )

    assert reconciled == ["1.5"]
    queued = await bus.drain()
    verdicts = [e for e in queued if e.type == EventType.CODE_REVIEW_VERDICT]
    assert len(verdicts) == 1
    payload = verdicts[0].payload
    assert payload["story_id"] == "1.5"
    assert payload["verdict"] == "approve"
    assert payload["source"] == "success_path_reconcile"
    assert payload["commits"] == 2


@pytest.mark.asyncio
async def test_reconcile_no_double_emit_when_verdict_seen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A verdict already surfaced by the merge gate is never overridden."""
    bus = EventLoop()

    async def fake_count(worktree: str, base_sha: str) -> int:
        return 3

    monkeypatch.setattr(run, "_count_new_commits", fake_count)

    # Merge gate already produced a (reject) verdict for 1.5 during drain #1.
    already = Event(
        type=EventType.CODE_REVIEW_VERDICT,
        payload={"story_id": "1.5", "verdict": "reject"},
    )
    reconciled = await run._reconcile_success_verdicts(
        bus, succeeded=["1.5"], handles=[_handle("1.5")], dispatched=[already]
    )

    assert reconciled == []
    assert bus.queue.empty()
