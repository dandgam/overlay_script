"""Tests for patch_counter.py — 888 Storm §5.2."""

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.conftest_storm import STORM_DIR


@pytest.fixture()
def counter_tmp(tmp_path, monkeypatch):
    """Use a temp patch-counter.json."""
    import patch_counter as pc
    counter_file = tmp_path / "patch-counter.json"
    monkeypatch.setattr(pc, "PATCH_COUNTER_FILE", counter_file)
    # Also patch _common
    import _common as c
    monkeypatch.setattr(c, "PATCH_COUNTER_FILE", counter_file)
    return counter_file


class TestIncrement:
    def test_increment_creates_entry(self, counter_tmp):
        import patch_counter as pc
        pc.increment("test-scope")
        data = json.loads(counter_tmp.read_text())
        assert "test-scope" in data
        assert len(data["test-scope"]) == 1

    def test_increment_multiple(self, counter_tmp):
        import patch_counter as pc
        for _ in range(3):
            pc.increment("test-scope")
        data = json.loads(counter_tmp.read_text())
        assert len(data["test-scope"]) == 3

    def test_increment_returns_total(self, counter_tmp):
        import patch_counter as pc
        total = pc.increment("test-scope")
        assert total == 1
        total2 = pc.increment("test-scope")
        assert total2 == 2

    def test_increment_multiple_scopes(self, counter_tmp):
        import patch_counter as pc
        pc.increment("scope-a")
        pc.increment("scope-b")
        data = json.loads(counter_tmp.read_text())
        assert "scope-a" in data
        assert "scope-b" in data


class TestGetCount:
    def test_empty_scope_returns_0(self, counter_tmp):
        import patch_counter as pc
        assert pc.get_count("nonexistent-scope") == 0

    def test_count_in_window(self, counter_tmp):
        import patch_counter as pc
        for _ in range(3):
            pc.increment("scope-x")
        count = pc.get_count("scope-x", timedelta(days=14))
        assert count == 3

    def test_old_entries_excluded(self, counter_tmp):
        import patch_counter as pc
        # Manually write an old entry
        old_ts = (datetime.now(timezone.utc) - timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
        counter_tmp.write_text(json.dumps({"old-scope": [old_ts]}))
        count = pc.get_count("old-scope", timedelta(days=14))
        assert count == 0

    def test_mixed_old_and_new(self, counter_tmp):
        import patch_counter as pc
        old_ts = (datetime.now(timezone.utc) - timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
        new_ts = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        counter_tmp.write_text(json.dumps({"mixed": [old_ts, new_ts, new_ts]}))
        count = pc.get_count("mixed", timedelta(days=14))
        assert count == 2


class TestListAll:
    def test_list_all_returns_dict(self, counter_tmp):
        import patch_counter as pc
        pc.increment("alpha")
        pc.increment("beta")
        result = pc.list_all()
        assert isinstance(result, dict)
        assert "alpha" in result
        assert "beta" in result


class TestCLI:
    def test_cli_increment_and_get(self, tmp_path, monkeypatch):
        script = STORM_DIR / "patch_counter.py"
        # Use custom file via monkeypatching isn't easy for subprocess,
        # so test with the actual file but unique scope name
        scope = "cli-test-scope-999"
        # Increment
        r1 = subprocess.run(
            [sys.executable, str(script), "increment", scope],
            capture_output=True, text=True, timeout=15,
        )
        assert r1.returncode == 0
        # Get in 14d window
        r2 = subprocess.run(
            [sys.executable, str(script), "get", scope, "--window", "14d"],
            capture_output=True, text=True, timeout=15,
        )
        assert r2.returncode == 0
        assert int(r2.stdout.strip()) >= 1

    def test_cli_get_unknown_scope(self):
        script = STORM_DIR / "patch_counter.py"
        r = subprocess.run(
            [sys.executable, str(script), "get", "definitely-nonexistent-scope-xyz"],
            capture_output=True, text=True, timeout=15,
        )
        assert r.returncode == 0
        assert int(r.stdout.strip()) == 0
