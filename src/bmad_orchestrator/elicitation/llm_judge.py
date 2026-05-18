"""Tier 2: LLM-judge for elicitation classification.

See spec/spec_auto_elicitation_engine.md §2.

Real Haiku integration deferred to Phase 4 (Supervisor LLM-loop). This module
exposes a pluggable callback so the engine can be wired and tested today; the
callback is mocked in tests, and will be replaced with anthropic.AsyncAnthropic
once we add the API key path.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol

JudgeRisk = Literal["low", "medium", "high"]
JudgeAction = Literal["auto_resolve", "escalate"]


@dataclass(frozen=True)
class JudgeInput:
    """What the engine hands to the judge."""

    question: str
    topics: tuple[str, ...]
    story_id: str | None
    epic_id: int | None


@dataclass(frozen=True)
class JudgeVerdict:
    """What the judge returns to the engine."""

    risk: JudgeRisk
    action: JudgeAction
    suggested_answer: str | None
    reason: str


class LLMJudgeProtocol(Protocol):
    """Pluggable callback interface for Tier 2 classifier."""

    async def classify(self, input_: JudgeInput) -> JudgeVerdict: ...


JudgeCallable = Callable[[JudgeInput], Awaitable[JudgeVerdict]]


class StubJudge:
    """Default judge: conservative, escalates everything.

    Production swap-in arrives with Supervisor LLM-loop (Phase 4). Until then we
    keep the engine wired through a real interface but with safe defaults so no
    unknown topic gets auto-resolved by accident.
    """

    async def classify(self, input_: JudgeInput) -> JudgeVerdict:
        return JudgeVerdict(
            risk="medium",
            action="escalate",
            suggested_answer=None,
            reason="stub-judge: real Haiku classifier deferred to Phase 4",
        )


__all__ = [
    "JudgeAction",
    "JudgeCallable",
    "JudgeInput",
    "JudgeRisk",
    "JudgeVerdict",
    "LLMJudgeProtocol",
    "StubJudge",
]
