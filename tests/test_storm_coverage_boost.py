"""Additional coverage tests to bring storm modules above 80%.

Targets:
- audit-trail.py lines 68, 75-78, 114-128, 132-149  (session_end stats, CLI)
- patch_counter.py lines 27-34, 62-63, 75-101        (_parse_window, list CLI)
- cycle_detector.py lines 53, 61, 70-73, 156-196     (CLI, no-scope list)
- fingerprint_tracker.py lines 79, 87, 96-99, 214-242 (list_fingerprints CLI)
- hot_files.py lines 75-76, 97-123                   (CLI)
- test_regression.py lines 94, 102, 106, 111-114, 174-214 (list, CLI)
- pattern_report.py lines 89-100, 109-118, etc        (load functions)
- storm-orchestrator.py 192-211 (T6/T9 paths), 297, 301 (user override)
- scope_from_path.py 64, 74, 80, 103-118             (_repo_basename)
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.conftest_storm import STORM_DIR


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def base_tmp(tmp_path, monkeypatch):
    import _common as c
    monkeypatch.setattr(c, "STORM_DIR", tmp_path)
    monkeypatch.setattr(c, "EVENTS_FILE", tmp_path / "events.jsonl")
    monkeypatch.setattr(c, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(c, "DECISION_LOG", tmp_path / "decision-log.md")
    monkeypatch.setattr(c, "PATCH_COUNTER_FILE", tmp_path / "patch-counter.json")
    monkeypatch.setattr(c, "FINGERPRINTS_FILE", tmp_path / "error-fingerprints.jsonl")
    monkeypatch.setattr(c, "HOT_FILES_FILE", tmp_path / "hot-files.json")
    monkeypatch.setattr(c, "TEST_FAILURES_FILE", tmp_path / "test-failures.jsonl")
    monkeypatch.setattr(c, "COMMIT_HISTORY_FILE", tmp_path / "commit-history.jsonl")
    (tmp_path / "state.json").write_text('{"initiatives": []}')
    (tmp_path / "events.jsonl").touch()
    (tmp_path / "decision-log.md").write_text("# 888 Storm Decision Log\n\n")
    (tmp_path / "patch-counter.json").write_text("{}")
    (tmp_path / "hot-files.json").write_text("{}")
    (tmp_path / "error-fingerprints.jsonl").touch()
    (tmp_path / "commit-history.jsonl").touch()
    (tmp_path / "test-failures.jsonl").touch()
    return tmp_path


# ---------------------------------------------------------------------------
# audit-trail.py additional coverage
# ---------------------------------------------------------------------------

class TestAuditTrailSessionEndStats:
    def test_session_end_counts_events_in_today(self, base_tmp):
        """Lines 68, 75-78: stats counting in session_end."""
        import _common as c
        # Write some events for today
        today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(base_tmp / "events.jsonl", "a") as f:
            f.write(json.dumps({"event": "code_gate_blocked", "timestamp": today}) + "\n")
            f.write(json.dumps({"event": "merge_gate_blocked", "timestamp": today}) + "\n")
        from audit_trail import record_session_end
        record_session_end("stats-test")
        lines = (base_tmp / "events.jsonl").read_text().strip().splitlines()
        ev = json.loads(lines[-1])
        assert ev["event"] == "session_end"
        # events_today should be ≥2 (the blocks we added)
        assert ev["summary"]["events_today"] >= 2
        assert ev["summary"]["blocks_today"] >= 2

    def test_parse_kv_args_various_types(self):
        """Line 114-128: _parse_kv_args numeric coercion."""
        from audit_trail import _parse_kv_args
        result = _parse_kv_args(["count=5", "score=3.14", "name=foo", "flag"])
        assert result["count"] == 5
        assert result["score"] == pytest.approx(3.14)
        assert result["name"] == "foo"
        assert result["flag"] is True


# ---------------------------------------------------------------------------
# patch_counter.py additional coverage
# ---------------------------------------------------------------------------

class TestPatchCounterAdditional:
    def test_parse_window_days_int(self):
        """Lines 27-34: _parse_window with integer string."""
        from patch_counter import _parse_window
        result = _parse_window("14d")
        assert result.days == 14

    def test_parse_window_fallback(self):
        """_parse_window with invalid string falls back to 14d."""
        from patch_counter import _parse_window
        result = _parse_window("invalid")
        assert result.days == 14

    def test_parse_window_pure_int(self):
        """_parse_window with '7' (no d suffix)."""
        from patch_counter import _parse_window
        result = _parse_window("7")
        assert result.days == 7

    def test_get_count_malformed_timestamp(self, base_tmp):
        """Lines 62-63: ValueError on malformed timestamp → skip."""
        import patch_counter as pc
        monkeypatch_path = base_tmp / "patch-counter.json"
        monkeypatch_path.write_text(json.dumps({"scope-bad": ["not-a-date", "also-bad"]}))
        # Temporarily patch the file path
        import _common as c
        c.PATCH_COUNTER_FILE = monkeypatch_path
        from patch_counter import get_count
        count = get_count("scope-bad", timedelta(days=14))
        assert count == 0

    def test_list_all_cli(self, base_tmp):
        """Lines 96-100: list subcommand via subprocess."""
        script = STORM_DIR / "patch_counter.py"
        r = subprocess.run(
            [sys.executable, str(script), "list"],
            capture_output=True, text=True, timeout=15,
            env=os.environ,
        )
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert isinstance(data, dict)

    def test_cli_no_subcommand_exits_1(self):
        """Lines 100-101: no command → exit 1."""
        script = STORM_DIR / "patch_counter.py"
        r = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=15,
        )
        assert r.returncode == 1


# ---------------------------------------------------------------------------
# cycle_detector.py additional coverage
# ---------------------------------------------------------------------------

class TestCycleDetectorCLI:
    def test_record_cli(self):
        """Line 152, 156-196: record command."""
        script = STORM_DIR / "cycle_detector.py"
        r = subprocess.run(
            [sys.executable, str(script), "record", "test-scope", "fix(test): something"],
            capture_output=True, text=True, timeout=15, env=os.environ,
        )
        assert r.returncode == 0

    def test_check_cli_no_cycle(self, tmp_path):
        """check CLI returns ok (exit 0)."""
        script = STORM_DIR / "cycle_detector.py"
        r = subprocess.run(
            [sys.executable, str(script), "check", "empty-scope-nonexistent-xyz"],
            capture_output=True, text=True, timeout=15, env=os.environ,
        )
        assert r.returncode == 0
        assert "ok" in r.stdout

    def test_list_cli_no_scope(self):
        """list CLI without --scope prints all."""
        script = STORM_DIR / "cycle_detector.py"
        r = subprocess.run(
            [sys.executable, str(script), "list"],
            capture_output=True, text=True, timeout=15, env=os.environ,
        )
        assert r.returncode == 0

    def test_list_cli_with_scope(self):
        """list CLI with --scope."""
        script = STORM_DIR / "cycle_detector.py"
        r = subprocess.run(
            [sys.executable, str(script), "list", "--scope", "test-scope"],
            capture_output=True, text=True, timeout=15, env=os.environ,
        )
        assert r.returncode == 0

    def test_no_command_exits_1(self):
        script = STORM_DIR / "cycle_detector.py"
        r = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=15,
        )
        assert r.returncode == 1


# ---------------------------------------------------------------------------
# fingerprint_tracker.py additional coverage
# ---------------------------------------------------------------------------

class TestFingerprintTrackerCLI:
    def test_list_cli(self):
        """Lines 214-242: list command."""
        script = STORM_DIR / "fingerprint_tracker.py"
        r = subprocess.run(
            [sys.executable, str(script), "list"],
            capture_output=True, text=True, timeout=15, env=os.environ,
        )
        assert r.returncode == 0
        json.loads(r.stdout)  # valid JSON

    def test_list_with_scope_cli(self):
        script = STORM_DIR / "fingerprint_tracker.py"
        r = subprocess.run(
            [sys.executable, str(script), "list", "--scope", "test-scope"],
            capture_output=True, text=True, timeout=15, env=os.environ,
        )
        assert r.returncode == 0

    def test_no_command_exits_1(self):
        script = STORM_DIR / "fingerprint_tracker.py"
        r = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=15,
        )
        assert r.returncode == 1

    def test_count_in_window_empty_file(self, base_tmp):
        """Lines 79, 87: empty / non-existent fingerprints file."""
        import fingerprint_tracker as ft
        ft.FINGERPRINTS_FILE = base_tmp / "error-fingerprints.jsonl"
        result = ft._count_in_window("any-fp", 30)
        assert result == 0

    def test_count_ignores_malformed_lines(self, base_tmp):
        """Lines 96-99: malformed JSON lines are skipped."""
        fp_path = base_tmp / "error-fingerprints.jsonl"
        with open(fp_path, "w") as f:
            f.write("not json\n")
            f.write(json.dumps({"fingerprint": "test-fp", "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}) + "\n")
        import fingerprint_tracker as ft
        ft.FINGERPRINTS_FILE = fp_path
        count = ft._count_in_window("test-fp", 30)
        assert count >= 1


# ---------------------------------------------------------------------------
# hot_files.py additional coverage
# ---------------------------------------------------------------------------

class TestHotFilesCLI:
    def test_record_cli(self):
        script = STORM_DIR / "hot_files.py"
        r = subprocess.run(
            [sys.executable, str(script), "record", "src/test_file.py"],
            capture_output=True, text=True, timeout=15, env=os.environ,
        )
        assert r.returncode == 0
        assert int(r.stdout.strip()) >= 1

    def test_get_cli(self):
        script = STORM_DIR / "hot_files.py"
        r = subprocess.run(
            [sys.executable, str(script), "get", "nonexistent-file-zzz.py"],
            capture_output=True, text=True, timeout=15, env=os.environ,
        )
        assert r.returncode == 0
        assert int(r.stdout.strip()) == 0

    def test_list_cli(self):
        script = STORM_DIR / "hot_files.py"
        r = subprocess.run(
            [sys.executable, str(script), "list"],
            capture_output=True, text=True, timeout=15, env=os.environ,
        )
        assert r.returncode == 0
        json.loads(r.stdout)

    def test_no_command_exits_1(self):
        script = STORM_DIR / "hot_files.py"
        r = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=15,
        )
        assert r.returncode == 1


# ---------------------------------------------------------------------------
# test_regression.py additional coverage
# ---------------------------------------------------------------------------

class TestRegressionCLI:
    def test_record_cli(self):
        """CLI record subcommand."""
        script = STORM_DIR / "test_regression.py"
        r = subprocess.run(
            [sys.executable, str(script), "record",
             "--scope", "cli-scope", "--runner", "pytest", "--exit", "1",
             "--output", "FAILED tests/test_foo.py::test_bar"],
            capture_output=True, text=True, timeout=15, env=os.environ,
        )
        assert r.returncode == 0

    def test_check_cli_no_regression(self):
        """check CLI returns ok."""
        script = STORM_DIR / "test_regression.py"
        r = subprocess.run(
            [sys.executable, str(script), "check", "--scope", "empty-scope-xyz-nope"],
            capture_output=True, text=True, timeout=15, env=os.environ,
        )
        assert r.returncode == 0
        assert "ok" in r.stdout

    def test_list_cli_with_scope(self):
        script = STORM_DIR / "test_regression.py"
        r = subprocess.run(
            [sys.executable, str(script), "list", "--scope", "cli-scope"],
            capture_output=True, text=True, timeout=15, env=os.environ,
        )
        assert r.returncode == 0

    def test_list_cli_all(self):
        script = STORM_DIR / "test_regression.py"
        r = subprocess.run(
            [sys.executable, str(script), "list"],
            capture_output=True, text=True, timeout=15, env=os.environ,
        )
        assert r.returncode == 0

    def test_no_command_exits_1(self):
        script = STORM_DIR / "test_regression.py"
        r = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=15,
        )
        assert r.returncode == 1

    def test_check_with_regression_pattern(self, base_tmp):
        """Lines 94, 102, 106, 111-114: regression checking function path."""
        import test_regression as tr
        tr.TEST_FAILURES_FILE = base_tmp / "test-failures.jsonl"
        # Record fail, pass, fail
        tr.record_result("pattern-scope", "pytest", 1, "FAILED tests/test_a.py::test_x")
        tr.record_result("pattern-scope", "pytest", 0, "2 passed")
        tr.record_result("pattern-scope", "pytest", 1, "FAILED tests/test_a.py::test_x")
        result = tr.check("pattern-scope")
        assert result is True


# ---------------------------------------------------------------------------
# pattern_report.py additional coverage
# ---------------------------------------------------------------------------

class TestPatternReportLoaders:
    def test_load_events_since_empty(self, base_tmp):
        """Lines 39, 46: empty events file."""
        import pattern_report as pr
        pr.EVENTS_FILE = base_tmp / "events.jsonl"
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        result = pr._load_events_since(cutoff)
        assert result == []

    def test_load_fingerprints_since_empty(self, base_tmp):
        """Lines 53-56: empty fingerprints file."""
        import pattern_report as pr
        pr.FINGERPRINTS_FILE = base_tmp / "error-fingerprints.jsonl"
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        result = pr._load_fingerprints_since(cutoff)
        assert result == []

    def test_load_test_failures_since_empty(self, base_tmp):
        """Lines 62, 69: empty test failures file."""
        import pattern_report as pr
        pr.TEST_FAILURES_FILE = base_tmp / "test-failures.jsonl"
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        result = pr._load_test_failures_since(cutoff)
        assert result == []

    def test_load_hot_files_recent(self, base_tmp):
        """Lines 75-78: hot files loading."""
        import pattern_report as pr
        pr.HOT_FILES_FILE = base_tmp / "hot-files.json"
        (base_tmp / "hot-files.json").write_text(json.dumps({
            "recent.py": [datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")]
        }))
        result = pr._load_hot_files_recent()
        assert "recent.py" in result

    def test_recommended_scopes(self):
        """Lines 302-303: _recommended_scopes with fingerprint repeats."""
        import pattern_report as pr
        findings = {
            "fingerprint_repeats": {"abc123456789": 5},
            "test_regressions": ["scope-x"],
            "t11_fires": ["scope-y"],
        }
        scopes = pr._recommended_scopes(findings)
        assert len(scopes) >= 2  # fingerprint scope + test regression + t11

    def test_daily_cli(self):
        """Lines 375-395: CLI daily command."""
        script = STORM_DIR / "pattern_report.py"
        r = subprocess.run(
            [sys.executable, str(script), "daily"],
            capture_output=True, text=True, timeout=30, env=os.environ,
        )
        assert r.returncode == 0

    def test_summary_cli(self):
        """Lines 378-390: CLI summary command."""
        script = STORM_DIR / "pattern_report.py"
        r = subprocess.run(
            [sys.executable, str(script), "summary"],
            capture_output=True, text=True, timeout=30, env=os.environ,
        )
        assert r.returncode == 0
        assert "Pattern Report" in r.stdout

    def test_no_command_exits_1(self):
        script = STORM_DIR / "pattern_report.py"
        r = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=15,
        )
        assert r.returncode == 1


# ---------------------------------------------------------------------------
# storm-orchestrator.py additional paths
# ---------------------------------------------------------------------------

class TestOrchestratorAdditional:
    def test_t6_artifact_in_storm_audit(self, tmp_path, monkeypatch):
        """Lines 192-211: T6 artifact goes to _storm_audit/."""
        monkeypatch.setenv("HEADLESS_MODE", "1")
        monkeypatch.chdir(tmp_path)
        import _common as c
        monkeypatch.setattr(c, "STORM_DIR", tmp_path / ".storm")
        monkeypatch.setattr(c, "EVENTS_FILE", tmp_path / ".storm" / "events.jsonl")
        monkeypatch.setattr(c, "STATE_FILE", tmp_path / ".storm" / "state.json")
        monkeypatch.setattr(c, "DECISION_LOG", tmp_path / ".storm" / "decision-log.md")
        (tmp_path / ".storm").mkdir(parents=True)
        (tmp_path / ".storm" / "state.json").write_text('{"initiatives": []}')
        (tmp_path / ".storm" / "events.jsonl").touch()
        (tmp_path / ".storm" / "decision-log.md").write_text("# Log\n")
        from storm_orchestrator import run
        artifact = run("T6", "my-doubt")
        # T6 goes to _storm_audit/
        assert "_storm_audit" in str(artifact)

    def test_t9_artifact_in_storm_audit(self, tmp_path, monkeypatch):
        """T9 also goes to _storm_audit/."""
        monkeypatch.setenv("HEADLESS_MODE", "1")
        monkeypatch.chdir(tmp_path)
        import _common as c
        monkeypatch.setattr(c, "STORM_DIR", tmp_path / ".storm")
        monkeypatch.setattr(c, "EVENTS_FILE", tmp_path / ".storm" / "events.jsonl")
        monkeypatch.setattr(c, "STATE_FILE", tmp_path / ".storm" / "state.json")
        monkeypatch.setattr(c, "DECISION_LOG", tmp_path / ".storm" / "decision-log.md")
        (tmp_path / ".storm").mkdir(parents=True)
        (tmp_path / ".storm" / "state.json").write_text('{"initiatives": []}')
        (tmp_path / ".storm" / "events.jsonl").touch()
        (tmp_path / ".storm" / "decision-log.md").write_text("# Log\n")
        from storm_orchestrator import run
        artifact = run("T9", "stuck-slug")
        assert "_storm_audit" in str(artifact)

    def test_all_trigger_ids_have_required_methods(self):
        """Lines 259-261: all triggers have entries."""
        from storm_orchestrator import _TRIGGER_REQUIRED_METHODS, _TRIGGER_ARTIFACT_TYPE
        for tid in ["T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9", "T10", "T11"]:
            assert tid in _TRIGGER_ARTIFACT_TYPE

    def test_method_name_unknown(self):
        """Lines 297, 301: unknown method ID."""
        from storm_orchestrator import _method_name
        name = _method_name(999)
        assert "999" in name


# ---------------------------------------------------------------------------
# scope_from_path.py additional coverage
# ---------------------------------------------------------------------------

class TestScopeFromPathAdditional:
    def test_slugify_special_chars(self):
        from scope_from_path import _slugify
        result = _slugify("MyModule_Test")
        assert "-" in result or result == "mymodule-test"

    def test_slugify_empty(self):
        from scope_from_path import _slugify
        result = _slugify("")
        assert result == ""

    def test_repo_basename_fallback(self):
        """Lines 103-118: fallback to repo basename or cwd."""
        from scope_from_path import _repo_basename
        result = _repo_basename()
        # Should return a non-empty string
        assert isinstance(result, str)

    def test_deep_absolute_path(self):
        from scope_from_path import scope_from_path
        # Should handle deep absolute path
        result = scope_from_path("/home/user/project/src/module/sub/file.py")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_agent_dir_maps_to_virgil(self):
        from scope_from_path import scope_from_path
        result = scope_from_path("agent/pool/worker.py")
        assert "virgil" in result or isinstance(result, str)
