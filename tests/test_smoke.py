"""Smoke tests — проверяют что scaffold импортируется и базовые types работают."""

from __future__ import annotations


def test_import_package() -> None:
    import bmad_orchestrator

    assert bmad_orchestrator.__version__


def test_import_models() -> None:
    from bmad_orchestrator.models import Risk, Story, StoryStatus

    s = Story(id="1-2-tenant-signup", epic_id="1", title="x", file_path="/tmp/x.md")
    assert s.risk == Risk.MEDIUM
    assert s.status == StoryStatus.BACKLOG
    # BMad-canonical 5-state enum
    assert StoryStatus.READY_FOR_DEV.value == "ready-for-dev"
    assert StoryStatus.IN_PROGRESS.value == "in-progress"
    assert StoryStatus.DONE.value == "done"


def test_import_config() -> None:
    from bmad_orchestrator.config import BudgetConfig, ModelConfig

    m = ModelConfig()
    assert "opus" in m.planner

    b = BudgetConfig()
    assert b.story_alarm_usd == 30.0
    assert b.story_halt_usd == 50.0


def test_pii_detector_redaction() -> None:
    from bmad_orchestrator.bot.pii_detector import scrub_output

    text = "Contact john@example.com or +7 999 123-45-67"
    redacted, cats = scrub_output(text)
    assert "[EMAIL]" in redacted
    assert "[PHONE]" in redacted
    assert "john@example.com" not in redacted
    assert set(cats) == {"EMAIL", "PHONE"}


def test_budget_guard_alarm_halt() -> None:
    from bmad_orchestrator.agent.safety.budget_guard import BudgetGuard
    from bmad_orchestrator.config import BudgetConfig

    g = BudgetGuard(BudgetConfig())
    assert not g.check_story(20.0).breached_alarm
    assert g.check_story(35.0).breached_alarm
    assert not g.check_story(35.0).breached_halt
    assert g.check_story(60.0).breached_halt


def test_branch_isolation_blocks_direct_main() -> None:
    from bmad_orchestrator.agent.safety.branch_isolation import validate_merge_target

    ok, _ = validate_merge_target("main", has_human_approval=False)
    assert not ok

    ok, _ = validate_merge_target("integration/wave-1a", has_human_approval=False)
    assert ok

    ok, _ = validate_merge_target("main", has_human_approval=True)
    assert ok
