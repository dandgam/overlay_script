"""Tests for self_learning.risk_classifier — M2."""

from __future__ import annotations

import pytest

from bmad_orchestrator.self_learning.config import SelfLearningConfig, SelfLearningDefaults
from bmad_orchestrator.self_learning.extractor import ProposedPattern
from bmad_orchestrator.self_learning.risk_classifier import classify_risk, is_auto_appliable


@pytest.fixture()
def default_config() -> SelfLearningConfig:
    return SelfLearningConfig(
        version=1,
        enabled=True,
        defaults=SelfLearningDefaults(),
        excluded_policy_files=["security-review.yaml", "deletion-safety.yaml"],
    )


def _pattern(
    policy_file: str = "cost-tuning.yaml",
    field: str = "story_alarm_usd",
    occurrences: int = 3,
    sources: tuple[str, ...] = ("/a.md", "/b.md", "/c.md"),
) -> ProposedPattern:
    return ProposedPattern(
        pattern_id="test",
        occurrence_count=occurrences,
        policy_file=policy_file,
        field=field,
        suggested_value=40.0,
        rationale="test",
        source_lessons=sources,
    )


def test_excluded_file_is_high_risk(default_config: SelfLearningConfig) -> None:
    p = _pattern(policy_file="security-review.yaml")
    assert classify_risk(p, default_config) == "high"


def test_deletion_safety_is_high_risk(default_config: SelfLearningConfig) -> None:
    p = _pattern(policy_file="deletion-safety.yaml")
    assert classify_risk(p, default_config) == "high"


def test_empty_policy_file_is_high_risk(default_config: SelfLearningConfig) -> None:
    p = _pattern(policy_file="")
    assert classify_risk(p, default_config) == "high"


def test_code_change_indicator_is_high_risk(default_config: SelfLearningConfig) -> None:
    p = _pattern(policy_file="code-hooks.yaml")
    assert classify_risk(p, default_config) == "high"


def test_threshold_field_is_medium_risk(default_config: SelfLearningConfig) -> None:
    p = _pattern(policy_file="cost-tuning.yaml", field="story_alarm_usd")
    assert classify_risk(p, default_config) == "medium"


def test_timeout_field_is_medium_risk(default_config: SelfLearningConfig) -> None:
    p = _pattern(policy_file="retry-policy.yaml", field="timeout_seconds")
    assert classify_risk(p, default_config) == "medium"


def test_many_sources_is_medium_risk(default_config: SelfLearningConfig) -> None:
    sources = tuple(f"/{i}.md" for i in range(6))
    p = _pattern(policy_file="retry-policy.yaml", field="max_retries", sources=sources)
    assert classify_risk(p, default_config) == "medium"


def test_normal_policy_is_low_risk(default_config: SelfLearningConfig) -> None:
    # backoff_seconds is not a threshold field and not excluded → low risk
    p = _pattern(policy_file="retry-policy.yaml", field="backoff_seconds", sources=("/a.md", "/b.md"))
    assert classify_risk(p, default_config) == "low"


def test_is_auto_appliable_low() -> None:
    assert is_auto_appliable("low") is True


def test_is_auto_appliable_medium() -> None:
    assert is_auto_appliable("medium") is False


def test_is_auto_appliable_high() -> None:
    assert is_auto_appliable("high") is False
