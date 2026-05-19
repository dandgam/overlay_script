"""NEW-15 — code_review verdict=error must not unconditionally block the gate.

A ``verdict=error`` from the two-stage merge gate is a *technical* failure of
the review step (empty review JSONL, spawn failure), not a story defect.
Mirroring NEW-13 (security_review), it must:

  * be retried up to ``CODE_REVIEW_ERROR_RETRY_MAX`` times;
  * on persistent error, escalate the single story via ONE HUMAN_QUERY
    (verdict=code_review_error) — NOT a CODE_REVIEW_VERDICT(error) that would
    feed the supervisor circuit breaker;
  * a runner-log fallback verdict must carry through to the final merge-gate
    verdict even when the quality stage independently yields ``error``.

3 unit + 2 integration. See spec/spec_pilot_findings_closure_v6.md §3.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import bmad_orchestrator.agent.run as run
from bmad_orchestrator.agent.run import code_review_subscriber, configure_code_review_gate
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.supervisor.engine import SupervisorEngine
from bmad_orchestrator.supervisor.llm_judge import JudgeInput, JudgeVerdict
from bmad_orchestrator.supervisor.policy import Defaults, SupervisorPolicy

# ── Helpers ──────────────────────────────────────────────────────────────────


class _SequenceStage:
    """Stand-in for a merge-gate stage runner returning a verdict sequence."""

    def __init__(self, verdicts: list[str]) -> None:
        self._verdicts = verdicts
        self.calls = 0

    async def __call__(
        self, *, worktree: str, story_id: str, wave: str, bus: EventLoop
    ) -> tuple[str, str, None, str]:
        idx = min(self.calls, len(self._verdicts) - 1)
        self.calls += 1
        v = self._verdicts[idx]
        # 4-tuple since NEW-21 (verdict, summary, metrics, jsonl_path).
        return v, f"{v} summary", None, ""


def _drain(bus: EventLoop) -> list[Event]:
    out: list[Event] = []
    while not bus.queue.empty():
        out.append(bus.queue.get_nowait())
    return out


def _worker_completed(worktree: Path, story_id: str = "1.4") -> Event:
    return Event(
        type=EventType.WORKER_COMPLETED,
        payload={"story_id": story_id, "worktree": str(worktree), "status": "success"},
    )


def _engine() -> SupervisorEngine:
    """Engine whose Tier 1 judge forces escalate_human (low confidence)."""

    class _FixedJudge:
        async def classify(self, input_: JudgeInput) -> JudgeVerdict:
            return JudgeVerdict(action="auto_respond", confidence=0.3, reason="x")

    policy = SupervisorPolicy(version=1, defaults=Defaults())
    return SupervisorEngine(policy, judge=_FixedJudge())


# ════════════════════════════════════════════════════════════════════════════
# Unit (3)
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_code_review_error_retried_then_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec stage errors once then approves → subscriber retries the gate;
    final verdict is approve, exactly one CODE_REVIEW_ERROR for the retry."""
    spec = _SequenceStage(["error", "approve"])
    quality = _SequenceStage(["approve"])
    monkeypatch.setattr(run, "_run_merge_gate_spec_stage", spec)
    monkeypatch.setattr(run, "_run_merge_gate_quality_stage", quality)
    monkeypatch.setenv("CODE_REVIEW_ERROR_RETRY_MAX", "1")
    configure_code_review_gate(target_project=tmp_path, wave="1a")

    bus = EventLoop()
    await code_review_subscriber(_worker_completed(tmp_path), bus)

    assert spec.calls == 2  # initial + 1 retry
    emitted = _drain(bus)
    types = [e.type for e in emitted]
    assert types.count(EventType.CODE_REVIEW_ERROR) == 1
    verdicts = [e for e in emitted if e.type == EventType.CODE_REVIEW_VERDICT]
    assert len(verdicts) == 1
    assert verdicts[0].payload["verdict"] == "approve"


