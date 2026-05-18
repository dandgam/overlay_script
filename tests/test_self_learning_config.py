"""Tests for self_learning.config — M1."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from bmad_orchestrator.self_learning.config import (
    PolicyNotFoundError,
    PolicyValidationError,
    SelfLearningConfig,
    SelfLearningDefaults,
    load_config,
)


@pytest.fixture()
def default_yaml(tmp_path: Path) -> Path:
    src = Path("config/self-learning.yaml")
    dest = tmp_path / "self-learning.yaml"
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dest


def test_load_default_config(default_yaml: Path) -> None:
    cfg = load_config(default_yaml)
    assert isinstance(cfg, SelfLearningConfig)
    assert cfg.version == 1
    assert cfg.enabled is True
    assert cfg.defaults.min_pattern_occurrences == 3
    assert cfg.defaults.measure_window_waves == 2
    assert cfg.defaults.regression_threshold_pct == 5.0
    assert cfg.defaults.auto_apply_max_risk == "low"


def test_excluded_policy_files_hard_gate(default_yaml: Path) -> None:
    cfg = load_config(default_yaml)
    # HARD GATE: both files must always be present in default config
    assert "security-review.yaml" in cfg.excluded_policy_files
    assert "deletion-safety.yaml" in cfg.excluded_policy_files


def test_excluded_compliance_tags_present(default_yaml: Path) -> None:
    cfg = load_config(default_yaml)
    assert "152-ФЗ" in cfg.excluded_compliance_tags
    assert "187-ФЗ" in cfg.excluded_compliance_tags


def test_missing_file_raises_policy_not_found(tmp_path: Path) -> None:
    with pytest.raises(PolicyNotFoundError):
        load_config(tmp_path / "nonexistent.yaml")


def test_bad_yaml_raises_policy_validation_error(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("{{not: valid: yaml: :\n", encoding="utf-8")
    with pytest.raises(PolicyValidationError):
        load_config(p)


def test_extra_field_rejected(tmp_path: Path) -> None:
    p = tmp_path / "extra.yaml"
    p.write_text(
        textwrap.dedent("""\
            version: 1
            enabled: true
            unknown_field: oops
        """),
        encoding="utf-8",
    )
    with pytest.raises(PolicyValidationError):
        load_config(p)


def test_non_mapping_root_raises(tmp_path: Path) -> None:
    p = tmp_path / "list.yaml"
    p.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(PolicyValidationError):
        load_config(p)


def test_defaults_model_construction() -> None:
    d = SelfLearningDefaults()
    assert d.auto_apply_max_risk == "low"
    assert d.extractor_model == "claude-sonnet-4-6"


def test_min_pattern_occurrences_min_bound(tmp_path: Path) -> None:
    p = tmp_path / "cfg.yaml"
    p.write_text(
        textwrap.dedent("""\
            version: 1
            defaults:
              min_pattern_occurrences: 0
        """),
        encoding="utf-8",
    )
    with pytest.raises(PolicyValidationError):
        load_config(p)
