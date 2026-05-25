"""Tests for _common.py — shared helpers for 888 Storm Framework."""

import json
from pathlib import Path

import pytest

from tests.conftest_storm import STORM_DIR  # noqa: F401


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def storm_tmp(tmp_path: Path, monkeypatch):
    """Redirect all STORM_DIR references to a temp directory."""
    import _common as c
    monkeypatch.setattr(c, "STORM_DIR", tmp_path)
    monkeypatch.setattr(c, "EVENTS_FILE", tmp_path / "events.jsonl")
    monkeypatch.setattr(c, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(c, "DECISION_LOG", tmp_path / "decision-log.md")
    monkeypatch.setattr(c, "PATCH_COUNTER_FILE", tmp_path / "patch-counter.json")
    monkeypatch.setattr(c, "FINGERPRINTS_FILE", tmp_path / "error-fingerprints.jsonl")
    monkeypatch.setattr(c, "HOT_FILES_FILE", tmp_path / "hot-files.json")
    monkeypatch.setattr(c, "COMMIT_HISTORY_FILE", tmp_path / "commit-history.jsonl")
    monkeypatch.setattr(c, "TEST_FAILURES_FILE", tmp_path / "test-failures.jsonl")
    return tmp_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLoadSaveJson:
    def test_load_missing_returns_default(self, tmp_path):
        import _common as c
        result = c.load_json(tmp_path / "missing.json", default={"a": 1})
        assert result == {"a": 1}

    def test_save_and_load_roundtrip(self, tmp_path):
        import _common as c
        path = tmp_path / "test.json"
        data = {"foo": "bar", "n": 42}
        c.save_json(path, data)
        result = c.load_json(path)
        assert result == data

    def test_load_corrupt_returns_default(self, tmp_path):
        import _common as c
        path = tmp_path / "corrupt.json"
        path.write_text("not json{{{", encoding="utf-8")
        result = c.load_json(path, default=[])
        assert result == []

    def test_save_creates_parents(self, tmp_path):
        import _common as c
        path = tmp_path / "deep" / "nested" / "file.json"
        c.save_json(path, {"x": 1})
        assert path.exists()


class TestAppendJsonl:
    def test_append_creates_file(self, tmp_path):
        import _common as c
        path = tmp_path / "events.jsonl"
        c.append_jsonl(path, {"event": "test"})
        assert path.exists()

    def test_append_multiple(self, tmp_path):
        import _common as c
        path = tmp_path / "events.jsonl"
        for i in range(5):
            c.append_jsonl(path, {"i": i})
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 5
        assert json.loads(lines[0])["i"] == 0
        assert json.loads(lines[4])["i"] == 4

    def test_append_idempotent_format(self, tmp_path):
        import _common as c
        path = tmp_path / "events.jsonl"
        c.append_jsonl(path, {"event": "foo", "val": "bar"})
        line = path.read_text().strip()
        parsed = json.loads(line)
        assert parsed["event"] == "foo"


class TestRecordEvent:
    def test_record_updates_events_jsonl(self, storm_tmp):
        import _common as c
        c.record_event("test_event", {"scope": "foo"})
        lines = (storm_tmp / "events.jsonl").read_text().strip().splitlines()
        assert len(lines) >= 1
        ev = json.loads(lines[-1])
        assert ev["event"] == "test_event"
        assert ev["scope"] == "foo"
        assert "timestamp" in ev

    def test_record_updates_state_json(self, storm_tmp):
        import _common as c
        # Initialize state first
        c.save_json(storm_tmp / "state.json", {"initiatives": []})
        c.record_event("test_event", {"scope": "foo"})
        state = c.load_json(storm_tmp / "state.json")
        assert "last_event" in state
        assert state["last_event"]["type"] == "test_event"

    def test_decision_event_appends_to_log(self, storm_tmp):
        import _common as c
        # Initialize decision log
        (storm_tmp / "decision-log.md").write_text("# Log\n\n")
        (storm_tmp / "state.json").write_text('{"initiatives": []}')
        c.record_event(
            "storm_method_selection",
            {"trigger_id": "T1", "slug": "test", "rationale": "static rules matched"},
        )
        log = (storm_tmp / "decision-log.md").read_text()
        assert "storm_method_selection" in log

    def test_non_decision_event_no_log(self, storm_tmp):
        import _common as c
        log_path = storm_tmp / "decision-log.md"
        log_path.write_text("# Log\n\n")
        (storm_tmp / "state.json").write_text('{"initiatives": []}')
        c.record_event("intent_detected", {"triggers": ["T1"]})
        log = log_path.read_text()
        # Should not have been appended
        assert "intent_detected" not in log


class TestNowIso:
    def test_format(self):
        import _common as c
        ts = c.now_iso()
        assert ts.endswith("Z")
        assert len(ts) == 20  # YYYY-MM-DDTHH:MM:SSZ
