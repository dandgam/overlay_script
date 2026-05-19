"""NEW-13 — security_review verdict=error must not abort the pipeline.

A ``verdict=error`` from the security-review step is a *technical* failure of
the review itself, not a story defect. It must:

  * NOT increment the supervisor circuit breaker's consecutive-escalation
    counter (so 3 technical errors in a row do not trigger
    ``abort_pipeline``);
  * be retried up to ``error_retry_max`` times by the subscriber;
  * on persistent error, escalate the single offending story via a lone
    HUMAN_QUERY — the rest of the pipeline keeps running.

4 unit (SupervisorEngine circuit breaker) + 3 integration
(security_review_subscriber retry + engine wiring). See
spec/spec_pilot_findings_closure_v5.md §3.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.runtime.security_review import (
    VERDICT_APPROVE,
    VERDICT_ERROR,
    security_review_subscriber,
)
from bmad_orchestrator.supervisor.engine import SupervisorEngine
from bmad_orchestrator.supervisor.llm_judge import JudgeInput, JudgeVerdict
from bmad_orchestrator.supervisor.policy import Defaults, SupervisorPolicy


class _FixedJudge:
    """Judge that always returns the same verdict (low confidence → escalate)."""

    def __init__(self, verdict: JudgeVerdict) -> None:
        self.verdict = verdict

    async def classify(self, input_: JudgeInput) -> JudgeVerdict:
        return self.verdict


def _engine() -> SupervisorEngine:
    """Engine whose Tier 1 judge forces escalate_human (confidence 0.3 < floor)."""
    policy = SupervisorPolicy(version=1, defaults=Defaults())
    judge = _FixedJudge(
        JudgeVerdict(action="auto_respond", confidence=0.3, reason="unclear")
    )
    return SupervisorEngine(policy, judge=judge)


def _sec_error_payload(story_id: str = "4.1") -> dict[str, object]:
    """HUMAN_QUERY payload as emitted by the security_review error path."""
    return {
        "story_id": story_id,
        "verdict": "security_review_error",
        "security_verdict": "error",
    }


def _real_escalation_payload() -> dict[str, object]:
    """A genuine HUMAN_QUERY with no security_review error markers."""
    return {"story_id": "1.1", "reason": "ambiguous elicitation"}


# ════════════════════════════════════════════════════════════════════════════
# Unit — SupervisorEngine circuit breaker (4)
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_circuit_breaker_skips_security_review_error() -> None:
    """5 consecutive security_review-error HUMAN_QUERY events → counter stays
    at 0, never escalates to abort_pipeline."""
    engine = _engine()
    for _ in range(5):
        decision = await engine.decide("HUMAN_QUERY", _sec_error_payload())
        assert decision.action != "abort_pipeline"
    assert engine.consecutive_escalations == 0


@pytest.mark.asyncio
async def test_circuit_breaker_trips_on_real_escalations() -> None:
    """3 genuine escalations in a row → circuit breaker fires abort_pipeline
    (control: confirms the breaker still works for real escalations)."""
    engine = _engine()
    actions = []
    for _ in range(3):
        decision = await engine.decide("HUMAN_QUERY", _real_escalation_payload())
        actions.append(decision.action)
    assert actions[-1] == "abort_pipeline"
    assert engine.consecutive_escalations >= 3


@pytest.mark.asyncio
async def test_security_review_error_leaves_counter_unchanged() -> None:
    """A security_review error between real escalations neither increments nor
    resets the counter — 2 real + 1 error + 1 real → breaker trips on the
    last (counter reaches 3, error did not mask it)."""
    engine = _engine()
    await engine.decide("HUMAN_QUERY", _real_escalation_payload())
    await engine.decide("HUMAN_QUERY", _real_escalation_payload())
    assert engine.consecutive_escalations == 2
    # Technical error in the middle — counter must stay at 2.
    await engine.decide("HUMAN_QUERY", _sec_error_payload())
    assert engine.consecutive_escalations == 2
    final = await engine.decide("HUMAN_QUERY", _real_escalation_payload())
    assert final.action == "abort_pipeline"


def test_is_security_review_error_detection() -> None:
    """The static detector recognises the security_review error markers and
    rejects everything else."""
    detect = SupervisorEngine._is_security_review_error
    assert detect("HUMAN_QUERY", {"security_verdict": "error"}) is True
    assert detect("HUMAN_QUERY", {"verdict": "security_review_error"}) is True
    assert detect("human_query", {"security_verdict": "error"}) is True  # case
    assert detect("HUMAN_QUERY", {"verdict": "reject"}) is False
    assert detect("HUMAN_QUERY", {"security_verdict": "block"}) is False
    assert detect("WORKER_HALT_FILE", {"security_verdict": "error"}) is False
    assert detect("HUMAN_QUERY", {}) is False


# ════════════════════════════════════════════════════════════════════════════
# Integration — security_review_subscriber retry + engine wiring (3)
# ════════════════════════════════════════════════════════════════════════════


def _policy_file(tmp_path: Path, *, error_retry_max: int = 1) -> Path:
    """Minimal security-review policy; story 4.1 → epic 4 always triggers."""
    p = tmp_path / "security-review.yaml"
    p.write_text(
        "enabled: true\n"
        "security_critical_epics: [3, 4, 5, 7, 9, 10]\n"
        f"error_retry_max: {error_retry_max}\n"
        "escalation_text: 'Security review error'\n",
        encoding="utf-8",
    )
    return p


class _SequenceRunner:
    """Security-review runner that returns a pre-set sequence of verdicts."""

    def __init__(self, results: list[tuple[str, str]]) -> None:
        self._results = results
        self.calls = 0

    async def __call__(
        self, worktree: Path, story_id: str, wave: str
    ) -> tuple[str, str]:
        idx = min(self.calls, len(self._results) - 1)
        self.calls += 1
        return self._results[idx]


def _drain(bus: EventLoop) -> list[Event]:
    out: list[Event] = []
    while not bus.queue.empty():
        out.append(bus.queue.get_nowait())
    return out


def _approve_event(worktree: Path, story_id: str = "4.1") -> Event:
    return Event(
        type=EventType.CODE_REVIEW_VERDICT,
        payload={
            "verdict": "approve",
            "story_id": story_id,
            "worktree": str(worktree),
        },
    )


@pytest.mark.asyncio
async def test_runner_retried_then_success(tmp_path: Path) -> None:
    """Runner errors once, then approves → subscriber retries (2 calls total),
    final outcome is SECURITY_REVIEW_PASSED, no HUMAN_QUERY."""
    runner = _SequenceRunner([(VERDICT_ERROR, "transient boom"), (VERDICT_APPROVE, "ok")])
    bus = EventLoop()
    await security_review_subscriber(
        _approve_event(tmp_path),
        bus,
        policy_path=_policy_file(tmp_path, error_retry_max=1),
        runner=runner,
    )
    assert runner.calls == 2
    emitted = _drain(bus)
    types = [e.type for e in emitted]
    assert EventType.SECURITY_REVIEW_PASSED in types
    assert EventType.HUMAN_QUERY not in types
    assert types.count(EventType.SECURITY_REVIEW_ERROR) == 1  # the one retry


@pytest.mark.asyncio
async def test_persistent_error_escalates_single_story_not_abort(
    tmp_path: Path,
) -> None:
    """Runner errors forever → after retries the subscriber emits ONE
    HUMAN_QUERY, and routing that HUMAN_QUERY through the supervisor engine
    does NOT abort the pipeline (the engine skips the breaker increment)."""
    runner = _SequenceRunner([(VERDICT_ERROR, "persistent boom")])
    bus = EventLoop()
    await security_review_subscriber(
        _approve_event(tmp_path),
        bus,
        policy_path=_policy_file(tmp_path, error_retry_max=1),
        runner=runner,
    )
    assert runner.calls == 2  # 1 initial + 1 retry
    emitted = _drain(bus)
    human_queries = [e for e in emitted if e.type == EventType.HUMAN_QUERY]
    assert len(human_queries) == 1
    hq = human_queries[0]
    assert hq.payload["security_verdict"] == "error"

    # Feed the HUMAN_QUERY through the supervisor — pipeline must survive 3×.
    engine = _engine()
    for _ in range(3):
        decision = await engine.decide("HUMAN_QUERY", dict(hq.payload))
        assert decision.action != "abort_pipeline"
    assert engine.consecutive_escalations == 0


@pytest.mark.asyncio
async def test_security_review_error_events_emitted_per_attempt(
    tmp_path: Path,
) -> None:
    """With error_retry_max=2 and a persistent error, the subscriber makes 3
    attempts and emits one SECURITY_REVIEW_ERROR per attempt; the final one is
    marked retrying=False."""
    runner = _SequenceRunner([(VERDICT_ERROR, "boom")])
    bus = EventLoop()
    await security_review_subscriber(
        _approve_event(tmp_path),
        bus,
        policy_path=_policy_file(tmp_path, error_retry_max=2),
        runner=runner,
    )
    assert runner.calls == 3  # 1 initial + 2 retries
    emitted = _drain(bus)
    sre = [e for e in emitted if e.type == EventType.SECURITY_REVIEW_ERROR]
    assert len(sre) == 3
    assert [e.payload["attempt"] for e in sre] == [1, 2, 3]
    assert [e.payload["retrying"] for e in sre] == [True, True, False]
