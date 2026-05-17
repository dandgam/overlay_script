"""E8 acceptance — L4 lessons → policy proposals + policy-apply CLI.

Spec: spec/spec_embed_phase45_with_selflearning.md §E8.

Coverage (30 tests):

* :func:`parse_lesson_markdown` (10) — empty input, single block, multi block,
  optional rationale, narrative ignored, missing ``before``, missing ``after``,
  unknown policy file, unknown field, scalar/list value parsing.
* :func:`parse_lessons_dir` (3) — scans ``*.md`` deterministically, missing
  dir → empty, empty dir → empty.
* Proposals YAML persistence (5) — path layout, round-trip, parent dir
  creation, atomic tempfile cleanup on raise, slug sanitisation.
* :func:`apply_proposal` (5) — code-review-gates / cost-tuning / retry-policy
  YAML writes, schema validation rejects out-of-range, other fields preserved.
* :func:`apply_proposals_batch` (5) — auto-apply path, interactive prompt
  invoked, rejection skips write, schema error → errors collection,
  ApplyResult counts.
* Audit emission (1) — ``policy_proposal_applied`` carries before/after for
  rollback.
* CLI ``policy-apply`` (1) — invocation with ``--auto-apply`` writes policy.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from bmad_orchestrator.cli.main import app
from bmad_orchestrator.runtime.lesson_parser import (
    LessonProposal,
    LessonProposalInvalidError,
    PolicyApplyError,
    apply_proposal,
    apply_proposals_batch,
    load_proposals_yaml,
    parse_lesson_markdown,
    parse_lessons_dir,
    policy_file_path,
    proposals_yaml_path,
    save_proposals_yaml,
)

# ── fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def policy_skills_root(tmp_path: Path) -> Path:
    """Skills root with the three baseline policy YAMLs written out."""
    root = tmp_path / "skills"
    policy = root / "policy"
    policy.mkdir(parents=True)
    (policy / "code-review-gates.yaml").write_text(
        "p0_threshold: 0.8\n"
        "test_coverage_threshold: 0.5\n"
        "compliance_tags:\n  - 152-ФЗ\n  - 187-ФЗ\n"
        "sweep_every_stories: 50\n",
        encoding="utf-8",
    )
    (policy / "cost-tuning.yaml").write_text(
        "daily_cap_usd: 50.0\nstory_reserve_usd: 0.5\nmax_concurrent_workers: 4\n",
        encoding="utf-8",
    )
    (policy / "retry-policy.yaml").write_text(
        "max_retries: 3\nbackoff_seconds: 30\nescalation_triggers:\n  - auto_rollback_failed\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def audit_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "audit.events.jsonl"
    monkeypatch.setenv("BMAD_AUDIT_LOG", str(target))
    return target


def _read_audit(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ── parse_lesson_markdown ──────────────────────────────────────────────────


def test_e8_parse_empty_markdown_returns_no_proposals(tmp_path: Path) -> None:
    out = parse_lesson_markdown("", source=tmp_path / "x.md")
    assert out == []


def test_e8_parse_single_proposal_block(tmp_path: Path) -> None:
    text = (
        "# Wave 1a retrospective\n\nNarrative here.\n\n"
        "## Policy proposal: code-review-gates.p0_threshold\n"
        "before: 0.8\n"
        "after: 0.7\n"
        "rationale: too strict on legacy modules\n"
    )
    out = parse_lesson_markdown(text, source=tmp_path / "wave-1a.md")
    assert len(out) == 1
    p = out[0]
    assert p.policy_file == "code-review-gates"
    assert p.field == "p0_threshold"
    assert p.before == 0.8
    assert p.after == 0.7
    assert p.rationale == "too strict on legacy modules"


def test_e8_parse_multiple_proposals_same_file(tmp_path: Path) -> None:
    text = (
        "## Policy proposal: code-review-gates.p0_threshold\n"
        "before: 0.8\n"
        "after: 0.7\n"
        "\n"
        "## Policy proposal: cost-tuning.daily_cap_usd\n"
        "before: 50.0\n"
        "after: 75.0\n"
    )
    out = parse_lesson_markdown(text, source=tmp_path / "x.md")
    assert [(p.policy_file, p.field) for p in out] == [
        ("code-review-gates", "p0_threshold"),
        ("cost-tuning", "daily_cap_usd"),
    ]


def test_e8_parse_proposal_rationale_optional(tmp_path: Path) -> None:
    text = (
        "## Policy proposal: cost-tuning.story_reserve_usd\n"
        "before: 0.5\n"
        "after: 0.75\n"
    )
    out = parse_lesson_markdown(text, source=tmp_path / "x.md")
    assert out[0].rationale is None


def test_e8_parse_ignores_narrative_text(tmp_path: Path) -> None:
    text = (
        "# Heading\n"
        "Some prose before:foo cannot be picked up.\n"
        "after:bar either.\n"
        "\n"
        "## Policy proposal: retry-policy.max_retries\n"
        "before: 3\n"
        "after: 5\n"
    )
    out = parse_lesson_markdown(text, source=tmp_path / "x.md")
    assert len(out) == 1
    assert out[0].after == 5


def test_e8_parse_malformed_missing_after_raises(tmp_path: Path) -> None:
    text = (
        "## Policy proposal: code-review-gates.p0_threshold\nbefore: 0.8\n"
    )
    with pytest.raises(LessonProposalInvalidError, match=r"missing 'after:"):
        parse_lesson_markdown(text, source=tmp_path / "x.md")


def test_e8_parse_malformed_missing_before_raises(tmp_path: Path) -> None:
    text = (
        "## Policy proposal: code-review-gates.p0_threshold\nafter: 0.7\n"
    )
    with pytest.raises(LessonProposalInvalidError, match=r"missing 'before:"):
        parse_lesson_markdown(text, source=tmp_path / "x.md")


def test_e8_parse_unknown_policy_file_raises(tmp_path: Path) -> None:
    text = "## Policy proposal: nonexistent-file.foo\nbefore: 1\nafter: 2\n"
    with pytest.raises(LessonProposalInvalidError, match="unknown policy file"):
        parse_lesson_markdown(text, source=tmp_path / "x.md")


def test_e8_parse_unknown_field_raises(tmp_path: Path) -> None:
    text = (
        "## Policy proposal: code-review-gates.nonexistent_field\n"
        "before: 1\n"
        "after: 2\n"
    )
    with pytest.raises(LessonProposalInvalidError, match="not on 'code-review-gates'"):
        parse_lesson_markdown(text, source=tmp_path / "x.md")


def test_e8_parse_handles_int_float_list_values(tmp_path: Path) -> None:
    text = (
        "## Policy proposal: retry-policy.escalation_triggers\n"
        "before: [auto_rollback_failed]\n"
        "after: [auto_rollback_failed, compliance_violation, sandbox_unavailable]\n"
        "\n"
        "## Policy proposal: retry-policy.max_retries\n"
        "before: 3\n"
        "after: 5\n"
        "\n"
        "## Policy proposal: code-review-gates.p0_threshold\n"
        "before: 0.8\n"
        "after: 0.65\n"
    )
    out = parse_lesson_markdown(text, source=tmp_path / "x.md")
    assert out[0].after == [
        "auto_rollback_failed",
        "compliance_violation",
        "sandbox_unavailable",
    ]
    assert out[1].after == 5
    assert out[2].after == 0.65


# ── parse_lessons_dir ──────────────────────────────────────────────────────


def test_e8_parse_lessons_dir_scans_md_files(tmp_path: Path) -> None:
    lessons = tmp_path / "lessons"
    lessons.mkdir()
    (lessons / "wave-1a.md").write_text(
        "## Policy proposal: code-review-gates.p0_threshold\nbefore: 0.8\nafter: 0.7\n",
        encoding="utf-8",
    )
    (lessons / "wave-1b.md").write_text(
        "## Policy proposal: cost-tuning.daily_cap_usd\nbefore: 50\nafter: 75\n",
        encoding="utf-8",
    )
    (lessons / "ignored.txt").write_text("not markdown", encoding="utf-8")
    out = parse_lessons_dir(lessons)
    assert [(p.policy_file, p.field) for p in out] == [
        ("code-review-gates", "p0_threshold"),
        ("cost-tuning", "daily_cap_usd"),
    ]


def test_e8_parse_lessons_dir_missing_returns_empty(tmp_path: Path) -> None:
    assert parse_lessons_dir(tmp_path / "does-not-exist") == []


def test_e8_parse_lessons_dir_empty_returns_empty(tmp_path: Path) -> None:
    lessons = tmp_path / "lessons"
    lessons.mkdir()
    assert parse_lessons_dir(lessons) == []


# ── proposals YAML persistence ─────────────────────────────────────────────


def test_e8_proposals_yaml_path_default_layout(tmp_path: Path) -> None:
    p = proposals_yaml_path("odyssey", orchestrator_home=tmp_path)
    assert p == tmp_path / "_config" / "projects" / "odyssey" / "policy-proposals.yaml"


def test_e8_proposals_yaml_path_rejects_bad_slug(tmp_path: Path) -> None:
    for bad in ("", "..", "../escape", "a/b", "a\\b"):
        with pytest.raises(LessonProposalInvalidError):
            proposals_yaml_path(bad, orchestrator_home=tmp_path)


def test_e8_save_proposals_yaml_creates_parent_and_round_trips(
    tmp_path: Path,
) -> None:
    proposals = [
        LessonProposal(
            policy_file="code-review-gates",
            field="p0_threshold",
            before=0.8,
            after=0.7,
            rationale="legacy modules",
            source_file="skills/lessons/odyssey/wave-1a.md",
        ),
    ]
    out = save_proposals_yaml(proposals, slug="odyssey", orchestrator_home=tmp_path)
    assert out.exists()
    loaded = load_proposals_yaml("odyssey", orchestrator_home=tmp_path)
    assert len(loaded) == 1
    assert loaded[0].policy_file == "code-review-gates"
    assert loaded[0].after == 0.7
    assert loaded[0].rationale == "legacy modules"


def test_e8_load_proposals_yaml_missing_returns_empty(tmp_path: Path) -> None:
    assert load_proposals_yaml("nothing", orchestrator_home=tmp_path) == []


def test_e8_save_proposals_yaml_atomic_tempfile_cleanup_on_raise(
    tmp_path: Path,
) -> None:
    proposals = [
        LessonProposal(
            policy_file="cost-tuning",
            field="daily_cap_usd",
            before=50.0,
            after=75.0,
            rationale=None,
            source_file="x",
        ),
    ]
    parent = tmp_path / "_config" / "projects" / "odyssey"
    with patch(
        "bmad_orchestrator.runtime.lesson_parser.os.replace",
        side_effect=OSError("boom"),
    ):
        with pytest.raises(OSError, match="boom"):
            save_proposals_yaml(proposals, slug="odyssey", orchestrator_home=tmp_path)
    leftovers = list(parent.glob("*.tmp"))
    assert leftovers == [], f"tempfile leftovers: {leftovers}"


# ── apply_proposal ─────────────────────────────────────────────────────────


def test_e8_apply_proposal_updates_code_review_gates_yaml(
    policy_skills_root: Path, audit_log: Path
) -> None:
    prop = LessonProposal(
        policy_file="code-review-gates",
        field="p0_threshold",
        before=0.8,
        after=0.65,
        rationale=None,
        source_file="x.md",
    )
    applied = apply_proposal(prop, skills_root=policy_skills_root)
    path = policy_file_path("code-review-gates", skills_root=policy_skills_root)
    on_disk = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert on_disk["p0_threshold"] == 0.65
    # sibling fields preserved
    assert on_disk["test_coverage_threshold"] == 0.5
    assert on_disk["compliance_tags"] == ["152-ФЗ", "187-ФЗ"]
    assert applied.policy_path == path


def test_e8_apply_proposal_updates_cost_tuning_yaml(
    policy_skills_root: Path, audit_log: Path
) -> None:
    prop = LessonProposal(
        policy_file="cost-tuning",
        field="daily_cap_usd",
        before=50.0,
        after=75.0,
        rationale=None,
        source_file="x.md",
    )
    apply_proposal(prop, skills_root=policy_skills_root)
    path = policy_file_path("cost-tuning", skills_root=policy_skills_root)
    on_disk = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert on_disk["daily_cap_usd"] == 75.0
    assert on_disk["story_reserve_usd"] == 0.5
    assert on_disk["max_concurrent_workers"] == 4


def test_e8_apply_proposal_updates_retry_policy_yaml(
    policy_skills_root: Path, audit_log: Path
) -> None:
    prop = LessonProposal(
        policy_file="retry-policy",
        field="max_retries",
        before=3,
        after=5,
        rationale=None,
        source_file="x.md",
    )
    apply_proposal(prop, skills_root=policy_skills_root)
    path = policy_file_path("retry-policy", skills_root=policy_skills_root)
    on_disk = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert on_disk["max_retries"] == 5
    assert on_disk["escalation_triggers"] == ["auto_rollback_failed"]


def test_e8_apply_proposal_schema_violation_raises_without_disk_write(
    policy_skills_root: Path, audit_log: Path
) -> None:
    prop = LessonProposal(
        policy_file="code-review-gates",
        field="p0_threshold",
        before=0.8,
        after=2.0,  # > 1.0 violates ge=0, le=1.0
        rationale=None,
        source_file="x.md",
    )
    path = policy_file_path("code-review-gates", skills_root=policy_skills_root)
    before_text = path.read_text(encoding="utf-8")
    with pytest.raises(PolicyApplyError, match="fails schema"):
        apply_proposal(prop, skills_root=policy_skills_root)
    # disk content untouched
    assert path.read_text(encoding="utf-8") == before_text


def test_e8_apply_proposal_missing_policy_file_raises(
    tmp_path: Path, audit_log: Path
) -> None:
    bare_root = tmp_path / "skills"
    (bare_root / "policy").mkdir(parents=True)
    prop = LessonProposal(
        policy_file="code-review-gates",
        field="p0_threshold",
        before=0.8,
        after=0.7,
        rationale=None,
        source_file="x.md",
    )
    with pytest.raises(PolicyApplyError, match="policy YAML missing"):
        apply_proposal(prop, skills_root=bare_root)


# ── apply_proposals_batch ──────────────────────────────────────────────────


def test_e8_apply_proposals_batch_auto_apply_writes_all_valid(
    policy_skills_root: Path, audit_log: Path
) -> None:
    proposals = [
        LessonProposal(
            policy_file="code-review-gates",
            field="p0_threshold",
            before=0.8,
            after=0.7,
            rationale=None,
            source_file="x.md",
        ),
        LessonProposal(
            policy_file="retry-policy",
            field="max_retries",
            before=3,
            after=4,
            rationale=None,
            source_file="x.md",
        ),
    ]
    result = apply_proposals_batch(
        proposals, skills_root=policy_skills_root, auto_apply=True
    )
    assert len(result.applied) == 2
    assert result.rejected == []
    assert result.errors == []


def test_e8_apply_proposals_batch_interactive_invokes_prompt_per_proposal(
    policy_skills_root: Path, audit_log: Path
) -> None:
    proposals = [
        LessonProposal(
            policy_file="code-review-gates",
            field="p0_threshold",
            before=0.8,
            after=0.7,
            rationale=None,
            source_file="x.md",
        ),
        LessonProposal(
            policy_file="retry-policy",
            field="max_retries",
            before=3,
            after=4,
            rationale=None,
            source_file="x.md",
        ),
    ]
    seen: list[str] = []

    def prompt(p: LessonProposal) -> bool:
        seen.append(f"{p.policy_file}.{p.field}")
        return True

    apply_proposals_batch(
        proposals, skills_root=policy_skills_root, prompt=prompt
    )
    assert seen == ["code-review-gates.p0_threshold", "retry-policy.max_retries"]


def test_e8_apply_proposals_batch_rejection_skips_write(
    policy_skills_root: Path, audit_log: Path
) -> None:
    prop = LessonProposal(
        policy_file="code-review-gates",
        field="p0_threshold",
        before=0.8,
        after=0.65,
        rationale=None,
        source_file="x.md",
    )
    path = policy_file_path("code-review-gates", skills_root=policy_skills_root)
    before_text = path.read_text(encoding="utf-8")
    result = apply_proposals_batch(
        [prop], skills_root=policy_skills_root, prompt=lambda _: False
    )
    assert result.applied == []
    assert result.rejected == [prop]
    assert path.read_text(encoding="utf-8") == before_text


def test_e8_apply_proposals_batch_schema_error_lands_in_errors(
    policy_skills_root: Path, audit_log: Path
) -> None:
    proposals = [
        LessonProposal(
            policy_file="code-review-gates",
            field="p0_threshold",
            before=0.8,
            after=0.7,
            rationale=None,
            source_file="x.md",
        ),
        LessonProposal(
            policy_file="code-review-gates",
            field="p0_threshold",
            before=0.7,
            after=2.0,  # out of range
            rationale=None,
            source_file="x.md",
        ),
    ]
    result = apply_proposals_batch(
        proposals, skills_root=policy_skills_root, auto_apply=True
    )
    assert len(result.applied) == 1
    assert len(result.errors) == 1
    assert "fails schema" in result.errors[0][1]


def test_e8_apply_proposals_batch_non_auto_requires_prompt(
    policy_skills_root: Path, audit_log: Path
) -> None:
    prop = LessonProposal(
        policy_file="cost-tuning",
        field="daily_cap_usd",
        before=50.0,
        after=75.0,
        rationale=None,
        source_file="x.md",
    )
    with pytest.raises(ValueError, match="prompt"):
        apply_proposals_batch([prop], skills_root=policy_skills_root)


# ── audit emission ─────────────────────────────────────────────────────────


def test_e8_apply_emits_policy_proposal_applied_audit_with_before_after(
    policy_skills_root: Path, audit_log: Path
) -> None:
    prop = LessonProposal(
        policy_file="cost-tuning",
        field="daily_cap_usd",
        before=50.0,
        after=75.0,
        rationale="wave 1b uplift",
        source_file="skills/lessons/odyssey/wave-1a.md",
    )
    apply_proposal(prop, skills_root=policy_skills_root)
    events = _read_audit(audit_log)
    assert len(events) == 1
    e = events[0]
    assert e["event_type"] == "policy_proposal_applied"
    assert e["policy_file"] == "cost-tuning"
    assert e["field"] == "daily_cap_usd"
    assert e["before"] == 50.0
    assert e["after"] == 75.0
    assert e["rationale"] == "wave 1b uplift"
    assert e["source_file"].endswith("wave-1a.md")


# ── CLI ───────────────────────────────────────────────────────────────────


def test_e8_cli_policy_apply_auto_apply_writes_policy(
    tmp_path: Path,
    policy_skills_root: Path,
    audit_log: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lessons = policy_skills_root / "lessons" / "odyssey"
    lessons.mkdir(parents=True)
    (lessons / "wave-1a.md").write_text(
        "## Policy proposal: code-review-gates.p0_threshold\n"
        "before: 0.8\n"
        "after: 0.65\n"
        "rationale: derived from wave 1a retro\n",
        encoding="utf-8",
    )
    home = tmp_path / "orch-home"
    home.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_ORCHESTRATOR_HOME", str(home))
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(tmp_path / "target"))
    (tmp_path / "target").mkdir()

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "policy-apply",
            "odyssey",
            "--auto-apply",
            "--skills-root",
            str(policy_skills_root),
            "--orchestrator-home",
            str(home),
        ],
    )
    assert result.exit_code == 0, result.output
    on_disk = yaml.safe_load(
        (policy_skills_root / "policy" / "code-review-gates.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert on_disk["p0_threshold"] == 0.65
    assert (home / "_config" / "projects" / "odyssey" / "policy-proposals.yaml").exists()
