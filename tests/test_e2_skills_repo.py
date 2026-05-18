"""E2 acceptance tests — skills_repo (customize + policy loaders).

См. spec/spec_embed_phase45_with_selflearning.md §4 E2.

Покрытие:
* EMBEDDED_SKILL_NAMES = 16 canonical phase 4+5 skills.
* Customize loader: empty-stub defaults, schema fields, extra=forbid, errors.
* PolicyConfig loader: production YAML defaults, bounds, missing-file errors.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bmad_orchestrator.skills_repo import (
    EMBEDDED_SKILL_NAMES,
    Customize,
    CustomizeInvalidError,
    CustomizeNotFoundError,
    PolicyInvalidError,
    PolicyNotFoundError,
    load_all_customize,
    load_customize,
    load_policy,
)


def _scaffold(tmp_path: Path) -> Path:
    """Build empty `customize/` + `policy/` subdirs under tmp_path."""
    (tmp_path / "customize").mkdir()
    (tmp_path / "policy").mkdir()
    return tmp_path


def _write_policy_trio(
    root: Path,
    *,
    gates: str = "p0_threshold: 0.8\ntest_coverage_threshold: 0.5\ncompliance_tags: []\nsweep_every_stories: 50\n",
    cost: str = "daily_cap_usd: 50.0\nstory_reserve_usd: 0.5\nmax_concurrent_workers: 4\n",
    retry: str = "max_retries: 3\nbackoff_seconds: 30\nescalation_triggers: []\n",
) -> None:
    (root / "policy" / "code-review-gates.yaml").write_text(gates, encoding="utf-8")
    (root / "policy" / "cost-tuning.yaml").write_text(cost, encoding="utf-8")
    (root / "policy" / "retry-policy.yaml").write_text(retry, encoding="utf-8")


# ─────────────────── 1. Constants ───────────────────


def test_embedded_skill_names_count_is_16() -> None:
    assert len(EMBEDDED_SKILL_NAMES) == 16
    assert "bmad-auto-dev" in EMBEDDED_SKILL_NAMES
    assert "bmad-retrospective" in EMBEDDED_SKILL_NAMES
    # BMad Phase 4 canonical workflows — gap closed 2026-05-19
    assert "bmad-sprint-planning" in EMBEDDED_SKILL_NAMES
    assert "bmad-investigate" in EMBEDDED_SKILL_NAMES


# ─────────────────── 2-9. Customize ───────────────────


def test_customize_load_empty_stub_returns_defaults() -> None:
    c = load_customize("bmad-auto-dev")
    assert c.enabled is True
    assert c.description_override is None
    assert c.extra_triggers == []
    assert c.body_overlay is None
    assert c.variables == {}


def test_customize_missing_file_raises_not_found(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    with pytest.raises(CustomizeNotFoundError, match="not found"):
        load_customize("nonexistent-skill", root=tmp_path)


def test_customize_description_override_parses(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    (tmp_path / "customize" / "foo.customize.toml").write_text(
        'description_override = "AABIT-tweaked variant"\n', encoding="utf-8"
    )
    c = load_customize("foo", root=tmp_path)
    assert c.description_override == "AABIT-tweaked variant"
    assert c.enabled is True


def test_customize_extra_triggers_parses(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    (tmp_path / "customize" / "foo.customize.toml").write_text(
        'extra_triggers = ["/quick-dev", "auto-dev now"]\n', encoding="utf-8"
    )
    c = load_customize("foo", root=tmp_path)
    assert c.extra_triggers == ["/quick-dev", "auto-dev now"]


def test_customize_body_overlay_parses(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    (tmp_path / "customize" / "foo.customize.toml").write_text(
        'body_overlay = """\n## Local addendum\n\nApply RLS audit step.\n"""\n',
        encoding="utf-8",
    )
    c = load_customize("foo", root=tmp_path)
    assert c.body_overlay is not None
    assert "Local addendum" in c.body_overlay
    assert "RLS audit" in c.body_overlay


def test_customize_variables_parses(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    (tmp_path / "customize" / "foo.customize.toml").write_text(
        "[variables]\nproject = \"aabit-crm\"\nmax_loops = \"5\"\n", encoding="utf-8"
    )
    c = load_customize("foo", root=tmp_path)
    assert c.variables == {"project": "aabit-crm", "max_loops": "5"}


def test_customize_extra_field_forbidden(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    (tmp_path / "customize" / "foo.customize.toml").write_text(
        "rogue_field = true\n", encoding="utf-8"
    )
    with pytest.raises(CustomizeInvalidError, match="schema validation"):
        load_customize("foo", root=tmp_path)


def test_customize_invalid_toml_syntax_raises(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    (tmp_path / "customize" / "foo.customize.toml").write_text(
        "this = is = not = toml\n", encoding="utf-8"
    )
    with pytest.raises(CustomizeInvalidError, match="TOML parse"):
        load_customize("foo", root=tmp_path)


def test_load_all_customize_returns_16_canonical_skills() -> None:
    all_c = load_all_customize()
    assert set(all_c.keys()) == set(EMBEDDED_SKILL_NAMES)
    assert len(all_c) == 16
    for name, c in all_c.items():
        assert isinstance(c, Customize), f"{name} did not parse"
        assert c.enabled is True


# ─────────────────── 10-15. PolicyConfig ───────────────────


def test_policy_load_production_defaults_parses() -> None:
    p = load_policy()
    assert p.code_review_gates.p0_threshold == 0.8
    assert "152-ФЗ" in p.code_review_gates.compliance_tags
    assert p.code_review_gates.sweep_every_stories == 50
    assert p.cost_tuning.daily_cap_usd == 50.0
    assert p.cost_tuning.max_concurrent_workers == 4
    assert p.retry_policy.max_retries == 3
    assert "auto_rollback_failed" in p.retry_policy.escalation_triggers


def test_policy_code_review_gates_threshold_out_of_range_raises(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    _write_policy_trio(
        tmp_path,
        gates="p0_threshold: 1.5\ntest_coverage_threshold: 0.5\ncompliance_tags: []\nsweep_every_stories: 50\n",
    )
    with pytest.raises(PolicyInvalidError, match="schema validation"):
        load_policy(root=tmp_path)


def test_policy_cost_tuning_negative_daily_cap_raises(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    _write_policy_trio(
        tmp_path,
        cost="daily_cap_usd: -1.0\nstory_reserve_usd: 0.5\nmax_concurrent_workers: 4\n",
    )
    with pytest.raises(PolicyInvalidError, match="schema validation"):
        load_policy(root=tmp_path)


def test_policy_retry_policy_max_retries_zero_ok(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    _write_policy_trio(
        tmp_path,
        retry="max_retries: 0\nbackoff_seconds: 0\nescalation_triggers: []\n",
    )
    p = load_policy(root=tmp_path)
    assert p.retry_policy.max_retries == 0
    assert p.retry_policy.backoff_seconds == 0


def test_policy_missing_yaml_raises_not_found(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    with pytest.raises(PolicyNotFoundError, match="not found"):
        load_policy(root=tmp_path)
