"""Tests for SupervisorEngine — Tier 0/1/2 + rate-limit + circuit breaker."""

from __future__ import annotations

import pytest

from bmad_orchestrator.supervisor.engine import SupervisorEngine
from bmad_orchestrator.supervisor.llm_judge import (
    JudgeError,
    JudgeInput,
    JudgeVerdict,
)
from bmad_orchestrator.supervisor.policy import (
    Defaults,
    HardRule,
    HardRuleMatch,
    SupervisorPolicy,
    ToolCall,
)


class _FixedJudge:
    def __init__(self, verdict: JudgeVerdict) -> None:
        self.verdict = verdict
        self.calls = 0

    async def classify(self, input_: JudgeInput) -> JudgeVerdict:
        self.calls += 1
        return self.verdict


class _FailingJudge:
    async def classify(self, input_: JudgeInput) -> JudgeVerdict:
        raise JudgeError("simulated transport failure")


def _policy(
    rules: list[HardRule] | None = None,
    defaults: Defaults | None = None,
) -> SupervisorPolicy:
    return SupervisorPolicy(
        version=1,
        defaults=defaults or Defaults(),
        hard_rules=rules or [],
    )


# ── Tier 0 ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tier0_hard_rule_match():
    rule = HardRule(
        id="budget-cap",
        when=HardRuleMatch(event="BUDGET_THRESHOLD_HIT", ratio_gte=1.0),
        action="pause_workers",
        reason="hard cap",
    )
    engine = SupervisorEngine(_policy(rules=[rule]))
    d = await engine.decide("BUDGET_THRESHOLD_HIT", {"ratio": 1.05})
    assert d.action == "pause_workers"
    assert d.tier == 0
    assert d.rule_id == "budget-cap"
    assert d.confidence == 1.0


@pytest.mark.asyncio
async def test_tier0_ratio_below_threshold_skipped():
    rule = HardRule(
        id="budget-cap",
        when=HardRuleMatch(event="BUDGET_THRESHOLD_HIT", ratio_gte=1.0),
        action="pause_workers",
        reason="r",
    )
    judge = _FixedJudge(JudgeVerdict(action="no_op", confidence=0.99, reason="below cap"))
    engine = SupervisorEngine(_policy(rules=[rule]), judge=judge)
    d = await engine.decide("BUDGET_THRESHOLD_HIT", {"ratio": 0.5})
    # ratio_gte=1.0 not met → falls through to Tier 1
    assert d.tier == 1
    assert d.action == "no_op"


@pytest.mark.asyncio
async def test_tier0_with_tool_calls():
    rule = HardRule(
        id="sweep",
        when=HardRuleMatch(event="COMPLIANCE_SWEEP_NEEDED"),
        action="auto_respond",
        tool_calls=[ToolCall(name="trigger_compliance_sweep")],
        reason="routine",
    )
    engine = SupervisorEngine(_policy(rules=[rule]))
    d = await engine.decide("COMPLIANCE_SWEEP_NEEDED", {})
    assert d.action == "auto_respond"
    assert len(d.tool_calls) == 1
    assert d.tool_calls[0].name == "trigger_compliance_sweep"


# ── Tier 1 (LLM judge) ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tier1_high_confidence_auto():
    judge = _FixedJudge(
        JudgeVerdict(action="auto_respond", confidence=0.95, reason="clear case")
    )
    engine = SupervisorEngine(_policy(), judge=judge)
    d = await engine.decide("HUMAN_QUERY", {"question": "format?"})
    assert d.action == "auto_respond"
    assert d.tier == 1
    assert judge.calls == 1


@pytest.mark.asyncio
async def test_tier1_medium_confidence_escalates():
    judge = _FixedJudge(
        JudgeVerdict(
            action="auto_respond",
            confidence=0.7,
            reason="uncertain — better ask",
        )
    )
    engine = SupervisorEngine(_policy(), judge=judge)
    d = await engine.decide("HUMAN_QUERY", {})
    assert d.action == "escalate_human"
    assert d.tier == 1
    assert "below auto threshold" in d.reason


@pytest.mark.asyncio
async def test_tier1_low_confidence_escalates_no_suggestion():
    judge = _FixedJudge(
        JudgeVerdict(action="auto_respond", confidence=0.3, reason="unclear")
    )
    engine = SupervisorEngine(_policy(), judge=judge)
    d = await engine.decide("HUMAN_QUERY", {})
    assert d.action == "escalate_human"
    assert d.tier == 1
    assert "below" in d.reason or "escalat" in d.reason


