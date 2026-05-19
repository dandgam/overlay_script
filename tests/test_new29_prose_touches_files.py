"""NEW-29 — parse_story_md derives touches_files from backtick code-spans.

Real BMad story files have no canonical ``- **touches_files:**`` block; their
file paths live in backtick-wrapped tokens in the prose body. The heuristic
keeps only tokens that contain a "/" or end in a known code extension. The
canonical machine field always wins when present and non-empty.
"""

from __future__ import annotations

from bmad_orchestrator.agent.tools._common import parse_story_md


def test_backtick_paths_with_slash_are_extracted() -> None:
    """Tokens containing "/" should be extracted as file paths."""
    md = (
        "# Story 1.3: FastAPI app\n\n"
        "**Эпик:** 1\n"
        "Creates `src/antares/main.py` and `core/db.py`.\n"
    )
    result = parse_story_md(md)
    assert "touches_files" in result
    tf = result["touches_files"]
    assert "src/antares/main.py" in tf
    assert "core/db.py" in tf


def test_backtick_tokens_with_known_extension_and_no_slash_are_extracted() -> None:
    """Tokens like `main.py` (no slash, known extension) are kept."""
    md = "# Story 2.1\n\nEdit `main.py` and `config.toml`.\n"
    result = parse_story_md(md)
    tf = result.get("touches_files", [])
    assert "main.py" in tf
    assert "config.toml" in tf


def test_command_tokens_are_excluded() -> None:
    """Multi-word commands like ``make dev`` and ``alembic>=1.14`` are excluded."""
    md = (
        "# Story 1.4\n\n"
        "Run `make dev` to start, then `alembic>=1.14` for migrations.\n"
    )
    result = parse_story_md(md)
    tf = result.get("touches_files", [])
    assert "make dev" not in tf
    assert "alembic>=1.14" not in tf


def test_single_word_no_extension_excluded() -> None:
    """Plain identifiers like ``lifespan`` or ``redis`` (no ext, no slash) are excluded."""
    md = "# Story 1.3\n\nThe `lifespan` context manager and `redis` client.\n"
    result = parse_story_md(md)
    tf = result.get("touches_files", [])
    assert "lifespan" not in tf
    assert "redis" not in tf


def test_canonical_machine_field_wins() -> None:
    """When canonical ``- **touches_files:**`` is present and non-empty, it takes priority."""
    md = (
        "# Story 1.4\n\n"
        "- **touches_files:**\n"
        "  - src/canonical.py\n"
        "\n"
        "But also see `src/antares/main.py` in the prose.\n"
    )
    result = parse_story_md(md)
    tf = result["touches_files"]
    assert tf == ["src/canonical.py"], (
        "canonical field must win; prose paths should not be added"
    )


def test_empty_when_no_paths_in_prose() -> None:
    """Prose with no backtick file paths → touches_files absent or empty."""
    md = "# Story 9.1\n\nThis story has no code references at all.\n"
    result = parse_story_md(md)
    assert not result.get("touches_files")


def test_deduplication_preserves_first_seen_order() -> None:
    """Duplicate backtick paths should appear only once, in first-seen order."""
    md = (
        "# Story 2.2\n\n"
        "Modify `src/app.py` then check `src/app.py` again and `src/db.py`.\n"
    )
    result = parse_story_md(md)
    tf = result["touches_files"]
    assert tf.count("src/app.py") == 1
    assert tf.index("src/app.py") < tf.index("src/db.py")


def test_real_story_1_3_has_nonempty_touches_files() -> None:
    """Regression: real Antares story 1.3 must yield a non-empty touches_files
    containing src/antares/main.py."""
    from pathlib import Path

    story_path = Path("/home/server/Antares/_bmad/output/planning/stories/1.3.md")
    if not story_path.exists():
        import pytest
        pytest.skip("Antares story 1.3 not available in this environment")

    result = parse_story_md(story_path.read_text(encoding="utf-8"))
    tf = result.get("touches_files", [])
    assert len(tf) > 0, "touches_files must be non-empty for story 1.3"
    assert "src/antares/main.py" in tf
