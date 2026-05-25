"""Second round of coverage boosters — covers internal functions missed by subprocess tests.

Uses pytest monkeypatching + direct function invocation to cover code that
only runs in __main__ blocks or specific error paths.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


# ---------------------------------------------------------------------------
# cycle_detector.py — cover load_recent + list-without-scope
# ---------------------------------------------------------------------------

class TestCycleDetectorInternal:
    def test_load_recent_with_scope_filter(self, storm_env):
        import cycle_detector as cd
        cd.COMMIT_HISTORY_FILE = storm_env / "commit-history.jsonl"
        cd.record_commit("scope-a", "fix(scope-a): error repeated x", 70)
        cd.record_commit("scope-b", "fix(scope-b): different error", 50)
        records = cd._load_recent("scope-a", window_days=30)
        assert all(r["scope"] == "scope-a" for r in records)

    def test_load_recent_empty_file(self, storm_env):
        import cycle_detector as cd
        cd.COMMIT_HISTORY_FILE = storm_env / "commit-history.jsonl"
        result = cd._load_recent("any-scope", window_days=30)
        assert result == []

    def test_load_recent_filters_old(self, storm_env):
        import cycle_detector as cd
        cd.COMMIT_HISTORY_FILE = storm_env / "commit-history.jsonl"
        old_ts = (datetime.now(timezone.utc) - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(storm_env / "commit-history.jsonl", "w") as f:
            f.write(json.dumps({"ts": old_ts, "scope": "old-scope", "subject": "old commit", "body_len": 50}) + "\n")
        result = cd._load_recent("old-scope", window_days=30)
        assert result == []

    def test_check_fires_on_3_similar(self, storm_env):
        import cycle_detector as cd
        cd.COMMIT_HISTORY_FILE = storm_env / "commit-history.jsonl"
        cd.STORM_DIR = storm_env
        import _common as c
        c.EVENTS_FILE = storm_env / "events.jsonl"
        c.STATE_FILE = storm_env / "state.json"
        for i in range(3):
            cd.record_commit("cycle-scope", f"fix(cycle-scope): stdin blocking issue variant {i+1}", 70)
        result = cd.check("cycle-scope")
        assert result is True


# ---------------------------------------------------------------------------
# fingerprint_tracker.py — list_fingerprints with data
# ---------------------------------------------------------------------------

class TestFingerprintListInternal:
    def test_list_fingerprints_with_data(self, storm_env):
        import fingerprint_tracker as ft
        ft.FINGERPRINTS_FILE = storm_env / "error-fingerprints.jsonl"
        ft.STORM_DIR = storm_env
        import _common as c
        c.EVENTS_FILE = storm_env / "events.jsonl"
        c.STATE_FILE = storm_env / "state.json"
        ft.record("scope-x", "RuntimeError: something")
        result = ft.list_fingerprints("scope-x")
        assert len(result) == 1

    def test_list_all_fingerprints(self, storm_env):
        import fingerprint_tracker as ft
        ft.FINGERPRINTS_FILE = storm_env / "error-fingerprints.jsonl"
        ft.STORM_DIR = storm_env
        import _common as c
        c.EVENTS_FILE = storm_env / "events.jsonl"
        c.STATE_FILE = storm_env / "state.json"
        ft.record("scope-a", "RuntimeError: error a")
        ft.record("scope-b", "ValueError: error b")
        result = ft.list_fingerprints()  # no scope filter
        assert len(result) == 2


# ---------------------------------------------------------------------------
# hot_files.py — _count_in_window directly
# ---------------------------------------------------------------------------

class TestHotFilesInternal:
    def test_count_in_window_direct(self, storm_env):
        import hot_files as hf
        hf.HOT_FILES_FILE = storm_env / "hot-files.json"
        hf.STORM_DIR = storm_env
        timestamps = [
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        ]
        count = hf._count_in_window(timestamps, window_days=14)
        assert count == 2

    def test_count_in_window_old_excluded(self, storm_env):
        import hot_files as hf
        old_ts = (datetime.now(timezone.utc) - timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
        count = hf._count_in_window([old_ts, old_ts])
        assert count == 0

    def test_list_hot_multiple_files(self, storm_env):
        import hot_files as hf
        hf.HOT_FILES_FILE = storm_env / "hot-files.json"
        hf.STORM_DIR = storm_env
        import _common as c
        c.EVENTS_FILE = storm_env / "events.jsonl"
        c.STATE_FILE = storm_env / "state.json"
        for _ in range(6):
            hf.record_edit("a.py")
        for _ in range(3):
            hf.record_edit("b.py")
        result = hf.list_hot(threshold=5)
        assert "a.py" in result
        assert "b.py" not in result


# ---------------------------------------------------------------------------
# test_regression.py — _load_recent_scope + list internal
# ---------------------------------------------------------------------------

class TestRegressionInternal:
    def test_load_recent_scope_with_data(self, storm_env):
        import test_regression as tr
        tr.TEST_FAILURES_FILE = storm_env / "test-failures.jsonl"
        tr.STORM_DIR = storm_env
        import _common as c
        c.EVENTS_FILE = storm_env / "events.jsonl"
        c.STATE_FILE = storm_env / "state.json"
        tr.record_result("my-scope", "pytest", 1, "FAILED test_a.py::test_foo")
        records = tr._load_recent_scope("my-scope")
        assert len(records) == 1
        assert records[0]["scope"] == "my-scope"

    def test_load_recent_scope_filters_old(self, storm_env):
        import test_regression as tr
        tr.TEST_FAILURES_FILE = storm_env / "test-failures.jsonl"
        old_ts = (datetime.now(timezone.utc) - timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(storm_env / "test-failures.jsonl", "w") as f:
            f.write(json.dumps({
                "ts": old_ts, "scope": "old-scope", "runner": "pytest",
                "exit_code": 1, "result": "failed", "failed_tests": []
            }) + "\n")
        tr.TEST_FAILURES_FILE = storm_env / "test-failures.jsonl"
        records = tr._load_recent_scope("old-scope")
        assert records == []

    def test_check_fires_regression_event(self, storm_env):
        import test_regression as tr
        tr.TEST_FAILURES_FILE = storm_env / "test-failures.jsonl"
        tr.STORM_DIR = storm_env
        import _common as c
        c.EVENTS_FILE = storm_env / "events.jsonl"
        c.STATE_FILE = storm_env / "state.json"
        # fail → pass → fail
        tr.record_result("reg-scope", "pytest", 1, "FAILED tests/test_x.py::test_foo")
        tr.record_result("reg-scope", "pytest", 0, "1 passed")
        tr.record_result("reg-scope", "pytest", 1, "FAILED tests/test_x.py::test_foo")
        result = tr.check("reg-scope")
        assert result is True
        events = (storm_env / "events.jsonl").read_text()
        assert "regression_detected" in events


# ---------------------------------------------------------------------------
# patch_counter.py — get_count with bad data
# ---------------------------------------------------------------------------

class TestPatchCounterEdgeCases:
    def test_get_count_empty_list(self, storm_env):
        import _common as c
        c.PATCH_COUNTER_FILE = storm_env / "patch-counter.json"
        (storm_env / "patch-counter.json").write_text(json.dumps({"empty": []}))
        import patch_counter as pc
        pc.PATCH_COUNTER_FILE = storm_env / "patch-counter.json"
        assert pc.get_count("empty") == 0

    def test_list_all_empty(self, storm_env):
        import patch_counter as pc
        pc.PATCH_COUNTER_FILE = storm_env / "patch-counter.json"
        result = pc.list_all()
        assert result == {}

    def test_increment_reads_existing(self, storm_env):
        import patch_counter as pc
        pc.PATCH_COUNTER_FILE = storm_env / "patch-counter.json"
        pc.increment("my-scope")
        pc.increment("my-scope")
        count = pc.get_count("my-scope")
        assert count == 2


# ---------------------------------------------------------------------------
# audit-trail.py — _parse_kv_args + more session_end paths
# ---------------------------------------------------------------------------

class TestAuditTrailEdgeCases:
    def test_session_end_no_events_file(self, storm_env):
        """Lines 75-78: events file doesn't exist yet."""
        import _common as c
        c.EVENTS_FILE = storm_env / "no-events.jsonl"
        c.STATE_FILE = storm_env / "state.json"
        c.DECISION_LOG = storm_env / "decision-log.md"
        from audit_trail import record_session_end
        # Should not raise
        record_session_end("no-events-session")
        assert (storm_env / "no-events.jsonl").exists()

    def test_record_event_creates_decision_log(self, storm_env):
        """Lines 132-149: _append_decision_log."""
        import _common as c
        c.EVENTS_FILE = storm_env / "events.jsonl"
        c.STATE_FILE = storm_env / "state.json"
        c.DECISION_LOG = storm_env / "decision-log.md"
        c.record_event("storm_completed", {
            "trigger_id": "T2",
            "slug": "test-agent",
            "rationale": "test decision",
        })
        log = (storm_env / "decision-log.md").read_text()
        assert "storm_completed" in log


