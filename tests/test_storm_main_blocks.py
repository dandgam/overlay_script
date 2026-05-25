"""Tests that exercise __main__ blocks via sys.argv injection.

This brings CLI code lines into coverage measurement since the code
runs in-process. Each test patches sys.argv then calls the module's
main block through importlib exec_module trick.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest_storm import STORM_DIR


@pytest.fixture()
def storm_env(tmp_path, monkeypatch):
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


def _run_main(module_name: str, argv: list[str]) -> None:
    """Run a module's main block with patched sys.argv via runpy.run_path.

    May raise SystemExit (from argparse help or explicit sys.exit()).
    Callers should use pytest.raises(SystemExit) or contextlib.suppress.
    """
    import runpy
    module_path = str(STORM_DIR / module_name)
    with patch.object(sys, "argv", [module_path] + argv):
        runpy.run_path(module_path, run_name="__main__")


# ---------------------------------------------------------------------------
# patch_counter.py __main__ block
# ---------------------------------------------------------------------------

class TestPatchCounterMain:
    def test_increment_via_main(self, storm_env):
        import patch_counter as pc
        pc.PATCH_COUNTER_FILE = storm_env / "patch-counter.json"
        import _common as c
        c.PATCH_COUNTER_FILE = storm_env / "patch-counter.json"
        # Normal path: no SystemExit
        _run_main("patch_counter.py", ["increment", "main-scope"])

    def test_get_via_main(self, storm_env):
        import patch_counter as pc
        pc.PATCH_COUNTER_FILE = storm_env / "patch-counter.json"
        import _common as c
        c.PATCH_COUNTER_FILE = storm_env / "patch-counter.json"
        pc.increment("main-get-scope")
        _run_main("patch_counter.py", ["get", "main-get-scope", "--window", "14d"])

    def test_list_via_main(self, storm_env):
        import _common as c
        c.PATCH_COUNTER_FILE = storm_env / "patch-counter.json"
        _run_main("patch_counter.py", ["list"])

    def test_no_cmd_via_main(self):
        with pytest.raises(SystemExit) as exc:
            _run_main("patch_counter.py", [])
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# hot_files.py __main__ block
# ---------------------------------------------------------------------------

class TestHotFilesMain:
    def test_record_via_main(self, storm_env):
        import _common as c
        c.EVENTS_FILE = storm_env / "events.jsonl"
        c.STATE_FILE = storm_env / "state.json"
        c.HOT_FILES_FILE = storm_env / "hot-files.json"
        _run_main("hot_files.py", ["record", "src/main.py"])

    def test_get_via_main(self, storm_env):
        import _common as c
        c.HOT_FILES_FILE = storm_env / "hot-files.json"
        _run_main("hot_files.py", ["get", "some-file.py"])

    def test_list_via_main(self, storm_env):
        import _common as c
        c.HOT_FILES_FILE = storm_env / "hot-files.json"
        _run_main("hot_files.py", ["list"])

    def test_no_cmd_via_main(self):
        with pytest.raises(SystemExit) as exc:
            _run_main("hot_files.py", [])
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# cycle_detector.py __main__ block
# ---------------------------------------------------------------------------

class TestCycleDetectorMain:
    def test_record_via_main(self, storm_env):
        import _common as c
        c.COMMIT_HISTORY_FILE = storm_env / "commit-history.jsonl"
        _run_main("cycle_detector.py", ["record", "main-scope", "fix: test subject"])

    def test_check_ok_via_main(self, storm_env):
        import _common as c
        c.COMMIT_HISTORY_FILE = storm_env / "commit-history.jsonl"
        c.EVENTS_FILE = storm_env / "events.jsonl"
        c.STATE_FILE = storm_env / "state.json"
        # Empty scope → exit 0
        with pytest.raises(SystemExit) as exc:
            _run_main("cycle_detector.py", ["check", "empty-scope-main"])
        assert exc.value.code == 0

    def test_list_no_scope_via_main(self, storm_env):
        import _common as c
        c.COMMIT_HISTORY_FILE = storm_env / "commit-history.jsonl"
        _run_main("cycle_detector.py", ["list"])

    def test_list_with_scope_via_main(self, storm_env):
        import _common as c
        c.COMMIT_HISTORY_FILE = storm_env / "commit-history.jsonl"
        _run_main("cycle_detector.py", ["list", "--scope", "main-scope"])

    def test_no_cmd_via_main(self):
        with pytest.raises(SystemExit) as exc:
            _run_main("cycle_detector.py", [])
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# fingerprint_tracker.py __main__ block
# ---------------------------------------------------------------------------

class TestFingerprintMain:
    def test_record_via_main(self, storm_env):
        import _common as c
        c.FINGERPRINTS_FILE = storm_env / "error-fingerprints.jsonl"
        c.EVENTS_FILE = storm_env / "events.jsonl"
        c.STATE_FILE = storm_env / "state.json"
        _run_main("fingerprint_tracker.py", ["record", "--scope", "main-scope", "--stderr", "RuntimeError: test"])

    def test_inject_via_main(self, storm_env):
        import _common as c
        c.FINGERPRINTS_FILE = storm_env / "error-fingerprints.jsonl"
        c.EVENTS_FILE = storm_env / "events.jsonl"
        c.STATE_FILE = storm_env / "state.json"
        _run_main("fingerprint_tracker.py", ["inject", "main-test-fp"])

    def test_list_via_main(self, storm_env):
        import _common as c
        c.FINGERPRINTS_FILE = storm_env / "error-fingerprints.jsonl"
        _run_main("fingerprint_tracker.py", ["list"])

    def test_no_cmd_via_main(self):
        with pytest.raises(SystemExit) as exc:
            _run_main("fingerprint_tracker.py", [])
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# test_regression.py __main__ block
# ---------------------------------------------------------------------------

class TestRegressionMain:
    def test_record_via_main(self, storm_env):
        import _common as c
        c.TEST_FAILURES_FILE = storm_env / "test-failures.jsonl"
        c.EVENTS_FILE = storm_env / "events.jsonl"
        c.STATE_FILE = storm_env / "state.json"
        _run_main("test_regression.py", [
            "record", "--scope", "main-scope", "--runner", "pytest",
            "--exit", "0", "--output", "1 passed"
        ])

    def test_check_via_main(self, storm_env):
        import _common as c
        c.TEST_FAILURES_FILE = storm_env / "test-failures.jsonl"
        c.EVENTS_FILE = storm_env / "events.jsonl"
        c.STATE_FILE = storm_env / "state.json"
        with pytest.raises(SystemExit) as exc:
            _run_main("test_regression.py", ["check", "--scope", "empty-scope-main"])
        assert exc.value.code == 0

    def test_list_with_scope_via_main(self, storm_env):
        import _common as c
        c.TEST_FAILURES_FILE = storm_env / "test-failures.jsonl"
        _run_main("test_regression.py", ["list", "--scope", "main-scope"])

    def test_list_all_via_main(self, storm_env):
        import _common as c
        c.TEST_FAILURES_FILE = storm_env / "test-failures.jsonl"
        _run_main("test_regression.py", ["list"])

    def test_no_cmd_via_main(self):
        with pytest.raises(SystemExit) as exc:
            _run_main("test_regression.py", [])
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# audit-trail.py __main__ block + specific paths
# ---------------------------------------------------------------------------

class TestAuditTrailMain:
    def test_session_end_via_main(self, storm_env):
        import _common as c
        c.EVENTS_FILE = storm_env / "events.jsonl"
        c.STATE_FILE = storm_env / "state.json"
        c.DECISION_LOG = storm_env / "decision-log.md"
        _run_main("audit-trail.py", ["session_end", "main-session"])

    def test_generic_event_via_main(self, storm_env):
        import _common as c
        c.EVENTS_FILE = storm_env / "events.jsonl"
        c.STATE_FILE = storm_env / "state.json"
        _run_main("audit-trail.py", ["intent_detected", "scope=test"])

    def test_session_end_with_existing_blocks(self, storm_env):
        """Lines 68, 75-78: count blocks in today's events."""
        import _common as c
        today_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Write some blocked events
        with open(storm_env / "events.jsonl", "a") as f:
            f.write(json.dumps({"event": "code_gate_blocked", "timestamp": today_ts}) + "\n")
            f.write(json.dumps({"event": "merge_gate_blocked", "timestamp": today_ts}) + "\n")
            f.write(json.dumps({"event": "storm_started", "timestamp": today_ts}) + "\n")
        from audit_trail import record_session_end
        record_session_end("test-blocks-session")
        events = (storm_env / "events.jsonl").read_text().strip().splitlines()
        last_ev = json.loads(events[-1])
        assert last_ev["event"] == "session_end"
        assert last_ev["summary"]["events_today"] >= 3
        assert last_ev["summary"]["blocks_today"] >= 2


