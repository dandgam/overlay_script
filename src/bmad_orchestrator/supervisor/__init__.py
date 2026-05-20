"""Supervisor LLM-loop — autonomous meta-orchestrator (Phase 4 item #9).

See spec/spec_supervisor_llm_loop.md.

Decision pipeline (P2 Routing + P4 Orchestrator-Workers):
    Event (one of 5 types) → SupervisorEngine.decide() → SupervisorDecision
        Tier 0  Hard rules (deterministic policy YAML)
        Tier 1  LLM judge (Sonnet 4.6, stub by default)
        Tier 2  Real human escalation (Telegram + TUI banner)
    → actions.execute() → tool calls / bus emit
    → audit.log_decision() → control.events.jsonl

Public API:
    SupervisorEngine     — main router
    SupervisorPolicy     — pydantic YAML schema
    SupervisorDecision   — engine output
    load_policy          — YAML loader
    log_decision         — audit writer
"""

from __future__ import annotations

from bmad_orchestrator.supervisor.audit import log_decision
from bmad_orchestrator.supervisor.engine import SupervisorEngine
from bmad_orchestrator.supervisor.judges import AnthropicJudge
from bmad_orchestrator.supervisor.llm_judge import (
    JudgeError,
    JudgeInput,
    JudgeVerdict,
    LLMJudgeProtocol,
    StubJudge,
)
from bmad_orchestrator.supervisor.policy import (
    HardRule,
    HardRuleMatch,
    SupervisorAction,
    SupervisorDecision,
    SupervisorPolicy,
    load_policy,
)

__all__ = [
    "AnthropicJudge",
    "HardRule",
    "HardRuleMatch",
    "JudgeError",
    "JudgeInput",
    "JudgeVerdict",
    "LLMJudgeProtocol",
    "StubJudge",
    "SupervisorAction",
    "SupervisorDecision",
    "SupervisorEngine",
    "SupervisorPolicy",
    "load_policy",
    "log_decision",
]
