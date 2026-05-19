"""SupervisorEngine — Tier 0/1/2 router for orchestrator-level events.

See spec/spec_supervisor_llm_loop.md §3.

Pipeline:
    Event → Tier 0 hard rule match → if hit → Decision
    no hit → Tier 1 LLM judge   → if confidence >= auto_threshold → auto_respond
                                    if confidence >= escalate_floor → escalate (with suggestion)
                                    else                            → escalate (no suggestion)
    LLM error and fail_safe_on_judge_error → Tier 2 escalate_human
    Rate-limit / circuit-breaker can override above → escalate / abort_pipeline
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from typing import Any

from bmad_orchestrator.supervisor.llm_judge import (
    JudgeError,
    JudgeInput,
    JudgeVerdict,
    LLMJudgeProtocol,
    StubJudge,
)
from bmad_orchestrator.supervisor.policy import (
    HardRule,
    SupervisorDecision,
    SupervisorPolicy,
    ToolCall,
)


class SupervisorEngine:
    """Decide one event at a time."""

    # Cap on stored timestamps for the rate-limit window — keeps memory bounded
    # even if max_actions_per_minute is set to a huge number.
    _MAX_TIMESTAMP_BUFFER: int = 1000

    def __init__(
        self,
        policy: SupervisorPolicy,
        judge: LLMJudgeProtocol | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.policy = policy
        self.judge: LLMJudgeProtocol = judge or StubJudge()
        self._clock = clock
        self._action_timestamps: deque[float] = deque(maxlen=self._MAX_TIMESTAMP_BUFFER)
        self._consecutive_escalations: int = 0
        self._history: deque[str] = deque(maxlen=10)

    async def decide(self, event_type: str, payload: dict[str, Any]) -> SupervisorDecision:
        # NEW-13: a HUMAN_QUERY raised because security_review yielded
        # verdict=error is a *technical* failure of the review step, not a
        # genuine pipeline escalation. It must not push the circuit breaker
        # toward abort_pipeline. Detect it once here and skip the
        # consecutive-escalation increment for every _track call this wake.
        count_escalation = not self._is_security_review_error(event_type, payload)

        # Rate limit fires BEFORE any decision logic — overload protection.
        if self._rate_limit_tripped():
            return self._make_decision(
                action="escalate_human",
                confidence=1.0,
                reason="rate limit exceeded — escalating until backlog drains",
                tier=2,
            )

        # Tier 0 — deterministic
        rule_hit = self._match_hard_rule(event_type, payload)
        if rule_hit is not None:
            decision = self._decision_from_rule(rule_hit)
            self._track(decision, count_escalation=count_escalation)
            return decision

        # Tier 1 — LLM judge
        try:
            verdict = await self.judge.classify(
                JudgeInput(
                    event_type=event_type,
                    payload=dict(payload),
                    history=tuple(self._history),
                )
            )
        except JudgeError as exc:
            decision = (
                self._make_decision(
                    action="escalate_human",
                    confidence=0.0,
                    reason=f"judge error → fail-safe escalate: {exc}",
                    tier=2,
                )
                if self.policy.defaults.fail_safe_on_judge_error
                else self._make_decision(
                    action="no_op",
                    confidence=0.0,
                    reason=f"judge error → fail-open no_op: {exc}",
                    tier=2,
                )
            )
            self._track(decision, count_escalation=count_escalation)
            return decision

        decision = self._decision_from_verdict(verdict)
        self._track(decision, count_escalation=count_escalation)

        # Circuit breaker — too many escalations in a row → abort
        if self._consecutive_escalations >= self.policy.defaults.max_consecutive_escalations:
            return self._make_decision(
                action="abort_pipeline",
                confidence=1.0,
                reason=(
                    f"circuit breaker: {self._consecutive_escalations} consecutive "
                    f"escalations exceeds cap {self.policy.defaults.max_consecutive_escalations}"
                ),
                tier=2,
            )
        return decision

    # ── Tier 0 helpers ─────────────────────────────────────────────────────

    def _match_hard_rule(self, event_type: str, payload: dict[str, Any]) -> HardRule | None:
        for rule in self.policy.hard_rules:
            if rule.when.event != event_type:
                continue
            if rule.when.ratio_gte is not None:
                ratio = payload.get("ratio")
                if not isinstance(ratio, int | float) or ratio < rule.when.ratio_gte:
                    continue
            if rule.when.reason_contains is not None:
                reason = str(payload.get("reason", ""))
                if rule.when.reason_contains.lower() not in reason.lower():
                    continue
            return rule
        return None

    def _decision_from_rule(self, rule: HardRule) -> SupervisorDecision:
        return SupervisorDecision(
            action=rule.action,
            tool_calls=list(rule.tool_calls),
            confidence=1.0,
            reason=f"rule {rule.id}: {rule.reason}",
            tier=0,
            rule_id=rule.id,
        )

    # ── Tier 1 helpers ─────────────────────────────────────────────────────

    def _decision_from_verdict(self, verdict: JudgeVerdict) -> SupervisorDecision:
        d = self.policy.defaults
        # Defence-in-depth: even if judge says auto_respond, low confidence
        # forces escalate.
        if verdict.confidence < d.confidence_escalate_floor:
            return self._make_decision(
                action="escalate_human",
                confidence=verdict.confidence,
                reason=(
                    f"judge confidence {verdict.confidence:.2f} < "
                    f"{d.confidence_escalate_floor:.2f} — escalating"
                ),
                tier=1,
                escalation_text=verdict.reason,
            )
        if verdict.confidence < d.confidence_auto_threshold:
            return self._make_decision(
                action="escalate_human",
                confidence=verdict.confidence,
                reason=(
                    f"judge suggested {verdict.action} at {verdict.confidence:.2f}, "
                    f"below auto threshold {d.confidence_auto_threshold:.2f}"
                ),
                tier=1,
                escalation_text=verdict.reason,
                tool_calls=list(verdict.tool_calls),
            )
        return self._make_decision(
            action=verdict.action,
            confidence=verdict.confidence,
            reason=f"judge: {verdict.reason}",
            tier=1,
            tool_calls=list(verdict.tool_calls),
        )

    # ── Rate limit + circuit breaker ───────────────────────────────────────

    def _rate_limit_tripped(self) -> bool:
        now = self._clock()
        window_start = now - 60.0
        # Drop timestamps older than 60s — bounded deque means in worst case we
        # walk MAX_TIMESTAMP_BUFFER elements.
        while self._action_timestamps and self._action_timestamps[0] < window_start:
            self._action_timestamps.popleft()
        return len(self._action_timestamps) >= self.policy.defaults.max_actions_per_minute

    @staticmethod
    def _is_security_review_error(event_type: str, payload: dict[str, Any]) -> bool:
        """True when this event is a HUMAN_QUERY raised by a security_review
        technical error (NEW-13). Such events must not feed the circuit
        breaker's consecutive-escalation counter — a failed review step is not
        a story escalation."""
        if event_type.upper() != "HUMAN_QUERY":
            return False
        return (
            payload.get("security_verdict") == "error"
            or payload.get("verdict") == "security_review_error"
        )

    def _track(
        self, decision: SupervisorDecision, *, count_escalation: bool = True
    ) -> None:
        self._action_timestamps.append(self._clock())
        self._history.append(decision.reason[:80])
        if decision.action == "escalate_human":
            # NEW-13: when count_escalation is False (security_review error)
            # leave the counter untouched — neither increment nor reset, so a
            # technical failure neither advances nor masks the circuit breaker.
            if count_escalation:
                self._consecutive_escalations += 1
        else:
            self._consecutive_escalations = 0

    # ── Construction helper ────────────────────────────────────────────────

    @staticmethod
    def _make_decision(
        *,
        action: str,
        confidence: float,
        reason: str,
        tier: int,
        rule_id: str | None = None,
        escalation_text: str | None = None,
        tool_calls: list[ToolCall] | None = None,
    ) -> SupervisorDecision:
        return SupervisorDecision(
            action=action,  # type: ignore[arg-type]
            confidence=confidence,
            reason=reason,
            tier=tier,  # type: ignore[arg-type]
            rule_id=rule_id,
            escalation_text=escalation_text,
            tool_calls=tool_calls or [],
        )

    # ── Public diagnostic helpers ──────────────────────────────────────────

    @property
    def consecutive_escalations(self) -> int:
        return self._consecutive_escalations

    @property
    def recent_action_count(self) -> int:
        return len(self._action_timestamps)