# ---------------------------------------------------------------------------
# scope_from_path.py — edge cases for coverage
# ---------------------------------------------------------------------------

class TestScopeFromPathEdge:
    def test_runtime_with_file(self):
        from scope_from_path import scope_from_path
        result = scope_from_path("runtime/sandbox.py")
        assert result.startswith("virgil")

    def test_runtime_subdir(self):
        from scope_from_path import scope_from_path
        result = scope_from_path("runtime/worker/pool.py")
        assert "virgil" in result

    def test_agent_with_file(self):
        from scope_from_path import scope_from_path
        result = scope_from_path("agent/planner.py")
        assert "virgil" in result

    def test_worker_with_file(self):
        from scope_from_path import scope_from_path
        result = scope_from_path("worker/executor.py")
        assert "virgil" in result

    def test_non_compound_dir(self):
        from scope_from_path import scope_from_path
        result = scope_from_path("docs/README.md")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_git_root_integration(self):
        """Test _repo_basename returns valid string in this repo."""
        from scope_from_path import _repo_basename
        result = _repo_basename()
        assert isinstance(result, str)
        # Should detect bmad-orchestrator or similar
        assert len(result) > 0


# ---------------------------------------------------------------------------
# intent-classifier.py __main__ block
# ---------------------------------------------------------------------------

