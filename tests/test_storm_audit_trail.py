"""Tests for audit-trail.py — 888 Storm §6.4."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest_storm import STORM_DIR


@pytest.fixture()
def audit_tmp(tmp_path, monkeypatch):
    """Redirect all storm paths to tmp_path."""
    import _common as c
    monkeypatch.setattr(c, "STORM_DIR", tmp_path)
    monkeypatch.setattr(c, "EVENTS_FILE", tmp_path / "events.jsonl")
    monkeypatch.setattr(c, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(c, "DECISION_LOG", tmp_path / "decision-log.md")
    (tmp_path / "state.json").write_text('{"initiatives": []}')
    (tmp_path / "decision-log.md").write_text("# 888 Storm Decision Log\n\n")
    (tmp_path / "events.jsonl").touch()
    return tmp_path


class TestRecordSessionEnd:
    def test_session_end_appended(self, audit_tmp):
        from audit_trail import record_session_end
        record_session_end("test-session-123")
        events = (audit_tmp / "events.jsonl").read_text().strip().splitlines()
        assert len(events) >= 1
        ev = json.loads(events[-1])
        assert ev["event"] == "session_end"
        assert ev["session_id"] == "test-session-123"
        assert "summary" in ev

    def test_session_end_has_timestamp(self, audit_tmp):
        from audit_trail import record_session_end
        record_session_end("sess-x")
        events = (audit_tmp / "events.jsonl").read_text().strip().splitlines()
        ev = json.loads(events[-1])
        assert "timestamp" in ev
        assert ev["timestamp"].endswith("Z")


class TestEnsureInit:
    def test_creates_missing_state(self, tmp_path, monkeypatch):
        import _common as c
        monkeypatch.setattr(c, "STORM_DIR", tmp_path)
        monkeypatch.setattr(c, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr(c, "EVENTS_FILE", tmp_path / "events.jsonl")
        monkeypatch.setattr(c, "DECISION_LOG", tmp_path / "decision-log.md")
        from audit_trail import _ensure_init
        _ensure_init()
        assert (tmp_path / "state.json").exists()
        assert (tmp_path / "events.jsonl").exists()
        assert (tmp_path / "decision-log.md").exists()

    def test_ensure_init_idempotent(self, audit_tmp, monkeypatch):
        import _common as c
        monkeypatch.setattr(c, "STORM_DIR", audit_tmp)
        monkeypatch.setattr(c, "STATE_FILE", audit_tmp / "state.json")
        monkeypatch.setattr(c, "EVENTS_FILE", audit_tmp / "events.jsonl")
        monkeypatch.setattr(c, "DECISION_LOG", audit_tmp / "decision-log.md")
        from audit_trail import _ensure_init
        _ensure_init()
        _ensure_init()  # second call should be noop
        state = json.loads((audit_tmp / "state.json").read_text())
        assert "initiatives" in state


class TestKnownEvents:
    def test_all_event_types_present(self):
        from audit_trail import KNOWN_EVENTS
        expected = {
            "session_end", "storm_started", "storm_completed",
            "code_gate_blocked", "merge_gate_blocked",
            "storm_method_selection", "regression_detected",
        }
        assert expected.issubset(KNOWN_EVENTS)


class TestCLI:
    def test_cli_session_end(self, tmp_path):
        """CLI audit-trail.py session_end <id> should work."""
        script = STORM_DIR / "audit-trail.py"
        r = subprocess.run(
            [sys.executable, str(script), "session_end", "test-cli-session"],
            capture_output=True, text=True, timeout=15,
        )
        assert r.returncode == 0

    def test_cli_no_args_exits_1(self):
        script = STORM_DIR / "audit-trail.py"
        r = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=15,
        )
        assert r.returncode == 1

    def test_cli_generic_event(self):
        script = STORM_DIR / "audit-trail.py"
        r = subprocess.run(
            [sys.executable, str(script), "intent_detected", "scope=test", "triggers=T1"],
            capture_output=True, text=True, timeout=15,
        )
        assert r.returncode == 0