# ── Tier 2 (LLM error → fail-safe) ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_judge_error_fail_safe_escalates():
    engine = SupervisorEngine(_policy(), judge=_FailingJudge())
    d = await engine.decide("HUMAN_QUERY", {})
    assert d.action == "escalate_human"
    assert d.tier == 2
    assert "fail-safe" in d.reason.lower()


@pytest.mark.asyncio
async def test_judge_error_fail_open_when_configured():
    pol = _policy(defaults=Defaults(fail_safe_on_judge_error=False))
    engine = SupervisorEngine(pol, judge=_FailingJudge())
    d = await engine.decide("HUMAN_QUERY", {})
    assert d.action == "no_op"
    assert d.tier == 2


# ── Rate limit ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rate_limit_trips():
    now = [1000.0]

    def clock() -> float:
        return now[0]

    pol = _policy(defaults=Defaults(max_actions_per_minute=3))
    judge = _FixedJudge(JudgeVerdict(action="no_op", confidence=0.99, reason="ok"))
    engine = SupervisorEngine(pol, judge=judge, clock=clock)

    # 3 decisions within the same second
    for _ in range(3):
        d = await engine.decide("HUMAN_QUERY", {})
        assert d.tier == 1

    # 4th → rate limit
    d = await engine.decide("HUMAN_QUERY", {})
    assert d.action == "escalate_human"
    assert "rate limit" in d.reason.lower()


@pytest.mark.asyncio
async def test_rate_limit_window_slides():
    now = [1000.0]

    def clock() -> float:
        return now[0]

    pol = _policy(defaults=Defaults(max_actions_per_minute=2))
    judge = _FixedJudge(JudgeVerdict(action="no_op", confidence=0.99, reason="ok"))
    engine = SupervisorEngine(pol, judge=judge, clock=clock)

    await engine.decide("HUMAN_QUERY", {})
    await engine.decide("HUMAN_QUERY", {})
    # Advance > 60s
    now[0] += 61
    d = await engine.decide("HUMAN_QUERY", {})
    # Old timestamps evicted, room for new decision
    assert d.tier == 1


# ── Circuit breaker ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_circuit_breaker_aborts_after_consecutive_escalations():
    pol = _policy(defaults=Defaults(max_consecutive_escalations=2))
    judge = _FixedJudge(JudgeVerdict(action="auto_respond", confidence=0.3, reason="x"))
    engine = SupervisorEngine(pol, judge=judge)

    # Low confidence → escalate, increments counter
    d1 = await engine.decide("HUMAN_QUERY", {})
    assert d1.action == "escalate_human"
    d2 = await engine.decide("HUMAN_QUERY", {})
    # 2nd escalation hits the breaker → abort
    assert d2.action == "abort_pipeline"
    assert "circuit breaker" in d2.reason.lower()


@pytest.mark.asyncio
async def test_circuit_breaker_resets_on_non_escalation():
    pol = _policy(defaults=Defaults(max_consecutive_escalations=3))
    judge = _FixedJudge(JudgeVerdict(action="auto_respond", confidence=0.95, reason="ok"))
    engine = SupervisorEngine(pol, judge=judge)

    # Force escalation by switching to low-confidence judge mid-test
    judge.verdict = JudgeVerdict(action="auto_respond", confidence=0.3, reason="x")
    await engine.decide("HUMAN_QUERY", {})
    assert engine.consecutive_escalations == 1
    # Reset on auto
    judge.verdict = JudgeVerdict(action="auto_respond", confidence=0.95, reason="ok")
    await engine.decide("HUMAN_QUERY", {})
    assert engine.consecutive_escalations == 0


# ── Audit JSON shape ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_decision_carries_history_to_judge():
    seen: list[tuple[str, ...]] = []

    class _Spy:
        async def classify(self, input_: JudgeInput) -> JudgeVerdict:
            seen.append(input_.history)
            return JudgeVerdict(action="no_op", confidence=0.99, reason="r")

    engine = SupervisorEngine(_policy(), judge=_Spy())
    await engine.decide("HUMAN_QUERY", {})
    await engine.decide("HUMAN_QUERY", {})
    # Second call should see at least the first decision's reason as history
    assert len(seen[1]) >= 1
