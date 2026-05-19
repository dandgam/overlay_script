"""NEW-30 — auto-split must see real story size from prose/markdown.

Real BMad story files carry no ``- **estimated_tokens:**`` machine field; the
split heuristic read all-zero metrics and never split a story, however large.
``parse_story_md`` now derives ``ac_count`` (distinct ``AC<n>`` headers) and
``task_count`` (Tasks/Subtasks checkboxes) from the markdown body.
"""

from __future__ import annotations

from bmad_orchestrator.agent.tools._common import parse_story_md
from bmad_orchestrator.runtime.story_splitter import (
    SPLIT_TASK_THRESHOLD,
    evaluate_split,
)


def test_ac_count_from_markdown_headers() -> None:
    md = (
        "# Story 1.3\n\n"
        "## Acceptance Criteria\n\n"
        "**AC1 — preload:**\nGIVEN x\n\n"
        "**AC2 — health:**\nGIVEN y\n\n"
        "### AC 3\nGIVEN z\n\n"
        "Tasks reference AC1-AC3 later, mid-sentence — must not double-count.\n"
    )
    assert parse_story_md(md)["ac_count"] == 3


def test_task_count_from_checkbox_list() -> None:
    md = (
        "# Story 1.3\n\n## Tasks / Subtasks\n\n"
        + "\n".join(f"- [ ] task {i}" for i in range(5))
        + "\n- [x] done task\n"
    )
    assert parse_story_md(md)["task_count"] == 6


def test_large_story_splits_on_task_count() -> None:
    """A story with no machine fields but a long subtask list must split."""
    md = (
        "# Story 1.3: FastAPI app\n\n"
        "## Acceptance Criteria\n\n**AC1 — x:**\nGIVEN a\n\n**AC2 — y:**\nGIVEN b\n\n"
        "## Tasks / Subtasks\n\n"
        + "\n".join(f"- [ ] subtask {i}" for i in range(SPLIT_TASK_THRESHOLD + 5))
    )
    meta = parse_story_md(md)
    decision = evaluate_split(meta)
    assert decision.decision == "split"
    assert any("tasks>=" in r for r in decision.rules_hit)
    assert decision.task_count == SPLIT_TASK_THRESHOLD + 5


def test_small_story_still_kept() -> None:
    """A genuinely small story (few ACs, few tasks) must not split."""
    md = (
        "# Story 10-1: small\n\n"
        "## Acceptance Criteria\n\n**AC1 — x:**\nGIVEN a\n\n"
        "## Tasks / Subtasks\n\n"
        + "\n".join(f"- [ ] subtask {i}" for i in range(8))
    )
    assert evaluate_split(parse_story_md(md)).decision == "keep"


def test_canonical_machine_fields_still_win() -> None:
    """An explicit ``- **estimated_tokens:**`` is not clobbered by prose."""
    md = (
        "# Story 1.1\n\n"
        "- **estimated_tokens:** 99999\n"
        "## Tasks / Subtasks\n\n- [ ] one task\n"
    )
    meta = parse_story_md(md)
    assert meta["estimated_tokens"] == 99999
    assert evaluate_split(meta).decision == "split"  # token rule fires
