"""Risk classifier for self-learning proposals.

Rules (spec §3.1 Step 5):
- low:    pure YAML policy delta + no compliance tag + not in excluded list
- medium: multi-file change OR touches gate thresholds (cost/timeout)
- high:   code change OR excluded policy file touched OR compliance tag present

Auto-apply is ONLY permitted for low-risk proposals (hard gate).

See spec/spec_self_learning_loop.md §3.1 Step 5.
"""

from __future__ import annotations

from typing import Literal

from bmad_orchestrator.self_learning.config import SelfLearningConfig
from bmad_orchestrator.self_learning.extractor import ProposedPattern

Risk = Literal["low", "medium", "high"]

# Fields that gate cost/timing thresholds — changes here are medium-risk.
_THRESHOLD_FIELDS: frozenset[str] = frozenset(
    {
        "story_alarm_usd",
        "story_halt_usd",
        "batch_alarm_usd",
        "batch_halt_usd",
        "daily_limit_usd",
        "max_retries",
        "timeout_seconds",
        "timeout_minutes",
    }
)

# Policy types that imply code changes — always high risk.
_CODE_CHANGE_INDICATORS: frozenset[str] = frozenset({"code", "script", "hook"})


def classify_risk(pattern: ProposedPattern, config: SelfLearningConfig) -> Risk:
    """Return risk level for ``pattern`` given ``config`` exclusion lists.

    Logic (first-match wins, most restrictive first):
    1. If policy_file in excluded_policy_files → high
    2. If policy_file is empty (unknown target) → high
    3. If policy_file name suggests code change → high
    4. If field touches gate thresholds AND occurrence_count < 5 → medium
    5. If source_lessons count > 1 policy file → medium (multi-file)
    6. Otherwise → low
    """
    excluded = set(config.excluded_policy_files)

    # Hard gate: excluded file
    if pattern.policy_file in excluded:
        return "high"

    # Unknown target
    if not pattern.policy_file:
        return "high"

    # Code-change indicator in policy file name
    if any(ind in pattern.policy_file.lower() for ind in _CODE_CHANGE_INDICATORS):
        return "high"

    # Gate threshold fields — medium if not overwhelmingly supported
    if pattern.field in _THRESHOLD_FIELDS:
        return "medium"

    # Multiple distinct policy files touched by source lessons (multi-file delta)
    # Detected by checking if occurrence_count suggests spread across many lessons
    # with potentially different targets. Conservative: flag as medium if many sources.
    if len(pattern.source_lessons) > 5:
        return "medium"

    return "low"


def is_auto_appliable(risk: Risk) -> bool:
    """True iff risk qualifies for auto-apply (hard gate: only 'low')."""
    return risk == "low"


__all__ = [
    "Risk",
    "classify_risk",
    "is_auto_appliable",
]
