"""Tests for ElicitationEngine — Tier 0/1/2 + window cap."""

from __future__ import annotations

from pathlib import Path

import pytest

from bmad_orchestrator.elicitation.engine import ElicitationEngine
from bmad_orchestrator.elicitation.llm_judge import (
    JudgeInput,
    JudgeVerdict,
)
from bmad_orchestrator.elicitation.policy import (
    Defaults,
    ElicitationPolicy,
    Rule,
    RuleMatch,
    load_policy,
)

EXAMPLE_YAML = Path(__file__).resolve().parent.parent / "examples" / "elicitation-policy.example.yaml"


class _FixedJudge:
    """Test judge returning a pre-configured verdict."""

    def __init__(self, verdict: JudgeVerdict) -> None:
        self.verdict = verdict
        self.call_count = 0
        self.last_input: JudgeInput | None = None

    async def classify(self, input_: JudgeInput) -> JudgeVerdict:
        self.call_count += 1
        self.last_input = input_
        return self.verdict


def _policy(rules: list[Rule] | None = None, defaults: Defaults | None = None) -> ElicitationPolicy:
    return ElicitationPolicy(
        version=1,
        defaults=defaults or Defaults(),
        rules=rules or [],
    )


# ── Tier 0 — hard override ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hard_override_security_keyword_in_question():
    engine = ElicitationEngine(_policy())
    decision = await engine.decide(
        {"question": "which crypto library should I use?", "topics": [], "story_id": "s1"}
    )
    assert decision.action == "escalate"
    assert decision.tier == 0
    assert decision.risk == "high"
    assert "crypto" in decision.reason.lower()


@pytest.mark.asyncio
async def test_hard_override_topic_pii():
    engine = ElicitationEngine(_policy())
    decision = await engine.decide(
        {"question": "where to log this?", "topics": ["pii", "redaction"], "story_id": "s1"}
    )
    assert decision.action == "escalate"
    assert decision.tier == 0


@pytest.mark.asyncio
async def test_hard_override_destructive_keyword():
    engine = ElicitationEngine(_policy())
    decision = await engine.decide(
        {"question": "should I drop table users?", "topics": [], "story_id": "s1"}
    )
    assert decision.action == "escalate"
    assert decision.tier == 0


# ── Tier 1 — static rule match ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rule_match_topics_auto_resolve():
    rule = Rule(
        id="naming-snake",
        match=RuleMatch(topics=["naming", "style"]),
        risk="low",
        action="auto_resolve",
        default_answer="use snake_case",
        reason="house style",
    )
    engine = ElicitationEngine(_policy(rules=[rule]))
    decision = await engine.decide(
        {"question": "var name?", "topics": ["naming"], "story_id": "s1"}
    )
    assert decision.action == "auto_resolve"
    assert decision.tier == 1
    assert decision.answer == "use snake_case"
    assert decision.rule_id == "naming-snake"


@pytest.mark.asyncio
async def test_rule_match_keyword_escalate():
    rule = Rule(
        id="migration-schema",
        match=RuleMatch(keywords=["migration"]),
        risk="high",
        action="escalate",
        reason="downtime risk",
    )
    engine = ElicitationEngine(_policy(rules=[rule]))
    decision = await engine.decide(
        {"question": "how to run migration safely?", "topics": [], "story_id": "s1"}
    )
    assert decision.action == "escalate"
    assert decision.tier == 1
    assert decision.rule_id == "migration-schema"


@pytest.mark.asyncio
async def test_rule_match_file_pattern():
    rule = Rule(
        id="tests-placement",
        match=RuleMatch(file_patterns=["tests/**/*.py"]),
        risk="low",
        action="auto_resolve",
        default_answer="put under tests/<feature>/",
        reason="layout",
    )
    engine = ElicitationEngine(_policy(rules=[rule]))
    decision = await engine.decide(
        {
            "question": "where for test?",
            "topics": [],
            "file_patterns": ["tests/foo/test_bar.py"],
            "story_id": "s1",
        }
    )
    assert decision.action == "auto_resolve"
    assert decision.tier == 1


# ── Tier 2 — LLM judge ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_topic_judge_low_auto_resolve():
    judge = _FixedJudge(
        JudgeVerdict(
            risk="low", action="auto_resolve", suggested_answer="use Path()", reason="stdlib pref"
        )
    )
    engine = ElicitationEngine(_policy(), judge=judge)
    decision = await engine.decide(
        {"question": "Path or os.path?", "topics": ["stdlib"], "story_id": "s1"}
    )
    assert judge.call_count == 1
    assert decision.action == "auto_resolve"
    assert decision.tier == 2
    assert decision.answer == "use Path()"


