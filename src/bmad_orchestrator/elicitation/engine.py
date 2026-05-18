"""ElicitationEngine — two-tier P2 Routing for worker elicitation events.

See spec/spec_auto_elicitation_engine.md §2.

Decision pipeline:
    Tier 0  Hard override     security/crypto/PII keywords → escalate
    Tier 1  Static rule match topics + keywords + file_patterns (first-match)
    Tier 2  LLM judge         pluggable callback (StubJudge by default)
    Window  Per-story counter > max_auto_resolve_per_story → force escalate
"""

from __future__ import annotations

import fnmatch
from collections import defaultdict
from typing import Any

from bmad_orchestrator.elicitation.llm_judge import (
    JudgeInput,
    LLMJudgeProtocol,
    StubJudge,
)
from bmad_orchestrator.elicitation.policy import (
    Decision,
    ElicitationPolicy,
    Rule,
)


class ElicitationEngine:
    """Routes a single elicitation event to auto_resolve / escalate."""

    def __init__(
        self,
        policy: ElicitationPolicy,
        judge: LLMJudgeProtocol | None = None,
    ) -> None:
        self.policy = policy
        self.judge: LLMJudgeProtocol = judge or StubJudge()
        # In-memory window counter; resets on orchestrator restart by design
        # (single missed cap event is acceptable, persistence not worth it).
        self._auto_resolve_counts: dict[str, int] = defaultdict(int)

    async def decide(self, event_payload: dict[str, Any]) -> Decision:
        """Classify one WORKER_ELICITATION event payload.

        Expected payload keys:
            question (str), topics (list[str]), file_patterns (list[str]),
            story_id (str | None), epic_id (int | None).
        """
        question: str = str(event_payload.get("question", ""))
        topics: list[str] = list(event_payload.get("topics") or [])
        file_patterns: list[str] = list(event_payload.get("file_patterns") or [])
        story_id: str | None = event_payload.get("story_id")
        epic_id_raw = event_payload.get("epic_id")
        epic_id: int | None = int(epic_id_raw) if isinstance(epic_id_raw, int) else None

        # Tier 0 — hard override (always wins, even before window check).
        hard = self._check_hard_override(question, topics)
        if hard is not None:
            return hard

        # Window cap — check BEFORE rule match so a low-risk rule cannot
        # auto-resolve once the per-story budget is exhausted.
        if story_id and self._auto_resolve_counts[story_id] >= self.policy.defaults.max_auto_resolve_per_story:
            return Decision(
                action="escalate",
                answer=None,
                reason=(
                    f"window cap reached: {self._auto_resolve_counts[story_id]} "
                    f"auto-resolves on story {story_id} "
                    f">= max {self.policy.defaults.max_auto_resolve_per_story}"
                ),
                tier="window",
                risk="medium",
                rule_id=None,
            )

        # Tier 1 — static rule match.
        rule_hit = self._match_rule(topics, question, file_patterns)
        if rule_hit is not None:
            decision = self._decision_from_rule(rule_hit)
            self._track(story_id, decision)
            return decision

        # Tier 2 — LLM judge fallback (or escalate, per defaults).
        if self.policy.defaults.unknown_topic_action == "escalate":
            return Decision(
                action="escalate",
                answer=None,
                reason="no rule matched, defaults.unknown_topic_action=escalate",
                tier=2,
                risk="medium",
                rule_id=None,
            )

        verdict = await self.judge.classify(
            JudgeInput(
                question=question,
                topics=tuple(topics),
                story_id=story_id,
                epic_id=epic_id,
            )
        )
        # Judge `high` risk always escalates regardless of suggested action —
        # defence-in-depth against a misconfigured judge.
        if verdict.risk == "high":
            action: str = "escalate"
            answer: str | None = None
        else:
            action = verdict.action
            answer = verdict.suggested_answer if action == "auto_resolve" else None
        decision = Decision(
            action=action,  # type: ignore[arg-type]
            answer=answer,
            reason=f"tier-2 judge: {verdict.reason}",
            tier=2,
            risk=verdict.risk,
            rule_id=None,
        )
        self._track(story_id, decision)
        return decision

    def _check_hard_override(self, question: str, topics: list[str]) -> Decision | None:
        haystack = (question + " " + " ".join(topics)).lower()
        for kw in self.policy.defaults.hard_escalate_keywords:
            if kw.lower() in haystack:
                return Decision(
                    action="escalate",
                    answer=None,
                    reason=f"hard-override keyword matched: {kw!r}",
                    tier=0,
                    risk="high",
                    rule_id=None,
                )
        return None

    def _match_rule(
        self, topics: list[str], question: str, file_patterns: list[str]
    ) -> Rule | None:
        topics_set = {t.lower() for t in topics}
        q_lower = question.lower()
        for rule in self.policy.rules:
            m = rule.match
            if m.topics and topics_set.intersection({t.lower() for t in m.topics}):
                return rule
            if m.keywords and any(kw.lower() in q_lower for kw in m.keywords):
                return rule
            if m.file_patterns and any(
                fnmatch.fnmatch(fp, pat) for fp in file_patterns for pat in m.file_patterns
            ):
                return rule
        return None

    @staticmethod
    def _decision_from_rule(rule: Rule) -> Decision:
        if rule.action == "auto_resolve":
            return Decision(
                action="auto_resolve",
                answer=rule.answer,
                reason=f"rule {rule.id}: {rule.reason}",
                tier=1,
                risk=rule.risk,
                rule_id=rule.id,
            )
        # conditional treated as escalate for now; full conditional logic is
        # a backlog item (needs predicate evaluator).
        return Decision(
            action="escalate",
            answer=None,
            reason=f"rule {rule.id}: {rule.reason}",
            tier=1,
            risk=rule.risk,
            rule_id=rule.id,
        )

    def _track(self, story_id: str | None, decision: Decision) -> None:
        if story_id and decision.action == "auto_resolve":
            self._auto_resolve_counts[story_id] += 1

    def auto_resolve_count(self, story_id: str) -> int:
        """Test/diagnostic helper — current per-story counter value."""
        return self._auto_resolve_counts.get(story_id, 0)
