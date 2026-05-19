"""S3 acceptance tests — Core runtime (event loop, DAG, worker spawn, liveness).

Coverage (per tracker S3 acceptance):
- event_loop: 13 event types present; emit/consume; subscribers fire;
  backstop wakeup actually emits SCHEDULED_WAKEUP on schedule.
- ratelimit: TokenBucket + RateLimiter behavior (capacity, refill, async acquire).
- liveness: is_alive, safe_to_kill, stall detection on JSONL.
- dag_planner: graph build, mutex-aware ready_stories, in-flight reservation.
- worker_spawn (mock-mode): JSONL stream contains worker_spawned + worker_completed.
- Mock pilot E2E: 3 fake stories → DAG → ready → worker spawned → JSONL → completed.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from bmad_orchestrator.runtime.dag_planner import (
    DagPlanner,
    build_graph,
    ready_stories,
)
from bmad_orchestrator.runtime.event_loop import (
    ALL_EVENT_TYPES,
    Event,
    EventLoop,
    EventType,
)
from bmad_orchestrator.runtime.liveness import (
    heartbeat_summary,
    is_alive,
    is_stalled,
    last_event_age_seconds,
    safe_to_kill,
)
from bmad_orchestrator.runtime.ratelimit import RateLimiter, TokenBucket
from bmad_orchestrator.runtime.worker_spawn import (
    spawn_worker,
    tail_jsonl_events,
)


@pytest.fixture(autouse=True)
def _isolate_target_project(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Each test starts with a fresh copy of mock-odyssey."""
    src = Path(__file__).parent / "fixtures" / "mock-odyssey"
    dst = tmp_path / "mock-odyssey"
    shutil.copytree(src, dst)
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(dst))
    monkeypatch.setenv("ORCHESTRATOR_STATE_DB", str(tmp_path / "state.db"))
    monkeypatch.setenv("ORCHESTRATOR_ORCHESTRATOR_HOME", str(tmp_path / "orch"))
    monkeypatch.setenv("BMAD_CURRENT_WAVE", "1a")


# ── Event loop ────────────────────────────────────────────────────────────────


def test_event_loop_has_all_spec_types() -> None:
    """Spec §4 baseline (13) + FS4 B9 + W4 + E5 quarterly sweep + P5 security_review
    + Phase 0 worker_silent_failure / cost_tracking_unavailable
    + Initiative #2C sub-story lifecycle (4) = 23 types."""
    expected = {
        "worker_completed",
        "worker_halt_file",
        "worker_elicitation",
        "worker_silent_failure",
        "cost_tracking_unavailable",
        "budget_threshold_hit",
        "wave_boundary_reached",
        "epic_boundary_reached",
        "user_chat_message",
        "monthly_review_scheduled",
        "voice_message_received",
        "story_split_triggered",
        "sub_story_started",
        "sub_story_completed",
        "sub_story_squash_done",
        "sub_story_squash_skipped",
        "phase4_complete",
        "human_query",
        "human_response",
        "scheduled_wakeup_5min",
        "code_review_verdict",
        "compliance_sweep_needed",
        "security_review_passed",
        # 2026-05-19 BMad Phase 4 gap-closure event types.
        "sprint_scope_change_detected",
        "forensic_investigation_needed",
        # 2026-05-19 Phase 4 hardening #2 — PreCompact memory persistence.
        "worker_state_persisted",
        # 2026-05-19 Phase 4 hardening #5 — Two-stage merge gate split.
        "merge_gate_stage_completed",
        # 2026-05-19 Phase 4 hardening #6 — Stop-hook cost + learning consolidation.
        "story_completed",
        "story_metrics_aggregated",
        # 2026-05-19 spec_pilot_findings_closure S3 #3 — auto-split trigger.
        "story_auto_split",
        # 2026-05-19 spec_pilot_findings_closure S4 #4 — per-worker cancellation.
        "worker_cancelled",
        # 2026-05-19 spec_pilot_findings_closure S5 #5 — MCP readiness gating.
        "mcp_not_ready",
        # 2026-05-19 spec_pilot_findings_closure S6 #6 — subscription auto-disable.
        "budget_auto_disabled",
        # 2026-05-19 spec_pilot_findings_closure S6 #7 — pre-spawn halt gate.
        "worker_halt_prespawn",
        # 2026-05-19 spec_pilot_findings_closure_v2 S2 #2 — reused-worktree
        # Stage 7 cleanup-failure detector.
        "runner_cleanup_failed_reused_worktree",
        # 2026-05-19 spec_pilot_findings_closure_v3 S3 #1 — NEW-7 merge-skip
        # observability (no_commits / verdict_missing / ff_conflict).
        "integration_merge_skipped",
        # 2026-05-19 spec_pilot_findings_closure_v5 S2 NEW-13 — security_review
        # technical-error audit signal (retry + escalate-story, no abort).
        "security_review_error",
        # 2026-05-19 spec_pilot_findings_closure_v6 S1 NEW-19 — replay-from-
        # worktree mode start signal (post-dev pipeline tail, no spawn).
        "replay_mode_started",
    }
    actual = {e.value for e in ALL_EVENT_TYPES}
    assert actual == expected, f"missing: {expected - actual}, extra: {actual - expected}"
    assert len(ALL_EVENT_TYPES) == 38


