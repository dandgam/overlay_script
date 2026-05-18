"""Pydantic schemas + YAML loader for the self-learning consolidation loop.

See spec/spec_self_learning_loop.md §3.3.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class SelfLearningError(Exception):
    """Base for self-learning config errors."""


class PolicyNotFoundError(SelfLearningError):
    pass


class PolicyValidationError(SelfLearningError):
    pass


class SelfLearningDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_pattern_occurrences: int = Field(default=3, ge=1)
    auto_apply_max_risk: Literal["low"] = "low"
    measure_window_waves: int = Field(default=2, ge=1)
    regression_threshold_pct: float = Field(default=5.0, gt=0.0)
    extractor_model: str = "claude-sonnet-4-6"


class SelfLearningConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    enabled: bool = True
    defaults: SelfLearningDefaults = Field(default_factory=SelfLearningDefaults)
    # HARD GATE: these policy files are NEVER auto-touched (non-overrideable default set)
    excluded_policy_files: list[str] = Field(
        default_factory=lambda: ["security-review.yaml", "deletion-safety.yaml"]
    )
    # HARD GATE: policies bearing any of these compliance tags are NEVER auto-touched
    excluded_compliance_tags: list[str] = Field(
        default_factory=lambda: ["152-ФЗ", "187-ФЗ"]
    )


def load_config(path: str | Path) -> SelfLearningConfig:
    """Load + validate self-learning config YAML.

    Raises PolicyNotFoundError / PolicyValidationError on failure.
    """
    p = Path(path)
    if not p.exists():
        raise PolicyNotFoundError(f"self-learning config not found: {p}")
    try:
        raw: Any = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PolicyValidationError(f"YAML parse error in {p}: {exc}") from exc
    if not isinstance(raw, dict):
        raise PolicyValidationError(
            f"self-learning config root must be a mapping, got {type(raw).__name__}"
        )
    try:
        return SelfLearningConfig.model_validate(raw)
    except ValidationError as exc:
        raise PolicyValidationError(f"self-learning config schema invalid: {exc}") from exc


__all__ = [
    "PolicyNotFoundError",
    "PolicyValidationError",
    "SelfLearningConfig",
    "SelfLearningDefaults",
    "SelfLearningError",
    "load_config",
]
