"""Tests for hot_files.py — 888 Storm §19 R2."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.conftest_storm import STORM_DIR


@pytest.fixture()
def hf_tmp(tmp_path, monkeypatch):
    import _common as c
    monkeypatch.setattr(c, "STORM_DIR", tmp_path)
    monkeypatch.setattr(c, "HOT_FILES_FILE", tmp_path / "hot-files.json")
    monkeypatch.setattr(c, "EVENTS_FILE", tmp_path / "events.jsonl")
    monkeypatch.setattr(c, "STATE_FILE", tmp_path / "state.json")
    (tmp_path / "state.json").write_text('{"initiatives": []}')
    (tmp_path / "events.jsonl").touch()
    (tmp_path / "hot-files.json").write_text("{}")

    import hot_files as hf
    monkeypatch.setattr(hf, "HOT_FILES_FILE", tmp_path / "hot-files.json")
    monkeypatch.setattr(hf, "STORM_DIR", tmp_path)
    return tmp_path


class TestRecordEdit:
    def test_record_increments(self, hf_tmp):
        from hot_files import record_edit
        count = record_edit("src/worker.py")
        assert count == 1

    def test_record_multiple(self, hf_tmp):
        from hot_files import record_edit
        for _ in range(5):
            record_edit("src/worker.py")
        from hot_files import get_count
        assert get_count("src/worker.py") == 5

    def test_t11_fired_at_threshold(self, hf_tmp):
        from hot_files import record_edit, _HOT_THRESHOLD
        for _ in range(_HOT_THRESHOLD + 1):
            record_edit("hot-file.py")
        events = (hf_tmp / "events.jsonl").read_text()
        assert "hot_file_detected" in events

    def test_no_t11_below_threshold(self, hf_tmp):
        from hot_files import record_edit, _HOT_THRESHOLD
        for _ in range(_HOT_THRESHOLD - 1):
            record_edit("cool-file.py")
        events = (hf_tmp / "events.jsonl").read_text()
        assert "hot_file_detected" not in events

    def test_different_files_tracked_separately(self, hf_tmp):
        from hot_files import record_edit, get_count
        record_edit("file-a.py")
        record_edit("file-b.py")
        assert get_count("file-a.py") == 1
        assert get_count("file-b.py") == 1


class TestGetCount:
    def test_unknown_file_returns_0(self, hf_tmp):
        from hot_files import get_count
        assert get_count("nonexistent-file.py") == 0

    def test_old_entries_excluded(self, hf_tmp):
        old_ts = (datetime.now(timezone.utc) - timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
        (hf_tmp / "hot-files.json").write_text(json.dumps({"old-file.py": [old_ts, old_ts]}))
        from hot_files import get_count
        assert get_count("old-file.py") == 0


class TestListHot:
    def test_list_returns_above_threshold(self, hf_tmp):
        from hot_files import record_edit, list_hot
        for _ in range(6):
            record_edit("hot.py")
        for _ in range(2):
            record_edit("cool.py")
        result = list_hot(threshold=5)
        assert "hot.py" in result
        assert "cool.py" not in result

    def test_list_empty_when_no_hot(self, hf_tmp):
        from hot_files import list_hot
        result = list_hot(threshold=5)
        assert result == {}
