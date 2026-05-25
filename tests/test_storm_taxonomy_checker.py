"""Tests for taxonomy-checker.py — 888 Storm §6.3."""

import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest_storm import STORM_DIR


@pytest.fixture()
def spec_dir(tmp_path, monkeypatch, chdir):
    """Create a spec/ dir in tmp_path and chdir to it."""
    spec = tmp_path / "spec"
    spec.mkdir()
    chdir(tmp_path)
    return spec


@pytest.fixture()
def chdir(tmp_path, monkeypatch):
    """Return a function to chdir via monkeypatch."""
    def _chdir(path):
        monkeypatch.chdir(path)
    return _chdir


VALID_TAXONOMY_CONTENT = """\
# Taxonomy: test-scope

## Categories

- Bug fix repetition
- Feature creep
- Architectural drift
- Environment issues

## Reactions

- Bug fix repetition → try-fix (1x), soft-warn (2x), hard-halt (3x)
- Feature creep → soft-warn then LLM-judge
- Architectural drift → LLM-judge then hard-halt
- Environment issues → try-fix

## Coverage

Any new case maps to one of the 4 categories above.
New type? Explicit taxonomy extension required (Rule M3).
"""

INVALID_TAXONOMY_CONTENT_TODO = """\
## Categories

- TODO item 1
- Item 2
- Item 3

## Reactions

- Item 1 → try-fix

## Coverage

TBD
"""

INCOMPLETE_TAXONOMY_CONTENT = """\
## Categories

- Only one item

## Reactions

- Item 1 → try-fix

## Coverage

Some coverage text here.
"""


class TestFindTaxonomy:
    def test_missing_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from taxonomy_checker import find_taxonomy
        assert find_taxonomy("nonexistent-scope") is None

    def test_finds_in_spec_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        spec = tmp_path / "spec"
        spec.mkdir()
        taxonomy = spec / "taxonomy_my-scope.md"
        taxonomy.write_text(VALID_TAXONOMY_CONTENT)
        from taxonomy_checker import find_taxonomy
        result = find_taxonomy("my-scope")
        assert result is not None

    def test_finds_in_current_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        taxonomy = tmp_path / "taxonomy_my-scope.md"
        taxonomy.write_text(VALID_TAXONOMY_CONTENT)
        from taxonomy_checker import find_taxonomy
        result = find_taxonomy("my-scope")
        assert result is not None


class TestCheckSchema:
    def test_valid_taxonomy_passes(self, tmp_path):
        path = tmp_path / "taxonomy_test.md"
        path.write_text(VALID_TAXONOMY_CONTENT)
        from taxonomy_checker import check_schema
        valid, reason = check_schema(path)
        assert valid is True
        assert reason == "OK"

    def test_todo_placeholder_fails(self, tmp_path):
        path = tmp_path / "taxonomy_test.md"
        path.write_text(INVALID_TAXONOMY_CONTENT_TODO)
        from taxonomy_checker import check_schema
        valid, reason = check_schema(path)
        assert valid is False
        assert "TODO" in reason or "placeholder" in reason.lower()

    def test_missing_categories_fails(self, tmp_path):
        path = tmp_path / "taxonomy_test.md"
        path.write_text("## Reactions\n\ntry-fix\n\n## Coverage\n\nsome text")
        from taxonomy_checker import check_schema
        valid, reason = check_schema(path)
        assert valid is False
        assert "Categories" in reason

    def test_too_few_categories_fails(self, tmp_path):
        path = tmp_path / "taxonomy_test.md"
        path.write_text(INCOMPLETE_TAXONOMY_CONTENT)
        from taxonomy_checker import check_schema
        valid, reason = check_schema(path)
        assert valid is False

    def test_missing_reactions_fails(self, tmp_path):
        path = tmp_path / "taxonomy_test.md"
        path.write_text("## Categories\n\n- A\n- B\n- C\n\n## Coverage\n\nsome text")
        from taxonomy_checker import check_schema
        valid, reason = check_schema(path)
        assert valid is False
        assert "Reactions" in reason

    def test_reactions_missing_levels_fails(self, tmp_path):
        path = tmp_path / "taxonomy_test.md"
        path.write_text(
            "## Categories\n\n- A\n- B\n- C\n\n"
            "## Reactions\n\n- A → handle it\n\n"
            "## Coverage\n\nsome coverage"
        )
        from taxonomy_checker import check_schema
        valid, reason = check_schema(path)
        assert valid is False
        assert "level" in reason.lower()

    def test_missing_coverage_fails(self, tmp_path):
        path = tmp_path / "taxonomy_test.md"
        path.write_text(
            "## Categories\n\n- A\n- B\n- C\n\n"
            "## Reactions\n\n- A → try-fix\n"
        )
        from taxonomy_checker import check_schema
        valid, reason = check_schema(path)
        assert valid is False
        assert "Coverage" in reason


class TestCheckFunction:
    def test_missing_scope_returns_1(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from taxonomy_checker import check
        assert check("no-such-scope") == 1

    def test_valid_taxonomy_returns_0(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        spec = tmp_path / "spec"
        spec.mkdir()
        (spec / "taxonomy_good-scope.md").write_text(VALID_TAXONOMY_CONTENT)
        from taxonomy_checker import check
        assert check("good-scope") == 0

    def test_invalid_taxonomy_returns_2(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        spec = tmp_path / "spec"
        spec.mkdir()
        (spec / "taxonomy_bad-scope.md").write_text(INVALID_TAXONOMY_CONTENT_TODO)
        from taxonomy_checker import check
        assert check("bad-scope") == 2


class TestCLI:
    def test_cli_missing_scope_exits_1(self, tmp_path):
        script = STORM_DIR / "taxonomy-checker.py"
        r = subprocess.run(
            [sys.executable, str(script), "nonexistent-scope-xyz-99"],
            capture_output=True, text=True, timeout=15,
            cwd=str(tmp_path),
        )
        assert r.returncode == 1

    def test_cli_valid_taxonomy_exits_0(self, tmp_path):
        spec = tmp_path / "spec"
        spec.mkdir()
        (spec / "taxonomy_cli-test.md").write_text(VALID_TAXONOMY_CONTENT)
        script = STORM_DIR / "taxonomy-checker.py"
        r = subprocess.run(
            [sys.executable, str(script), "cli-test"],
            capture_output=True, text=True, timeout=15,
            cwd=str(tmp_path),
        )
        assert r.returncode == 0