class TestIntentClassifierMain:
    def test_t1_via_main(self):
        # No SystemExit on success
        _run_main("intent-classifier.py", ["хочу внедрить новую фичу"])

    def test_no_args_via_main(self):
        with pytest.raises(SystemExit) as exc:
            _run_main("intent-classifier.py", [])
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# storm-orchestrator.py — T10, T11 triggers
# ---------------------------------------------------------------------------

class TestOrchestratorEdgeTriggers:
    def test_t10_artifact(self, storm_env, monkeypatch):
        monkeypatch.setenv("HEADLESS_MODE", "1")
        monkeypatch.chdir(storm_env)
        import _common as c
        c.STATE_FILE = storm_env / "state.json"
        c.EVENTS_FILE = storm_env / "events.jsonl"
        c.DECISION_LOG = storm_env / "decision-log.md"
        from storm_orchestrator import run
        artifact = run("T10", "dangerous-op")
        assert artifact.exists()

    def test_t11_artifact(self, storm_env, monkeypatch):
        monkeypatch.setenv("HEADLESS_MODE", "1")
        monkeypatch.chdir(storm_env)
        import _common as c
        c.STATE_FILE = storm_env / "state.json"
        c.EVENTS_FILE = storm_env / "events.jsonl"
        c.DECISION_LOG = storm_env / "decision-log.md"
        from storm_orchestrator import run
        artifact = run("T11", "abc123")
        assert artifact.exists()
        content = artifact.read_text()
        assert "T11" in content

    def test_t4_t5_t8_triggers(self, storm_env, monkeypatch):
        monkeypatch.setenv("HEADLESS_MODE", "1")
        monkeypatch.chdir(storm_env)
        import _common as c
        c.STATE_FILE = storm_env / "state.json"
        c.EVENTS_FILE = storm_env / "events.jsonl"
        c.DECISION_LOG = storm_env / "decision-log.md"
        from storm_orchestrator import run
        for tid, slug in [("T4", "compare-a"), ("T5", "improve-b"), ("T8", "merge-c")]:
            artifact = run(tid, slug)
            assert artifact.exists()

    def test_method_name_known_ids(self):
        from storm_orchestrator import _method_name
        assert "First Principles" in _method_name(39)
        assert "Pre-mortem" in _method_name(34)
        assert "5 Whys" in _method_name(40)
