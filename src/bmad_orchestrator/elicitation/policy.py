"""Pydantic schemas + YAML loader for elicitation policy.

See spec/spec_auto_elicitation_engine.md §4.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

# Hard-override keywords applied at Tier 0 — NEVER auto-resolve regardless of rules
# or LLM judge verdict. Conservative defaults; can be extended via policy YAML.
DEFAULT_HARD_ESCALATE_KEYWORDS: tuple[str, ...] = (
    "crypto",
    "encryption",
    "hashing",
    "kdf",
    "argon2",
    "bcrypt",
    "pii",
    "personal data",
    "personal-data",
    "gdpr",
    "152-фз",
    "152-fz",
    "187-фз",
    "187-fz",
    "drop table",
    "delete from",
    "rm -rf",
    "force push",
    "force-push",
)


class PolicyError(Exception):
    """Base for policy loading errors."""


class PolicyNotFoundError(PolicyError):
    pass


class PolicyValidationError(PolicyError):
    pass


class RuleMatch(BaseModel):
    """First-match-wins criteria for an elicitation rule."""

    model_config = ConfigDict(extra="forbid")

    topics: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    file_patterns: list[str] = Field(default_factory=list)


class Rule(BaseModel):
    """One elicitation routing rule.

    Fields ``default`` and ``default_answer`` are interchangeable; ``default``
    matches the example YAML wording, ``default_answer`` is used internally.
    Use :meth:`answer` to read the resolved value.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    match: RuleMatch
    risk: Literal["low", "medium", "high"]
    action: Literal["auto_resolve", "escalate", "conditional"]
    default_answer: str | None = None
    default: str | None = None
    # Conditional-rule extras (not evaluated yet — treated as escalate when
    # action="conditional"; full predicate evaluator is a backlog item).
    auto_if: dict[str, Any] | None = None
    else_action: Literal["escalate", "auto_resolve"] | None = None
    reason: str = ""

    @property
    def answer(self) -> str | None:
        return self.default_answer if self.default_answer is not None else self.default


class Defaults(BaseModel):
    """Fallback behaviour when no rule matches."""

    model_config = ConfigDict(extra="forbid")

    unknown_topic_action: Literal["escalate", "judge"] = "judge"
    max_auto_resolve_per_story: int = Field(default=5, ge=0)
    escalation_channel: str = "telegram"
    hard_escalate_keywords: list[str] = Field(
        default_factory=lambda: list(DEFAULT_HARD_ESCALATE_KEYWORDS)
    )


class ElicitationPolicy(BaseModel):
    """Root YAML schema."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    project: str | None = None
    defaults: Defaults = Field(default_factory=Defaults)
    rules: list[Rule] = Field(default_factory=list)
    channels: dict[str, Any] | None = None


class Decision(BaseModel):
    """Engine output — what to do with an elicitation event."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["auto_resolve", "escalate"]
    answer: str | None = None
    reason: str
    tier: Literal[0, 1, 2, "window"]
    risk: Literal["low", "medium", "high"]
    rule_id: str | None = None


def load_policy(path: str | Path) -> ElicitationPolicy:
    """Load + validate policy YAML. Raises PolicyError subclasses on failure."""
    p = Path(path)
    if not p.exists():
        raise PolicyNotFoundError(f"policy file not found: {p}")
    try:
        raw: Any = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PolicyValidationError(f"YAML parse error in {p}: {exc}") from exc
    if not isinstance(raw, dict):
        raise PolicyValidationError(f"policy root must be a mapping, got {type(raw).__name__}")
    try:
        return ElicitationPolicy.model_validate(raw)
    except ValidationError as exc:
        raise PolicyValidationError(f"policy schema invalid: {exc}") from exc
