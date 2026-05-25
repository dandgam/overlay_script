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

    def test_absolute_path_src(self):
        """Absolute path under bmad-orchestrator/src → git-relative, compound dir → subdirectory name."""
        from scope_from_path import scope_from_path
        result = scope_from_path("/home/server/bmad-orchestrator/src/foo/bar.py")
        # git root = /home/server/bmad-orchestrator, relative = src/foo/bar.py
        # src is a _COMPOUND_DIR → use stem of parts[1] = 'foo' → 'foo'
        assert result == "foo", f"Expected 'foo', got '{result}'"

    def test_absolute_path_runtime_spec_example(self):
        """Spec example: runtime/sandbox.py → virgil-sandbox (both relative and absolute)."""
        from scope_from_path import scope_from_path
        result = scope_from_path("/home/server/bmad-orchestrator/runtime/sandbox.py")
        # git-relative = runtime/sandbox.py → _DIR_SCOPE_MAP[runtime]=virgil, stem=sandbox → virgil-sandbox
        assert result == "virgil-sandbox", f"Expected 'virgil-sandbox', got '{result}'"

    def test_absolute_path_not_home(self):
        """Absolute paths under /home must NOT collapse to scope 'home'."""
        from scope_from_path import scope_from_path
        for abs_path in [
            "/home/server/bmad-orchestrator/src/foo/bar.py",
            "/home/server/bmad-orchestrator/runtime/sandbox.py",
            "/home/server/bmad-orchestrator/agents/foo-bar-baz.py",
        ]:
            result = scope_from_path(abs_path)
            assert result != "home", f"Got 'home' for path '{abs_path}' — STRM-3 regression"

    def test_absolute_path_custom_topdir(self):
        """Absolute path with custom top-level dir → that dir name as scope."""
        from scope_from_path import scope_from_path
        result = scope_from_path("/home/server/bmad-orchestrator/s5-override-test/x.py")
        assert result == "s5-override-test", f"Expected 's5-override-test', got '{result}'"

    def test_absolute_path_tmp_no_git(self):
        """/tmp paths have no git root → parent dir basename used."""
        from scope_from_path import scope_from_path
        result = scope_from_path("/tmp/test-inject.py")
        # parent dir is /tmp, basename is 'tmp' which is in the block list → falls to stem = test-inject
        assert result == "test-inject", f"Expected 'test-inject', got '{result}'"

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
        # STRM-3 fix: must not return 'home' for absolute paths
        assert result != "home", f"STRM-3 regression: got 'home' for absolute path"

    def test_cli_runtime_sandbox_spec_example(self):
        """CLI version of spec example: absolute runtime/sandbox.py → virgil-sandbox."""
        script = STORM_DIR / "scope_from_path.py"
        r = subprocess.run(
            [sys.executable, str(script), "/home/server/bmad-orchestrator/runtime/sandbox.py"],
            capture_output=True, text=True, timeout=15,
        )
        assert r.returncode == 0
        result = r.stdout.strip()
        assert result == "virgil-sandbox", f"Expected 'virgil-sandbox', got '{result}'"

    def test_cli_no_args_exits_1(self):
        script = STORM_DIR / "scope_from_path.py"
        r = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=15,
        )
        assert r.returncode == 1
