"""Pydantic schemas + YAML loader for Supervisor policy.

See spec/spec_supervisor_llm_loop.md §3.4.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

SupervisorAction = Literal[
    "auto_respond",
    "pause_workers",
    "abort_pipeline",
    "escalate_human",
    "no_op",
    # Initiative pilot_findings_closure S4 (#4 R1): cancel a single stuck
    # worker via per-worker token (faster than ``pause_workers`` which targets
    # the whole pool). Requires ``tool_calls=[{name="cancel_worker", args={
    # worker_id: ..., reason: ...}}]`` so the actions module knows which
    # registry entry to trip.
    "cancel_worker",
]

WatchedEventType = Literal[
    "HUMAN_QUERY",
    "WORKER_HALT_FILE",
    "BUDGET_THRESHOLD_HIT",
    "WORKER_SILENT_FAILURE",
    "COMPLIANCE_SWEEP_NEEDED",
    # NEW-33.3 — cumulative stuck-worker timeout from runtime.stuck_watchdog.
    "WORKER_STUCK_TIMEOUT",
]


class PolicyError(Exception):
    """Base for supervisor-policy loading errors."""


class PolicyNotFoundError(PolicyError):
    pass


class PolicyValidationError(PolicyError):
    pass


class ToolCall(BaseModel):
    """A single tool invocation requested by a Supervisor decision."""

    model_config = ConfigDict(extra="forbid")

    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class HardRuleMatch(BaseModel):
    """Conditions for a Tier 0 deterministic rule."""

    model_config = ConfigDict(extra="forbid")

    event: WatchedEventType
    # Optional numeric predicate (e.g. budget ratio ≥ threshold).
    ratio_gte: float | None = None
    # Optional substring match on payload['reason'] (case-insensitive).
    reason_contains: str | None = None


class HardRule(BaseModel):
    """One deterministic Tier 0 rule."""

    model_config = ConfigDict(extra="forbid")

    id: str
    when: HardRuleMatch
    action: SupervisorAction
    tool_calls: list[ToolCall] = Field(default_factory=list)
    reason: str = ""


JudgeProvider = Annotated[
    Literal["anthropic", "claude_p", "gemini", "openai", "yandex", "ollama"],
    "LLM provider for Tier 1 judge. 'claude_p' = subscription CLI (primary); 'anthropic' = SDK (needs API key).",
]


class JudgeConfig(BaseModel):
    """Settings for Tier 1 LLM-judge."""

    model_config = ConfigDict(extra="forbid")

    # M4-followup: 'claude_p' = subscription mode via ``claude -p`` CLI
    # (default — production env has no ANTHROPIC_API_KEY).
    # 'anthropic' = AsyncAnthropic SDK (needs ANTHROPIC_API_KEY).
    # Others planned: gemini, openai, yandex, ollama.
    provider: JudgeProvider = "claude_p"
    model: str = "claude-sonnet-4-6"
    timeout_seconds: float = Field(default=30.0, gt=0)
    system_prompt: str = (
        "You are a pipeline supervisor for a BMad orchestrator. "
        "Classify each event and output a JSON decision."
    )


class Defaults(BaseModel):
    """Engine-level guard rails."""

    model_config = ConfigDict(extra="forbid")

    confidence_auto_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    confidence_escalate_floor: float = Field(default=0.60, ge=0.0, le=1.0)
    max_actions_per_minute: int = Field(default=10, ge=1)
    max_consecutive_escalations: int = Field(default=3, ge=1)
    fail_safe_on_judge_error: bool = True


class SupervisorPolicy(BaseModel):
    """Root YAML schema."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    defaults: Defaults = Field(default_factory=Defaults)
    judge: JudgeConfig = Field(default_factory=JudgeConfig)
    hard_rules: list[HardRule] = Field(default_factory=list)


class SupervisorDecision(BaseModel):
    """Engine output — what Supervisor wants to happen for this event."""

    model_config = ConfigDict(extra="forbid")

    action: SupervisorAction
    tool_calls: list[ToolCall] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    tier: Literal[0, 1, 2]
    rule_id: str | None = None
    escalation_text: str | None = None


def load_policy(path: str | Path) -> SupervisorPolicy:
    """Load + validate Supervisor policy YAML."""
    p = Path(path)
    if not p.exists():
        raise PolicyNotFoundError(f"supervisor policy file not found: {p}")
    try:
        raw: Any = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PolicyValidationError(f"YAML parse error in {p}: {exc}") from exc
    if not isinstance(raw, dict):
        raise PolicyValidationError(
            f"supervisor policy root must be a mapping, got {type(raw).__name__}"
        )
    try:
        return SupervisorPolicy.model_validate(raw)
    except ValidationError as exc:
        raise PolicyValidationError(f"supervisor policy schema invalid: {exc}") from exc
