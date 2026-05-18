"""Tests for SupervisorPolicy YAML loading + schema validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from bmad_orchestrator.supervisor.policy import (
    Defaults,
    HardRule,
    HardRuleMatch,
    JudgeConfig,
    PolicyNotFoundError,
    PolicyValidationError,
    SupervisorDecision,
    SupervisorPolicy,
    ToolCall,
    load_policy,
)

DEFAULT_POLICY_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "supervisor-policy.yaml"
)


def test_default_policy_loads_ok():
    policy = load_policy(DEFAULT_POLICY_PATH)
    assert policy.version == 1
    assert len(policy.hard_rules) >= 3


def test_default_policy_has_budget_rule():
    policy = load_policy(DEFAULT_POLICY_PATH)
    ids = {r.id for r in policy.hard_rules}
    assert "budget-hard-cap" in ids


def test_default_policy_has_compliance_rule():
    policy = load_policy(DEFAULT_POLICY_PATH)
    ids = {r.id for r in policy.hard_rules}
    assert "compliance-sweep-routine" in ids


def test_default_policy_silent_failure_escalates():
    policy = load_policy(DEFAULT_POLICY_PATH)
    silent = next(r for r in policy.hard_rules if r.id == "silent-failure-always-escalate")
    assert silent.action == "escalate_human"


def test_load_policy_missing_file(tmp_path: Path):
    with pytest.raises(PolicyNotFoundError):
        load_policy(tmp_path / "nope.yaml")


def test_load_policy_invalid_yaml(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("not valid: [\n yaml here", encoding="utf-8")
    with pytest.raises(PolicyValidationError):
        load_policy(bad)


def test_load_policy_root_not_mapping(tmp_path: Path):
    bad = tmp_path / "list.yaml"
    bad.write_text("- one\n- two\n", encoding="utf-8")
    with pytest.raises(PolicyValidationError):
        load_policy(bad)


def test_load_policy_extra_field_rejected(tmp_path: Path):
    bad = tmp_path / "extra.yaml"
    bad.write_text("version: 1\nbogus_field: bad\n", encoding="utf-8")
    with pytest.raises(PolicyValidationError):
        load_policy(bad)


def test_defaults_have_sensible_values():
    d = Defaults()
    assert 0.0 < d.confidence_escalate_floor < d.confidence_auto_threshold <= 1.0
    assert d.max_actions_per_minute >= 1
    assert d.fail_safe_on_judge_error is True


def test_judge_config_default_model_is_sonnet():
    j = JudgeConfig()
    assert "sonnet" in j.model.lower()
    assert j.timeout_seconds > 0


def test_hard_rule_validates():
    rule = HardRule(
        id="x",
        when=HardRuleMatch(event="BUDGET_THRESHOLD_HIT", ratio_gte=1.0),
        action="pause_workers",
        reason="r",
    )
    assert rule.id == "x"
    assert rule.when.event == "BUDGET_THRESHOLD_HIT"


def test_hard_rule_match_unknown_event_rejected():
    with pytest.raises(Exception):  # ValidationError
        HardRuleMatch(event="UNKNOWN_EVENT")  # type: ignore[arg-type]


def test_supervisor_decision_confidence_bounded():
    with pytest.raises(Exception):  # ValidationError
        SupervisorDecision(
            action="auto_respond",
            confidence=1.5,
            reason="x",
            tier=1,
        )


def test_minimal_valid_policy(tmp_path: Path):
    p = tmp_path / "min.yaml"
    p.write_text("version: 1\n", encoding="utf-8")
    policy = load_policy(p)
    assert isinstance(policy, SupervisorPolicy)
    assert policy.hard_rules == []
    assert policy.defaults.max_actions_per_minute == 10


def test_tool_call_args_default_empty_dict():
    tc = ToolCall(name="trigger_sweep")
    assert tc.args == {}