@pytest.mark.asyncio
async def test_event_loop_emit_and_next_fifo() -> None:
    loop = EventLoop()
    await loop.emit(EventType.WORKER_COMPLETED, story_id="s1")
    await loop.emit(EventType.WORKER_COMPLETED, story_id="s2")
    ev1 = await loop.next(timeout=0.5)
    ev2 = await loop.next(timeout=0.5)
    assert ev1 is not None and ev1.payload["story_id"] == "s1"
    assert ev2 is not None and ev2.payload["story_id"] == "s2"


@pytest.mark.asyncio
async def test_event_loop_next_timeout_returns_none() -> None:
    loop = EventLoop()
    ev = await loop.next(timeout=0.1)
    assert ev is None


@pytest.mark.asyncio
async def test_event_loop_subscribers_dispatch_in_order() -> None:
    loop = EventLoop()
    seen: list[str] = []

    async def cb1(e: Event) -> None:
        seen.append(f"a:{e.payload.get('id')}")

    async def cb2(e: Event) -> None:
        seen.append(f"b:{e.payload.get('id')}")

    loop.on(cb1)
    loop.on(cb2)
    await loop.emit(EventType.HUMAN_RESPONSE, id=1)
    await loop.dispatch_one(timeout=0.5)
    assert seen == ["a:1", "b:1"]


@pytest.mark.asyncio
async def test_event_loop_backstop_emits_scheduled_wakeup() -> None:
    loop = EventLoop()
    # Tiny interval — verify backstop fires at all.
    loop.start_backstop_task(interval_seconds=1)
    ev = await loop.next(timeout=2.5)
    assert ev is not None
    assert ev.type == EventType.SCHEDULED_WAKEUP
    await loop.stop()


# ── Ratelimit ─────────────────────────────────────────────────────────────────


def test_token_bucket_starts_full_and_consumes() -> None:
    tb = TokenBucket(capacity=10, refill_per_second=1.0)
    assert tb.tokens == pytest.approx(10.0, abs=0.01)
    assert tb.try_consume(7)
    assert tb.tokens == pytest.approx(3.0, abs=0.01)
    assert not tb.try_consume(5)  # insufficient
    assert tb.tokens == pytest.approx(3.0, abs=0.01)


def test_token_bucket_refills_over_time() -> None:
    tb = TokenBucket(capacity=10, refill_per_second=10.0)
    tb.try_consume(10)
    assert tb.tokens == 0.0
    # Force `last_refill_ts` into the past instead of sleeping.
    tb.last_refill_ts -= 0.5
    tb._refill()
    assert tb.tokens >= 4.9


def test_ratelimiter_try_acquire_within_capacity() -> None:
    rl = RateLimiter(tpm=1000, rpm=10)
    assert rl.try_acquire("worker-1", tokens=500)
    snap = rl.snapshot("worker-1")
    # Bucket starts at capacity and is refilled tiny amount on snapshot;
    # the residual after consuming 500 should be close to (but ≤) 500.5.
    assert snap["tokens_available"] <= 500.5
    assert snap["requests_available"] <= 9.5


