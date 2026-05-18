"""Autofix model routing — Initiative pilot_findings_closure S3 (#3 rycag 1).

The bmad-auto-dev runner (`skills/upstream/bmad-auto-dev/scripts/bmad-auto-dev-runner.sh`)
hard-coded `claude --model sonnet -p` for Stage 6 auto-fix. On security-critical
stories or after a first auto-fix attempt that still left findings, Sonnet's
limited context + 300-LOC diff cap routinely triggered halt. Routing rules:

  * Story carries the ``security-critical`` tag (or matches policy override)  → opus
  * ``review_iteration >= opus_min_iteration`` (default 2) — i.e. retry #2+   → opus
  * Otherwise                                                                  → sonnet

ENV ``BMAD_AUTOFIX_MODEL`` (set by orchestrator before invoking runner.sh) is
the highest precedence: it overrides policy entirely. Used for tests and
manual overrides.

Wiring: runner.sh calls ``python3 -m bmad_orchestrator.runtime.autofix_routing
--story-file <path> --iteration <n>`` and uses the printed model string with
``claude --model <model> -p ...``. On any error the CLI prints the policy's
default_model so the runner never breaks.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from bmad_orchestrator.skills_repo import PolicyInvalidError, PolicyNotFoundError

AUTOFIX_ROUTING_POLICY_PATH_DEFAULT = (
    Path(__file__).resolve().parents[3] / "skills" / "policy" / "autofix-routing.yaml"
)

ENV_AUTOFIX_MODEL_OVERRIDE = "BMAD_AUTOFIX_MODEL"

AutofixModel = Literal["sonnet", "opus"]


class AutofixRoutingPolicy(BaseModel):
    """Schema for ``skills/policy/autofix-routing.yaml``."""

    enabled: bool = True
    security_critical_tag: str = Field(default="security-critical")
    opus_min_iteration: int = Field(default=2, ge=1)
    default_model: AutofixModel = "sonnet"
    opus_model: str = "opus"
    sonnet_model: str = "sonnet"

    def cli_model_for(self, choice: AutofixModel) -> str:
        return self.opus_model if choice == "opus" else self.sonnet_model


def load_autofix_routing_policy(
    path: Path | None = None,
) -> AutofixRoutingPolicy:
    """Load + validate the autofix-routing policy yaml.

    Raises:
        PolicyNotFoundError — file missing.
        PolicyInvalidError  — YAML parse OR schema validation fail.
    """
    p = path or AUTOFIX_ROUTING_POLICY_PATH_DEFAULT
    if not p.exists():
        raise PolicyNotFoundError(f"autofix-routing policy not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise PolicyInvalidError(f"YAML parse error in {p}: {e}") from e
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise PolicyInvalidError(
            f"top-level structure must be mapping in {p}, got {type(raw).__name__}"
        )
    try:
        return AutofixRoutingPolicy.model_validate(raw)
    except ValidationError as e:
        raise PolicyInvalidError(
            f"autofix-routing schema validation failed: {e}"
        ) from e


def _story_tags(story: Mapping[str, Any]) -> list[str]:
    """Return story tags as a list of lowercase strings (tolerant of shapes)."""
    raw = story.get("tags")
    if raw is None:
        return []
    if isinstance(raw, str):
        # CSV or single token
        items: Iterable[str] = (s.strip() for s in raw.split(","))
    elif isinstance(raw, (list, tuple, set, frozenset)):
        items = (str(s) for s in raw)
    else:
        return []
    return [s.lower() for s in items if s]


def pick_autofix_model(
    story: Mapping[str, Any] | None,
    review_iteration: int,
    *,
    policy: AutofixRoutingPolicy | None = None,
    env: Mapping[str, str] | None = None,
) -> AutofixModel:
    """Resolve the autofix model for a single Stage-6-retry invocation.

    Order of precedence:
      1. ``BMAD_AUTOFIX_MODEL`` env override (case-insensitive sonnet|opus).
      2. Policy disabled → ``default_model``.
      3. Story tag matches ``policy.security_critical_tag`` → opus.
      4. ``review_iteration >= policy.opus_min_iteration`` → opus.
      5. ``default_model``.

    ``review_iteration`` is 1-based (first auto-fix attempt = 1). Values < 1
    are clamped to 1.
    """
    env_map = env if env is not None else os.environ
    override = env_map.get(ENV_AUTOFIX_MODEL_OVERRIDE, "").strip().lower()
    if override in ("sonnet", "opus"):
        return override  # type: ignore[return-value]

    pol = policy or AutofixRoutingPolicy()

    if not pol.enabled:
        return pol.default_model

    iteration = max(1, int(review_iteration))

    tags = _story_tags(story or {})
    if pol.security_critical_tag.lower() in tags:
        return "opus"

    if iteration >= pol.opus_min_iteration:
        return "opus"

    return pol.default_model


def _cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bmad_orchestrator.runtime.autofix_routing",
        description="Pick autofix model (sonnet|opus) per routing policy.",
    )
    parser.add_argument(
        "--story-file",
        type=Path,
        help="Path to story markdown. Frontmatter parsed for tags.",
    )
    parser.add_argument(
        "--iteration",
        type=int,
        default=1,
        help="Review iteration (1-based). Default 1.",
    )
    parser.add_argument(
        "--policy-file",
        type=Path,
        default=None,
        help="Path to policy yaml. Default = skills/policy/autofix-routing.yaml.",
    )
    parser.add_argument(
        "--print-cli-name",
        action="store_true",
        help="Print policy.cli_model_for(<choice>) instead of the canonical "
        "internal name. Use this in runner.sh: passes correct --model arg.",
    )
    args = parser.parse_args(argv)

    story: dict[str, Any] = {}
    if args.story_file and args.story_file.exists():
        try:
            from bmad_orchestrator.agent.tools._common import parse_story_md

            story = parse_story_md(args.story_file.read_text(encoding="utf-8"))
        except Exception:
            story = {}

    try:
        policy = load_autofix_routing_policy(args.policy_file)
    except (PolicyNotFoundError, PolicyInvalidError):
        policy = AutofixRoutingPolicy()

    choice = pick_autofix_model(story, args.iteration, policy=policy)
    if args.print_cli_name:
        sys.stdout.write(policy.cli_model_for(choice))
    else:
        sys.stdout.write(choice)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - thin CLI wrapper
    raise SystemExit(_cli_main())


__all__ = [
    "AUTOFIX_ROUTING_POLICY_PATH_DEFAULT",
    "ENV_AUTOFIX_MODEL_OVERRIDE",
    "AutofixModel",
    "AutofixRoutingPolicy",
    "load_autofix_routing_policy",
    "pick_autofix_model",
]