@pytest.mark.asyncio
async def test_persistent_error_escalates_single_story_not_abort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec stage errors forever → after retries the subscriber emits ONE
    HUMAN_QUERY (verdict=code_review_error) and NO CODE_REVIEW_VERDICT; routing
    that HUMAN_QUERY through the supervisor does NOT abort the pipeline."""
    spec = _SequenceStage(["error"])
    monkeypatch.setattr(run, "_run_merge_gate_spec_stage", spec)
    monkeypatch.setattr(run, "parse_runner_review_log", lambda wt, sid: None)
    monkeypatch.setenv("CODE_REVIEW_ERROR_RETRY_MAX", "1")
    configure_code_review_gate(target_project=tmp_path, wave="1a")

    bus = EventLoop()
    await code_review_subscriber(_worker_completed(tmp_path), bus)

    assert spec.calls == 2  # initial + 1 retry
    emitted = _drain(bus)
    assert EventType.CODE_REVIEW_VERDICT not in [e.type for e in emitted]
    human = [e for e in emitted if e.type == EventType.HUMAN_QUERY]
    assert len(human) == 1
    assert human[0].payload["verdict"] == "code_review_error"

    # The escalation must NOT feed the circuit breaker — 3× must not abort.
    engine = _engine()
    for _ in range(3):
        decision = await engine.decide("HUMAN_QUERY", dict(human[0].payload))
        assert decision.action != "abort_pipeline"
    assert engine.consecutive_escalations == 0


@pytest.mark.asyncio
async def test_fallback_verdict_applied_over_quality_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sub-bug (b): spec stage errors → runner-log fallback yields approve →
    quality stage independently errors → the fallback verdict carries through
    to the final merge-gate verdict instead of being worst-wins-merged to
    error."""
    spec = _SequenceStage(["error"])
    quality = _SequenceStage(["error"])
    monkeypatch.setattr(run, "_run_merge_gate_spec_stage", spec)
    monkeypatch.setattr(run, "_run_merge_gate_quality_stage", quality)
    monkeypatch.setattr(
        run, "parse_runner_review_log", lambda wt, sid: ("approve", "runner log PASS")
    )
    configure_code_review_gate(target_project=tmp_path, wave="1a")

    bus = EventLoop()
    await code_review_subscriber(_worker_completed(tmp_path), bus)

    emitted = _drain(bus)
    verdicts = [e for e in emitted if e.type == EventType.CODE_REVIEW_VERDICT]
    assert len(verdicts) == 1
    assert verdicts[0].payload["verdict"] == "approve"
    assert verdicts[0].payload["source"] == "runner_log_fallback"


# ════════════════════════════════════════════════════════════════════════════
# Integration (2)
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_error_emits_one_event_per_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With error_retry_max=2 and a persistent error, the subscriber makes 3
    gate attempts and emits one CODE_REVIEW_ERROR per attempt; the final one
    is marked retrying=False."""
    spec = _SequenceStage(["error"])
    monkeypatch.setattr(run, "_run_merge_gate_spec_stage", spec)
    monkeypatch.setattr(run, "parse_runner_review_log", lambda wt, sid: None)
    monkeypatch.setenv("CODE_REVIEW_ERROR_RETRY_MAX", "2")
    configure_code_review_gate(target_project=tmp_path, wave="1a")

    bus = EventLoop()
    await code_review_subscriber(_worker_completed(tmp_path), bus)

    assert spec.calls == 3  # 1 initial + 2 retries
    emitted = _drain(bus)
    cre = [e for e in emitted if e.type == EventType.CODE_REVIEW_ERROR]
    assert [e.payload["attempt"] for e in cre] == [1, 2, 3]
    assert [e.payload["retrying"] for e in cre] == [True, True, False]


@pytest.mark.asyncio
async def test_fallback_approve_reaches_merge_gate_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec stage errors → runner-log fallback yields approve → quality stage
    approves → final CODE_REVIEW_VERDICT is approve via runner_log_fallback,
    no retry needed (fallback already lifted the verdict off error)."""
    spec = _SequenceStage(["error"])
    quality = _SequenceStage(["approve"])
    monkeypatch.setattr(run, "_run_merge_gate_spec_stage", spec)
    monkeypatch.setattr(run, "_run_merge_gate_quality_stage", quality)
    monkeypatch.setattr(
        run, "parse_runner_review_log", lambda wt, sid: ("approve", "PASS")
    )
    configure_code_review_gate(target_project=tmp_path, wave="1a")

    bus = EventLoop()
    await code_review_subscriber(_worker_completed(tmp_path), bus)

    assert spec.calls == 1  # fallback lifted error → no retry
    emitted = _drain(bus)
    assert EventType.CODE_REVIEW_ERROR not in [e.type for e in emitted]
    verdicts = [e for e in emitted if e.type == EventType.CODE_REVIEW_VERDICT]
    assert len(verdicts) == 1
    assert verdicts[0].payload["verdict"] == "approve"
    assert verdicts[0].payload["source"] == "runner_log_fallback"
