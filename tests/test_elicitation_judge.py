"""Tests for Tier 2 LLM-judge contract + StubJudge default."""

from __future__ import annotations

import pytest

from bmad_orchestrator.elicitation.llm_judge import (
    JudgeInput,
    JudgeVerdict,
    StubJudge,
)


@pytest.mark.asyncio
async def test_stub_judge_always_escalates():
    judge = StubJudge()
    verdict = await judge.classify(
        JudgeInput(question="random question", topics=("unknown",), story_id="s1", epic_id=1)
    )
    assert verdict.action == "escalate"
    assert verdict.risk == "medium"
    assert verdict.suggested_answer is None
    assert "deferred" in verdict.reason.lower()


def test_judge_input_immutable():
    inp = JudgeInput(question="q", topics=("a",), story_id=None, epic_id=None)
    with pytest.raises(Exception):
        inp.question = "changed"  # type: ignore[misc]


def test_judge_verdict_immutable():
    v = JudgeVerdict(risk="low", action="auto_resolve", suggested_answer="ok", reason="r")
    with pytest.raises(Exception):
        v.risk = "high"  # type: ignore[misc]


@pytest.mark.asyncio
async def test_custom_judge_implements_protocol():
    """A user-supplied callable should satisfy LLMJudgeProtocol via duck-typing."""

    class CustomJudge:
        async def classify(self, input_: JudgeInput) -> JudgeVerdict:
            return JudgeVerdict(
                risk="low",
                action="auto_resolve",
                suggested_answer="use snake_case",
                reason="naming convention",
            )

    judge = CustomJudge()
    verdict = await judge.classify(
        JudgeInput(question="name?", topics=("naming",), story_id="s1", epic_id=None)
    )
    assert verdict.action == "auto_resolve"
    assert verdict.suggested_answer == "use snake_case"
