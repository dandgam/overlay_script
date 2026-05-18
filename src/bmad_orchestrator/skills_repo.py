"""skills_repo — load/parse helpers для embedded skills overlay.

См. spec/spec_embed_phase45_with_selflearning.md §4 E2.

Принципы skills-as-data:

* `skills/upstream/<skill>/`              pristine копии (read-only с точки зрения dev)
* `skills/customize/<skill>.customize.toml`  per-skill overlay (parsed here → Customize)
* `skills/policy/*.yaml`                  gates/cost/retry (parsed here → PolicyConfig)
* `skills/patches/*.diff`                 code patches re-applied by E4
* `skills/lessons/<project>/...`          retrospective output, parsed by E8
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

SKILLS_ROOT_DEFAULT: Path = Path(__file__).resolve().parent.parent.parent / "skills"

EMBEDDED_SKILL_NAMES: frozenset[str] = frozenset(
    {
        "bmad-advanced-elicitation",
        "bmad-agent-dev",
        "bmad-auto-dev",
        "bmad-checkpoint-preview",
        "bmad-code-review",
        "bmad-correct-course",
        "bmad-create-story",
        "bmad-customize",
        "bmad-dev-story",
        "bmad-investigate",
        "bmad-quick-dev",
        "bmad-retrospective",
        "bmad-review-adversarial-general",
        "bmad-review-edge-case-hunter",
        "bmad-sprint-planning",
        "bmad-sprint-status",
    }
)


class SkillsRepoError(Exception):
    """Base for skills_repo errors."""


class CustomizeNotFoundError(SkillsRepoError):
    pass


class CustomizeInvalidError(SkillsRepoError):
    pass


class PolicyNotFoundError(SkillsRepoError):
    pass


class PolicyInvalidError(SkillsRepoError):
    pass


class Customize(BaseModel):
    """Per-skill overlay applied on top of upstream SKILL.md.

    Loaded from `skills/customize/<skill-name>.customize.toml`.
    Empty TOML file → all defaults (no-op overlay, upstream applies as-is).
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    description_override: str | None = None
    extra_triggers: list[str] = Field(default_factory=list)
    body_overlay: str | None = None
    variables: dict[str, str] = Field(default_factory=dict)


class CodeReviewGates(BaseModel):
    """Code-review gates config — file `policy/code-review-gates.yaml`."""

    model_config = ConfigDict(extra="forbid")

    p0_threshold: float = Field(0.8, ge=0.0, le=1.0)
    test_coverage_threshold: float = Field(0.5, ge=0.0, le=1.0)
    compliance_tags: list[str] = Field(default_factory=lambda: ["152-ФЗ", "187-ФЗ"])
    sweep_every_stories: int = Field(50, ge=1)
    # P5 Evaluator-Optimizer hard iteration cap. Each review→fix round inside
    # bmad-auto-dev increments ORCHESTRATOR_WORKER_REVIEW_ITERATION; the verdict
    # event carries it back. When the count exceeds this cap, _gate_iteration_cap
    # converts an otherwise-approve verdict into a human escalation — prevents
    # runaway loops on stories the worker can't fix on its own.
    max_review_iterations: int = Field(3, ge=1)


class CostTuning(BaseModel):
    """Cost tuning config — file `policy/cost-tuning.yaml`."""

    model_config = ConfigDict(extra="forbid")

    daily_cap_usd: float = Field(50.0, ge=0.0)
    story_reserve_usd: float = Field(0.5, ge=0.0)
    max_concurrent_workers: int = Field(4, ge=1)


class RetryPolicy(BaseModel):
    """Retry policy config — file `policy/retry-policy.yaml`."""

    model_config = ConfigDict(extra="forbid")

    max_retries: int = Field(3, ge=0)
    backoff_seconds: int = Field(30, ge=0)
    escalation_triggers: list[str] = Field(default_factory=list)


class PolicyConfig(BaseModel):
    """Aggregate of three policy YAML files."""

    model_config = ConfigDict(extra="forbid")

    code_review_gates: CodeReviewGates
    cost_tuning: CostTuning
    retry_policy: RetryPolicy


def _resolve_root(root: Path | None) -> Path:
    return root if root is not None else SKILLS_ROOT_DEFAULT


def customize_path(skill_name: str, root: Path | None = None) -> Path:
    """Return TOML overlay path for given skill (file may or may not exist)."""
    return _resolve_root(root) / "customize" / f"{skill_name}.customize.toml"


def load_customize(skill_name: str, root: Path | None = None) -> Customize:
    """Load + parse `customize/<skill>.customize.toml`.

    Raises:
        CustomizeNotFoundError — file missing.
        CustomizeInvalidError  — TOML parse fail OR schema validation fail.
    """
    p = customize_path(skill_name, root)
    if not p.exists():
        raise CustomizeNotFoundError(f"customize file not found: {p}")
    try:
        data: dict[str, Any] = tomllib.loads(p.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise CustomizeInvalidError(f"TOML parse error in {p}: {e}") from e
    try:
        return Customize.model_validate(data)
    except ValidationError as e:
        raise CustomizeInvalidError(f"schema validation failed for {p}: {e}") from e


def load_all_customize(root: Path | None = None) -> dict[str, Customize]:
    """Load all 14 canonical skill customize stubs.

    Loads each name from EMBEDDED_SKILL_NAMES; missing file = CustomizeNotFoundError.
    """
    return {name: load_customize(name, root) for name in EMBEDDED_SKILL_NAMES}


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise PolicyNotFoundError(f"policy file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise PolicyInvalidError(f"YAML parse error in {path}: {e}") from e
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PolicyInvalidError(
            f"top-level structure must be mapping in {path}, got {type(raw).__name__}"
        )
    return raw


def load_policy(root: Path | None = None) -> PolicyConfig:
    """Load + parse all three `policy/*.yaml` files into a PolicyConfig.

    Raises:
        PolicyNotFoundError — any of the three files missing.
        PolicyInvalidError  — YAML parse OR schema validation fail.
    """
    pol = _resolve_root(root) / "policy"
    gates_raw = _load_yaml(pol / "code-review-gates.yaml")
    cost_raw = _load_yaml(pol / "cost-tuning.yaml")
    retry_raw = _load_yaml(pol / "retry-policy.yaml")
    try:
        return PolicyConfig(
            code_review_gates=CodeReviewGates.model_validate(gates_raw),
            cost_tuning=CostTuning.model_validate(cost_raw),
            retry_policy=RetryPolicy.model_validate(retry_raw),
        )
    except ValidationError as e:
        raise PolicyInvalidError(f"policy schema validation failed: {e}") from e