@pytest.mark.asyncio
async def test_unknown_topic_judge_high_forces_escalate():
    """Even if judge says auto_resolve, risk=high must escalate (defence-in-depth)."""
    judge = _FixedJudge(
        JudgeVerdict(
            risk="high",
            action="auto_resolve",  # judge contradicts itself; engine must override
            suggested_answer="dangerous",
            reason="misjudgement",
        )
    )
    engine = ElicitationEngine(_policy(), judge=judge)
    decision = await engine.decide(
        {"question": "use eval()?", "topics": ["exec"], "story_id": "s1"}
    )
    assert decision.action == "escalate"
    assert decision.tier == 2
    assert decision.answer is None


@pytest.mark.asyncio
async def test_unknown_topic_defaults_escalate_skips_judge():
    judge = _FixedJudge(
        JudgeVerdict(risk="low", action="auto_resolve", suggested_answer="x", reason="r")
    )
    policy = _policy(defaults=Defaults(unknown_topic_action="escalate"))
    engine = ElicitationEngine(policy, judge=judge)
    decision = await engine.decide(
        {"question": "anything?", "topics": ["unmapped"], "story_id": "s1"}
    )
    assert decision.action == "escalate"
    assert decision.tier == 2
    assert judge.call_count == 0


# ── Window cap ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_window_cap_after_max_auto_resolves():
    rule = Rule(
        id="low-risk",
        match=RuleMatch(topics=["style"]),
        risk="low",
        action="auto_resolve",
        default_answer="ok",
        reason="r",
    )
    policy = _policy(rules=[rule], defaults=Defaults(max_auto_resolve_per_story=3))
    engine = ElicitationEngine(policy)

    # 3 auto-resolves allowed
    for _ in range(3):
        d = await engine.decide({"question": "?", "topics": ["style"], "story_id": "s1"})
        assert d.action == "auto_resolve"
    assert engine.auto_resolve_count("s1") == 3

    # 4th forces escalate via window cap
    d = await engine.decide({"question": "?", "topics": ["style"], "story_id": "s1"})
    assert d.action == "escalate"
    assert d.tier == "window"


@pytest.mark.asyncio
async def test_window_cap_per_story_isolation():
    rule = Rule(
        id="low-risk",
        match=RuleMatch(topics=["style"]),
        risk="low",
        action="auto_resolve",
        default_answer="ok",
        reason="r",
    )
    policy = _policy(rules=[rule], defaults=Defaults(max_auto_resolve_per_story=2))
    engine = ElicitationEngine(policy)

    for _ in range(2):
        await engine.decide({"question": "?", "topics": ["style"], "story_id": "s1"})
    # s1 capped, s2 fresh
    d_other = await engine.decide({"question": "?", "topics": ["style"], "story_id": "s2"})
    assert d_other.action == "auto_resolve"
    d_capped = await engine.decide({"question": "?", "topics": ["style"], "story_id": "s1"})
    assert d_capped.action == "escalate"
    assert d_capped.tier == "window"


# ── Integration via example YAML ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_example_yaml_security_routes_to_escalate():
    policy = load_policy(EXAMPLE_YAML)
    engine = ElicitationEngine(policy)
    decision = await engine.decide(
        {"question": "JWT refresh flow?", "topics": ["jwt", "refresh-token"], "story_id": "s1"}
    )
    # Hard override hits before rules — either way: escalate.
    assert decision.action == "escalate"


@pytest.mark.asyncio
async def test_priority_hard_override_beats_rules():
    """Hard override should fire even if a low-risk auto_resolve rule matches."""
    rule = Rule(
        id="naming",
        match=RuleMatch(topics=["pii"]),  # intentional clash: pii is hard-override
        risk="low",
        action="auto_resolve",
        default_answer="redact()",
        reason="convention",
    )
    engine = ElicitationEngine(_policy(rules=[rule]))
    decision = await engine.decide(
        {"question": "redact pii field?", "topics": ["pii"], "story_id": "s1"}
    )
    assert decision.tier == 0
    assert decision.action == "escalate"


@pytest.mark.asyncio
async def test_window_does_not_count_escalates():
    rule = Rule(
        id="high-risk",
        match=RuleMatch(topics=["arch"]),
        risk="high",
        action="escalate",
        reason="r",
    )
    policy = _policy(rules=[rule], defaults=Defaults(max_auto_resolve_per_story=2))
    engine = ElicitationEngine(policy)
    for _ in range(5):
        d = await engine.decide({"question": "?", "topics": ["arch"], "story_id": "s1"})
        assert d.action == "escalate"
        assert d.tier == 1
    assert engine.auto_resolve_count("s1") == 0