# ---------------------------------------------------------------------------
# scope_from_path.py — compound dirs
# ---------------------------------------------------------------------------

class TestScopeAdditional:
    def test_compound_dir_src_with_sub(self):
        from scope_from_path import scope_from_path
        result = scope_from_path("src/dag_planner/optimizer.py")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_compound_dir_tests_with_sub(self):
        from scope_from_path import scope_from_path
        result = scope_from_path("tests/test_worker.py")
        assert isinstance(result, str)

    def test_top_level_unknown_dir(self):
        from scope_from_path import scope_from_path
        result = scope_from_path("config/settings.yaml")
        assert isinstance(result, str)

    def test_storm_dir_maps(self):
        from scope_from_path import scope_from_path
        result = scope_from_path("storm/fingerprint_tracker.py")
        # storm → compound, sub=fingerprint_tracker
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# pattern_report.py — loaders with actual data + load_hot with malformed
# ---------------------------------------------------------------------------

class TestPatternReportInternal:
    def test_load_events_since_with_data(self, storm_env):
        import pattern_report as pr
        pr.EVENTS_FILE = storm_env / "events.jsonl"
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(storm_env / "events.jsonl", "w") as f:
            f.write(json.dumps({"event": "storm_started", "timestamp": ts}) + "\n")
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        result = pr._load_events_since(cutoff)
        assert len(result) >= 1

    def test_load_fingerprints_since_with_data(self, storm_env):
        import pattern_report as pr
        pr.FINGERPRINTS_FILE = storm_env / "error-fingerprints.jsonl"
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(storm_env / "error-fingerprints.jsonl", "w") as f:
            f.write(json.dumps({"fingerprint": "abc", "ts": ts, "scope": "s"}) + "\n")
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        result = pr._load_fingerprints_since(cutoff)
        assert len(result) >= 1

    def test_load_test_failures_since_with_data(self, storm_env):
        import pattern_report as pr
        pr.TEST_FAILURES_FILE = storm_env / "test-failures.jsonl"
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(storm_env / "test-failures.jsonl", "w") as f:
            f.write(json.dumps({"ts": ts, "scope": "s", "result": "failed", "failed_tests": []}) + "\n")
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        result = pr._load_test_failures_since(cutoff)
        assert len(result) >= 1

    def test_analyze_detects_test_regression(self, storm_env):
        import pattern_report as pr
        pr.TEST_FAILURES_FILE = storm_env / "test-failures.jsonl"
        pr.EVENTS_FILE = storm_env / "events.jsonl"
        pr.FINGERPRINTS_FILE = storm_env / "error-fingerprints.jsonl"
        pr.HOT_FILES_FILE = storm_env / "hot-files.json"
        pr.COMMIT_HISTORY_FILE = storm_env / "commit-history.jsonl"
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # fail → pass → fail pattern
        with open(storm_env / "test-failures.jsonl", "w") as f:
            for result in ["failed", "passed", "failed"]:
                f.write(json.dumps({"ts": ts, "scope": "test-scope", "result": result, "failed_tests": ["test_x"]}) + "\n")
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        findings = pr.analyze(cutoff)
        assert "test-scope" in findings["test_regressions"]

    def test_top3_includes_test_regression(self, storm_env):
        import pattern_report as pr
        findings = {
            "fingerprint_repeats": {},
            "hot_files": {},
            "test_regressions": ["scope-x"],
            "blocks": {"total": 0, "by_scope": {}},
            "t11_fires": [],
            "storms_run": {},
            "event_breakdown": {},
        }
        patterns = pr._top3_patterns(findings)
        assert any(p["type"] == "test_regression" for p in patterns)
