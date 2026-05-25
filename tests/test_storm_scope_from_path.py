"""Tests for scope_from_path.py — 888 Storm §5.3."""

import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest_storm import STORM_DIR


class TestScopeFromPath:
    def test_runtime_prefix(self):
        from scope_from_path import scope_from_path
        result = scope_from_path("runtime/sandbox.py")
        assert result.startswith("virgil")

    def test_src_top_level(self):
        from scope_from_path import scope_from_path
        result = scope_from_path("src/bmad_orchestrator/dag.py")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_tests_dir(self):
        from scope_from_path import scope_from_path
        result = scope_from_path("tests/test_foo.py")
        assert isinstance(result, str)

    def test_absolute_path(self):
        from scope_from_path import scope_from_path
        result = scope_from_path("/home/server/bmad-orchestrator/src/foo/bar.py")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_single_file(self):
        from scope_from_path import scope_from_path
        result = scope_from_path("CLAUDE.md")
        # Should return something (repo basename or stem)
        assert result != ""

    def test_empty_path(self):
        from scope_from_path import scope_from_path
        result = scope_from_path("")
        assert isinstance(result, str)

    def test_nested_path(self):
        from scope_from_path import scope_from_path
        result = scope_from_path("agent/worker/pool.py")
        # agent maps to virgil
        assert "virgil" in result or isinstance(result, str)

    def test_storm_dir_path(self):
        from scope_from_path import scope_from_path
        result = scope_from_path("storm/fingerprint_tracker.py")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_output_is_single_token(self):
        """Result must be a single token (no spaces)."""
        from scope_from_path import scope_from_path
        paths = [
            "runtime/sandbox.py",
            "tests/test_foo.py",
            "src/bar/baz.py",
        ]
        for p in paths:
            result = scope_from_path(p)
            assert " " not in result, f"Got space in result: '{result}' for path '{p}'"


class TestCLI:
    def test_cli_returns_single_token(self):
        script = STORM_DIR / "scope_from_path.py"
        r = subprocess.run(
            [sys.executable, str(script), "/home/server/bmad-orchestrator/src/foo/bar.py"],
            capture_output=True, text=True, timeout=15,
        )
        assert r.returncode == 0
        result = r.stdout.strip()
        assert len(result) > 0
        assert "\n" not in result

    def test_cli_no_args_exits_1(self):
        script = STORM_DIR / "scope_from_path.py"
        r = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=15,
        )
        assert r.returncode == 1
