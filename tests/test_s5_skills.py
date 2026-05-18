"""S5 acceptance tests — 14 specialized internal skills (spec §19).

Coverage:
- All 12 expected skills present with valid frontmatter (name + description).
- Skill metadata aggregate fits under MAX_METADATA_TOKENS_TOTAL (~1.5K tokens).
- Per-skill metadata under MAX_METADATA_TOKENS_PER_SKILL (~120 tokens).
- Dispatcher routes EventType → skill names correctly per TRIGGER_MAP.
- Body of a skill is loaded on demand (not part of metadata block).
- Reflexion loop (Actor / Evaluator / Self-Reflection) present in
  reflexion-learner SKILL.md body — spec §19.3.
- References subdirs (Level 3) discoverable + path-traversal blocked.
- system_prompt._tool_and_skill_metadata embeds skill catalog.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bmad_orchestrator.agent.skills import (
    EXPECTED_SKILL_NAMES,
    MAX_METADATA_TOKENS_PER_SKILL,
    MAX_METADATA_TOKENS_TOTAL,
    SKILLS_DIR,
    SkillError,
    dispatch,
    iter_metadata,
    list_references,
    load_body,
    load_reference,
    metadata_block,
)
from bmad_orchestrator.runtime.event_loop import EventType


def test_all_14_skills_present() -> None:
    # Phase 4 hardening #5 added merge-gate-spec + merge-gate-quality (14 total).
    metas = iter_metadata()
    names = {m.name for m in metas}
    assert names == EXPECTED_SKILL_NAMES
    assert len(metas) == 14


def test_each_skill_has_valid_frontmatter() -> None:
    for m in iter_metadata():
        assert m.name, f"empty name in {m.path}"
        assert m.description, f"empty description in {m.path}"
        assert m.path.name == "SKILL.md"
        assert m.path.parent.name == m.name


def test_metadata_total_under_budget() -> None:
    metas = iter_metadata()
    total = sum(m.estimated_tokens for m in metas)
    assert total <= MAX_METADATA_TOKENS_TOTAL, (
        f"Skill metadata budget overflow: {total} > {MAX_METADATA_TOKENS_TOTAL}. "
        "Shorten descriptions."
    )


def test_each_skill_metadata_under_per_skill_budget() -> None:
    for m in iter_metadata():
        assert m.estimated_tokens <= MAX_METADATA_TOKENS_PER_SKILL, (
            f"{m.name}: {m.estimated_tokens} > {MAX_METADATA_TOKENS_PER_SKILL}"
        )


def test_metadata_block_renderable() -> None:
    block = metadata_block()
    assert "Skill catalog" in block
    for name in EXPECTED_SKILL_NAMES:
        assert name in block


def test_dispatch_worker_completed_routes_to_merge_gate_and_failure_analyst() -> None:
    names = dispatch(EventType.WORKER_COMPLETED)
    # merge-gate (deprecated) + merge-gate-spec both subscribe to WORKER_COMPLETED.
    assert "merge-gate" in names
    assert "merge-gate-spec" in names
    assert "failure-analyst" in names


def test_dispatch_budget_threshold_hit_only_cost_watchdog() -> None:
    names = dispatch(EventType.BUDGET_THRESHOLD_HIT)
    assert names == ["cost-watchdog"]


def test_dispatch_user_chat_message_includes_intent_router() -> None:
    names = dispatch(EventType.USER_CHAT_MESSAGE)
    assert "intent-router" in names
    assert "proactive-improver" in names


def test_dispatch_wave_boundary_fans_out_to_multiple_skills() -> None:
    names = dispatch(EventType.WAVE_BOUNDARY_REACHED)
    expected = {
        "dag-planner",
        "retrospective-writer",
        "reflexion-learner",
        "wave-coordinator",
        "proactive-improver",
    }
    assert expected.issubset(set(names))


def test_dispatch_unknown_event_returns_empty_list() -> None:
    names = dispatch(EventType.HUMAN_RESPONSE)
    assert names == []


def test_dispatch_phase4_complete_includes_phase5_skills() -> None:
    names = dispatch(EventType.PHASE4_COMPLETE)
    assert "retrospective-writer" in names
    assert "reflexion-learner" in names
    assert "wave-coordinator" in names
    assert "proactive-improver" in names


def test_dispatch_worker_elicitation_only_elicitation_router() -> None:
    names = dispatch(EventType.WORKER_ELICITATION)
    assert names == ["elicitation-router"]


def test_dispatch_story_split_triggered_only_story_splitter() -> None:
    names = dispatch(EventType.STORY_SPLIT_TRIGGERED)
    assert names == ["story-splitter"]


def test_dispatch_voice_message_routes_to_intent_router() -> None:
    names = dispatch(EventType.VOICE_MESSAGE_RECEIVED)
    assert names == ["intent-router"]


def test_load_body_returns_full_skill_md_without_frontmatter() -> None:
    # merge-gate is now a deprecated stub per Phase 4 hardening #5.
    body = load_body("merge-gate")
    assert body.startswith("# merge-gate skill") or body.startswith("# ")
    # Frontmatter delimiters not present in body
    assert not body.startswith("---")
    # New skills carry full content
    spec_body = load_body("merge-gate-spec")
    assert "Когда активируется" in spec_body
    quality_body = load_body("merge-gate-quality")
    assert "Когда активируется" in quality_body


def test_load_body_unknown_raises() -> None:
    with pytest.raises(SkillError, match="Unknown skill"):
        load_body("nonexistent")


def test_reflexion_learner_body_contains_three_role_loop() -> None:
    body = load_body("reflexion-learner")
    # Spec §19.3 — Actor / Evaluator / Self-Reflection
    assert "Actor" in body
    assert "Evaluator" in body
    assert "Self-Reflection" in body
    # Output goes to Memory Tool
    assert "Memory" in body or "memory" in body


def test_dag_planner_references_listed() -> None:
    refs = list_references("dag-planner")
    assert "networkx-patterns.md" in refs
    assert "conflict-detection.md" in refs


def test_merge_gate_references_listed() -> None:
    refs = list_references("merge-gate")
    assert "review-criteria.md" in refs


def test_proactive_improver_references_listed() -> None:
    refs = list_references("proactive-improver")
    assert "proposal-templates.md" in refs


def test_load_reference_returns_content() -> None:
    text = load_reference("dag-planner", "networkx-patterns.md")
    assert "networkx" in text.lower()


def test_load_reference_path_traversal_blocked() -> None:
    with pytest.raises(SkillError, match="Invalid reference filename"):
        load_reference("dag-planner", "../SKILL.md")
    with pytest.raises(SkillError, match="Invalid reference filename"):
        load_reference("dag-planner", "/etc/passwd")


def test_load_reference_unknown_skill_blocked() -> None:
    with pytest.raises(SkillError, match="Unknown skill"):
        load_reference("nonexistent", "foo.md")


def test_load_reference_missing_file_raises() -> None:
    with pytest.raises(SkillError, match="not found"):
        load_reference("dag-planner", "doesnotexist.md")


def test_iter_metadata_cached() -> None:
    # Same tuple returned (lru_cache)
    assert iter_metadata() is iter_metadata()


def test_system_prompt_includes_skill_catalog() -> None:
    from bmad_orchestrator.agent.system_prompt import build_system_prompt

    blocks = build_system_prompt(Path("/tmp"), wave="1a", locale="ru")
    catalog_text = next(b["text"] for b in blocks if "Skill catalog" in b["text"])
    for name in EXPECTED_SKILL_NAMES:
        assert name in catalog_text


def test_metadata_descriptions_are_short_and_action_oriented() -> None:
    for m in iter_metadata():
        # Descriptions should be one-line summaries, not paragraphs
        assert "\n" not in m.description
        # Должно содержать минимум один глагол / условие активации
        assert len(m.description) >= 20
        assert len(m.description) <= 350


def test_no_trigger_routes_to_unknown_skill() -> None:
    from bmad_orchestrator.agent.skills import TRIGGER_MAP

    for skill_name in TRIGGER_MAP:
        assert skill_name in EXPECTED_SKILL_NAMES


def test_skill_dirs_match_skill_names() -> None:
    dirs = {
        p.name
        for p in SKILLS_DIR.iterdir()
        if p.is_dir() and not p.name.startswith("_") and (p / "SKILL.md").exists()
    }
    assert dirs == EXPECTED_SKILL_NAMES
