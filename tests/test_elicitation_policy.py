"""Tests for elicitation policy YAML loading + validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from bmad_orchestrator.elicitation.policy import (
    DEFAULT_HARD_ESCALATE_KEYWORDS,
    Defaults,
    ElicitationPolicy,
    PolicyNotFoundError,
    PolicyValidationError,
    Rule,
    load_policy,
)

EXAMPLE_YAML = Path(__file__).resolve().parent.parent / "examples" / "elicitation-policy.example.yaml"


def test_example_policy_loads_ok():
    policy = load_policy(EXAMPLE_YAML)
    assert policy.version == 1
    assert len(policy.rules) > 0


def test_example_policy_has_security_rules():
    policy = load_policy(EXAMPLE_YAML)
    rule_ids = {r.id for r in policy.rules}
    assert any(rid.startswith("sec-") for rid in rule_ids), "expected at least one sec-* rule"


def test_load_policy_missing_file(tmp_path: Path):
    with pytest.raises(PolicyNotFoundError):
        load_policy(tmp_path / "does-not-exist.yaml")


def test_load_policy_invalid_yaml(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("not: a mapping: [\n broken", encoding="utf-8")
    with pytest.raises(PolicyValidationError):
        load_policy(bad)


def test_load_policy_root_not_mapping(tmp_path: Path):
    bad = tmp_path / "list.yaml"
    bad.write_text("- one\n- two\n", encoding="utf-8")
    with pytest.raises(PolicyValidationError):
        load_policy(bad)


def test_load_policy_extra_field_rejected(tmp_path: Path):
    bad = tmp_path / "extra.yaml"
    bad.write_text(
        "version: 1\nunknown_field: bad\nrules: []\n",
        encoding="utf-8",
    )
    with pytest.raises(PolicyValidationError):
        load_policy(bad)


def test_defaults_hard_keywords_have_security_terms():
    d = Defaults()
    keywords_lower = {k.lower() for k in d.hard_escalate_keywords}
    for required in ("crypto", "pii", "gdpr"):
        assert required in keywords_lower, f"hard keyword list missing {required!r}"


def test_default_hard_keywords_constant_matches():
    assert "crypto" in DEFAULT_HARD_ESCALATE_KEYWORDS
    assert "drop table" in DEFAULT_HARD_ESCALATE_KEYWORDS


def test_rule_model_forbids_extras():
    with pytest.raises(Exception):
        Rule.model_validate(
            {
                "id": "x",
                "match": {"topics": ["t"]},
                "risk": "low",
                "action": "auto_resolve",
                "extra_field": "nope",
            }
        )


def test_minimal_valid_policy(tmp_path: Path):
    p = tmp_path / "min.yaml"
    p.write_text("version: 1\n", encoding="utf-8")
    policy = load_policy(p)
    assert isinstance(policy, ElicitationPolicy)
    assert policy.rules == []
    assert policy.defaults.max_auto_resolve_per_story == 5