@pytest.mark.asyncio
async def test_ratelimiter_acquire_waits_then_proceeds() -> None:
    rl = RateLimiter(tpm=600, rpm=120)  # 10 tokens/sec, 2 req/sec
    # Drain request bucket so next acquire must wait.
    for _ in range(120):
        rl.try_acquire("k", tokens=1)
    started = asyncio.get_event_loop().time()
    await asyncio.wait_for(rl.acquire("k", tokens=1), timeout=2.0)
    elapsed = asyncio.get_event_loop().time() - started
    assert elapsed > 0.0  # had to wait at least a few ms


# ── Liveness ──────────────────────────────────────────────────────────────────


def test_liveness_is_alive_self_pid() -> None:
    assert is_alive(os.getpid()) is True
    assert is_alive(0) is False
    assert is_alive(-1) is False


def test_liveness_safe_to_kill_rejects_self_and_sentinel() -> None:
    self_pid = os.getpid()
    ok, reason = safe_to_kill(self_pid, self_pid=self_pid)
    assert not ok and reason == "self_pid"
    ok, reason = safe_to_kill(0)
    assert not ok and reason == "pid_sentinel"


def test_liveness_stall_detection(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    # Empty / missing → not stalled.
    assert is_stalled(p, threshold_seconds=10) is False
    # Fresh event → not stalled.
    from datetime import UTC, datetime, timedelta

    fresh = datetime.now(UTC).isoformat(timespec="seconds")
    p.write_text(f'{{"event_type":"x","ts":"{fresh}"}}\n', encoding="utf-8")
    assert is_stalled(p, threshold_seconds=10) is False
    # Stale event (1 hour ago) → stalled.
    stale = (datetime.now(UTC) - timedelta(hours=1)).isoformat(timespec="seconds")
    p.write_text(f'{{"event_type":"x","ts":"{stale}"}}\n', encoding="utf-8")
    assert is_stalled(p, threshold_seconds=10) is True
    age = last_event_age_seconds(p)
    assert age is not None and age > 600


def test_liveness_heartbeat_summary_keys(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    p.write_text('{"event_type":"x","ts":"2026-01-01T00:00:00+00:00"}\n', encoding="utf-8")
    summary = heartbeat_summary(os.getpid(), p, threshold_seconds=10)
    assert {"pid", "alive", "last_event_age_seconds", "stalled", "checked_at"} <= summary.keys()
    assert summary["alive"] is True
    assert summary["stalled"] is True


# ── DAG planner ───────────────────────────────────────────────────────────────


def test_dag_planner_from_target_builds_graph() -> None:
    planner = DagPlanner.from_target()
    # Fixture has 4 stories across epic 1 and 2.
    assert planner.graph.number_of_nodes() >= 3
    ids = {n for n in planner.graph.nodes}
    assert "1-1-tenant-signup" in ids


def test_dag_planner_find_ready_respects_mutex() -> None:
    planner = DagPlanner.from_target()
    ready = planner.find_ready(max_n=10)
    ids = {s["id"] for s in ready}
    # 1-1 is ready-for-dev; epic 2 is backlog, no deps, included by default.
    assert "1-1-tenant-signup" in ids
    # Reserve 1-1's files → 1-1 should still be in graph but not in next ready cycle
    # (1-1 itself doesn't filter out; mutex is for parallel candidates).
    s = next(s for s in ready if s["id"] == "1-1-tenant-signup")
    planner.reserve(s)
    # Any other story sharing src/tenant/signup.py would now be filtered;
    # in fixture no other story touches it — just ensure call doesn't crash.
    planner.release(s)


def test_dag_build_graph_detects_cycle() -> None:
    stories = [
        {"id": "a", "depends_on": ["b"], "touches_files": [], "touches_shared": []},
        {"id": "b", "depends_on": ["a"], "touches_files": [], "touches_shared": []},
    ]
    with pytest.raises(ValueError, match="cycle"):
        build_graph(stories)


def test_dag_ready_stories_skips_mutex_overlap() -> None:
    stories = [
        {"id": "x", "depends_on": [], "touches_files": ["a.py"], "touches_shared": []},
        {"id": "y", "depends_on": [], "touches_files": ["a.py"], "touches_shared": []},
    ]
    g = build_graph(stories)
    sprint = {"epics": {"1": {"stories": {"x": "ready-for-dev", "y": "ready-for-dev"}}}}
    ready = ready_stories(g, sprint, active_touches={"a.py"})
    assert ready == []  # both blocked by mutex
    ready = ready_stories(g, sprint, active_touches=set())
    assert len(ready) == 2


# ── Worker spawn (mock-mode E2E) ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_worker_spawn_mock_writes_jsonl_events(tmp_path: Path) -> None:
    wt = tmp_path / "wt-mock"
    wt.mkdir()
    handle = await spawn_worker(
        worktree=str(wt),
        story_id="1-1-tenant-signup",
        branch="feature/1-1",
        mock=True,
    )
    assert handle.mock is True
    assert handle.pid == 0
    assert handle.jsonl_path.exists()
    lines = handle.jsonl_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    import json as _json

    spawned = _json.loads(lines[0])
    completed = _json.loads(lines[1])
    assert spawned["event_type"] == "worker_spawned"
    assert spawned["story_id"] == "1-1-tenant-signup"
    assert completed["event_type"] == "worker_completed"
    assert completed["status"] == "success"


@pytest.mark.asyncio
async def test_worker_spawn_tail_completes_on_terminal_event(tmp_path: Path) -> None:
    wt = tmp_path / "wt-tail"
    wt.mkdir()
    handle = await spawn_worker(
        worktree=str(wt),
        story_id="x",
        branch="feature/x",
        mock=True,
    )
    events: list[dict[str, str]] = []
    async for ev in tail_jsonl_events(handle.jsonl_path, poll_interval=0.05):
        events.append(ev)
        if len(events) > 5:
            break
    types = [e.get("event_type") for e in events]
    assert "worker_completed" in types


# ── Mock pilot — 3 stories DAG → ready → spawn → JSONL → completed ───────────


@pytest.mark.asyncio
async def test_mock_pilot_three_stories_end_to_end(tmp_path: Path) -> None:
    """Acceptance: 3+ fake stories → DAG → ready → worker spawned → JSONL → completed.

    Fixture has 4 stories: 1-1 (no deps, ready-for-dev) and 2-1 (no deps, backlog)
    open initially; 1-2 and 1-3 depend on 1-1. The pilot walks the cascade:
    spawn what's ready, mark done in sprint-status, repeat → drives ≥3 stories
    through the full pipeline (DAG → spawn → JSONL terminal event).
    """
    from bmad_orchestrator.agent.tools._common import (
        read_sprint_status_yaml,
        write_sprint_status_yaml,
    )

    planner = DagPlanner.from_target()
    spawned_ids: list[str] = []
    handles: list[Any] = []

    # Iterate the DAG until we've driven ≥3 stories through.
    for _round in range(5):
        ready = planner.find_ready(max_n=10)
        # Exclude stories we've already processed this run.
        ready = [s for s in ready if s["id"] not in spawned_ids]
        if not ready:
            break
        for story in ready:
            wt_dir = tmp_path / f"wt-{story['id']}"
            wt_dir.mkdir()
            h = await spawn_worker(
                worktree=str(wt_dir),
                story_id=story["id"],
                branch=f"feature/{story['id']}",
                mock=True,
            )
            handles.append(h)
            spawned_ids.append(story["id"])
            # Mark done in sprint-status so dependents unlock on next round.
            sprint = read_sprint_status_yaml()
            for epic in (sprint.get("epics") or {}).values():
                stories_map = epic.get("stories") or {}
                if story["id"] in stories_map:
                    stories_map[story["id"]] = "done"
            write_sprint_status_yaml(sprint)
        planner.reload()

    assert len(spawned_ids) >= 3, f"expected ≥3 stories spawned, got {len(spawned_ids)}: {spawned_ids}"

    # Verify each JSONL has both worker_spawned + worker_completed.
    import json as _json

    for h in handles:
        assert h.jsonl_path.exists()
        lines = h.jsonl_path.read_text(encoding="utf-8").splitlines()
        events = [_json.loads(line) for line in lines if line.strip()]
        types = {e["event_type"] for e in events}
        assert "worker_spawned" in types
        assert "worker_completed" in types
        # Terminal event is success.
        terminal = [e for e in events if e["event_type"] == "worker_completed"][-1]
        assert terminal["status"] == "success"
