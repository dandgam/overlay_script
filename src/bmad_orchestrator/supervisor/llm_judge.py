"""Tier 1 LLM-judge for Supervisor decisions.

Real Anthropic Sonnet call deferred to M4 (behind `--supervisor-llm` flag).
For now StubJudge with pluggable callback: returns escalate_human by default
(fail-safe), allowing all of M2/M3 wiring to be exercised without burning
tokens. Tests inject FixedJudge instances.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from bmad_orchestrator.supervisor.policy import SupervisorAction, ToolCall


@dataclass(frozen=True)
class JudgeInput:
    """What the engine hands to the judge."""

    event_type: str
    payload: dict[str, Any]
    history: tuple[str, ...] = ()  # last N supervisor decisions (reasons)


@dataclass(frozen=True)
class JudgeVerdict:
    """What the judge returns to the engine."""

    action: SupervisorAction
    confidence: float
    reason: str
    tool_calls: tuple[ToolCall, ...] = ()


class JudgeError(Exception):
    """Raised on LLM-judge transport failure (timeout, network, parse)."""


class LLMJudgeProtocol(Protocol):
    async def classify(self, input_: JudgeInput) -> JudgeVerdict: ...


JudgeCallable = Callable[[JudgeInput], Awaitable[JudgeVerdict]]


class StubJudge:
    """Default judge — conservative, always escalates.

    Production swap-in arrives with M4 (`--supervisor-llm` flag enables real
    Sonnet call). Until then we keep the engine wired through a real interface
    but safe defaults so no unknown event auto-resolves by accident.
    """

    async def classify(self, input_: JudgeInput) -> JudgeVerdict:
        return JudgeVerdict(
            action="escalate_human",
            confidence=0.5,
            reason=(
                f"stub-judge: real Sonnet classifier deferred. Event "
                f"{input_.event_type!r} escalated by default."
            ),
        )


__all__ = [
    "JudgeCallable",
    "JudgeError",
    "JudgeInput",
    "JudgeVerdict",
    "LLMJudgeProtocol",
    "StubJudge",
]
