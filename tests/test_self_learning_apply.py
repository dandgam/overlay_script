"""Tests for self_learning.apply — M2.

Includes the end-to-end mock scenario from spec §5:
5 synthetic lessons → 1 proposal → auto-apply → simulated regression → rollback.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bmad_orchestrator.runtime.lesson_parser import LessonProposal
from bmad_orchestrator.self_learning.apply import AutoApplyResult, AutoApplyService
from bmad_orchestrator.self_learning.config import SelfLearningConfig, SelfLearningDefaults
from bmad_orchestrator.self_learning.metrics import WaveMetrics


@pytest.fixture()
def default_config() -> SelfLearningConfig:
    return SelfLearningConfig(
        version=1,
        enabled=True,
        defaults=SelfLearningDefaults(
            measure_window_waves=2,
            regression_threshold_pct=5.0,
        ),
        excluded_policy_files=["security-review.yaml", "deletion-safety.yaml"],
    )


def _make_proposal(
    policy_file: str = "retry-policy",
    field: str = "backoff_seconds",
    before: object = 30,
    after: object = 60,
) -> LessonProposal:
    return LessonProposal(
        policy_file=policy_file,
        field=field,
        before=before,
        after=after,
        rationale="test rationale",
        source_file="test.md",
    )


def _setup_skills_root(tmp_path: Path, policy_file: str = "retry-policy") -> Path:
    """Create a minimal skills/policy/<policy_file>.yaml for testing.

    Uses only valid fields per the RetryPolicy pydantic model.
    """
    policy_dir = tmp_path / "policy"
    policy_dir.mkdir(parents=True)
    (policy_dir / f"{policy_file}.yaml").write_text(
        yaml.dump({"max_retries": 3, "backoff_seconds": 30, "escalation_triggers": []}),
        encoding="utf-8",
    )
    return tmp_path


def test_auto_apply_empty_proposals(
    default_config: SelfLearningConfig, tmp_path: Path
) -> None:
    skills_root = _setup_skills_root(tmp_path)
    svc = AutoApplyService(config=default_config, skills_root=skills_root)
    result = svc.auto_apply([], trigger_event="wave_boundary_reached", batch_id="b-001")
    assert isinstance(result, AutoApplyResult)
    assert result.applied_count == 0


def test_auto_apply_applies_valid_proposal(
    default_config: SelfLearningConfig, tmp_path: Path
) -> None:
    skills_root = _setup_skills_root(tmp_path)
    proposal = _make_proposal(field="backoff_seconds", before=30, after=60)
    svc = AutoApplyService(config=default_config, skills_root=skills_root)
    result = svc.auto_apply(
        [proposal],
        trigger_event="wave_boundary_reached",
        batch_id="b-001",
    )
    assert result.applied_count == 1
    assert result.error_count == 0
    # Verify file was actually modified
    policy_yaml = skills_root / "policy" / "retry-policy.yaml"
    content = yaml.safe_load(policy_yaml.read_text(encoding="utf-8"))
    assert content["backoff_seconds"] == 60


def test_auto_apply_error_on_invalid_field(
    default_config: SelfLearningConfig, tmp_path: Path
) -> None:
    skills_root = _setup_skills_root(tmp_path)
    # Create proposal with a field that doesn't exist on the model
    bad_proposal = LessonProposal(
        policy_file="retry-policy",
        field="nonexistent_field",
        before=1,
        after=2,
        rationale=None,
        source_file="test.md",
    )
    svc = AutoApplyService(config=default_config, skills_root=skills_root)
    result = svc.auto_apply(
        [bad_proposal],
        trigger_event="wave_boundary_reached",
        batch_id="b-002",
    )
    assert result.error_count == 1


def test_no_regression_no_rollback(
    default_config: SelfLearningConfig, tmp_path: Path
) -> None:
    skills_root = _setup_skills_root(tmp_path)
    svc = AutoApplyService(
        config=default_config,
        skills_root=skills_root,
        wave_history=[
            WaveMetrics("1a", escalation_rate=0.1, pass_rate=0.9, cost_per_story_usd=10.0),
            WaveMetrics("1b", escalation_rate=0.1, pass_rate=0.9, cost_per_story_usd=10.0),
        ],
    )
    # Current metrics stable — no regression
    current = WaveMetrics("2a", escalation_rate=0.1, pass_rate=0.9, cost_per_story_usd=10.0)
    rolled = svc.check_regression_and_rollback(current, "wave_boundary_reached")
    assert rolled is False


def test_regression_triggers_rollback(
    default_config: SelfLearningConfig, tmp_path: Path
) -> None:
    """End-to-end: apply proposal → inject regression → rollback is triggered."""
    skills_root = _setup_skills_root(tmp_path)
    svc = AutoApplyService(
        config=default_config,
        skills_root=skills_root,
        wave_history=[
            WaveMetrics("0a", pass_rate=0.95, escalation_rate=0.05, cost_per_story_usd=10.0),
            WaveMetrics("0b", pass_rate=0.95, escalation_rate=0.05, cost_per_story_usd=10.0),
        ],
    )
    # Apply a proposal to backoff_seconds (low-risk field)
    proposal = _make_proposal(field="backoff_seconds", before=30, after=60)
    apply_result = svc.auto_apply(
        [proposal],
        trigger_event="wave_boundary_reached",
        batch_id="b-test",
        wave_id="1a",
    )
    assert apply_result.applied_count == 1

    # Inject severe regression: pass_rate drops from 0.95 → 0.5 (47% drop > 5% threshold)
    svc.record_wave_metrics(
        WaveMetrics("1a", pass_rate=0.5, escalation_rate=0.05, cost_per_story_usd=10.0)
    )
    severe = WaveMetrics("1b", pass_rate=0.5, escalation_rate=0.05, cost_per_story_usd=10.0)
    rolled = svc.check_regression_and_rollback(severe, "wave_boundary_reached")
    assert rolled is True

    # Policy should be restored to original value
    policy_yaml = skills_root / "policy" / "retry-policy.yaml"
    content = yaml.safe_load(policy_yaml.read_text(encoding="utf-8"))
    assert content["backoff_seconds"] == 30


def test_insufficient_wave_history_no_rollback(
    default_config: SelfLearningConfig, tmp_path: Path
) -> None:
    skills_root = _setup_skills_root(tmp_path)
    svc = AutoApplyService(config=default_config, skills_root=skills_root)
    # Only 1 wave in history — not enough for 2-wave window comparison
    svc.record_wave_metrics(WaveMetrics("1a", pass_rate=0.5))
    current = WaveMetrics("1b", pass_rate=0.1)
    rolled = svc.check_regression_and_rollback(current, "wave_boundary_reached")
    assert rolled is False
