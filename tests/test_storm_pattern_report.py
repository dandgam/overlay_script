"""Tests for pattern_report.py — 888 Storm §19 R5."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.conftest_storm import STORM_DIR


@pytest.fixture()
def pr_tmp(tmp_path, monkeypatch):
    import _common as c
    monkeypatch.setattr(c, "STORM_DIR", tmp_path)
    monkeypatch.setattr(c, "EVENTS_FILE", tmp_path / "events.jsonl")
    monkeypatch.setattr(c, "FINGERPRINTS_FILE", tmp_path / "error-fingerprints.jsonl")
    monkeypatch.setattr(c, "HOT_FILES_FILE", tmp_path / "hot-files.json")
    monkeypatch.setattr(c, "TEST_FAILURES_FILE", tmp_path / "test-failures.jsonl")
    monkeypatch.setattr(c, "COMMIT_HISTORY_FILE", tmp_path / "commit-history.jsonl")
    monkeypatch.setattr(c, "STATE_FILE", tmp_path / "state.json")
    (tmp_path / "state.json").write_text('{"initiatives": []}')
    (tmp_path / "events.jsonl").touch()
    (tmp_path / "error-fingerprints.jsonl").touch()
    (tmp_path / "hot-files.json").write_text("{}")
    (tmp_path / "test-failures.jsonl").touch()
    (tmp_path / "commit-history.jsonl").touch()

    import pattern_report as pr
    monkeypatch.setattr(pr, "EVENTS_FILE", tmp_path / "events.jsonl")
    monkeypatch.setattr(pr, "FINGERPRINTS_FILE", tmp_path / "error-fingerprints.jsonl")
    monkeypatch.setattr(pr, "HOT_FILES_FILE", tmp_path / "hot-files.json")
    monkeypatch.setattr(pr, "TEST_FAILURES_FILE", tmp_path / "test-failures.jsonl")
    monkeypatch.setattr(pr, "COMMIT_HISTORY_FILE", tmp_path / "commit-history.jsonl")
    monkeypatch.setattr(pr, "_AUDIT_DIR", tmp_path / "_storm_audit")
    return tmp_path


def _write_event(path: Path, event_type: str, **kwargs):
    record = {"event": event_type, "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **kwargs}
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


class TestAnalyze:
    def test_empty_returns_valid_structure(self, pr_tmp):
        from pattern_report import analyze
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        result = analyze(cutoff)
        assert "total_events" in result
        assert "blocks" in result
        assert "fingerprint_repeats" in result
        assert "hot_files" in result
        assert "test_regressions" in result

    def test_counts_events(self, pr_tmp):
        from pattern_report import analyze
        for i in range(3):
            _write_event(pr_tmp / "events.jsonl", "intent_detected", scope="test")
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        result = analyze(cutoff)
        assert result["total_events"] >= 3

    def test_counts_blocks(self, pr_tmp):
        from pattern_report import analyze
        _write_event(pr_tmp / "events.jsonl", "code_gate_blocked", scope="blocked-scope")
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        result = analyze(cutoff)
        assert result["blocks"]["total"] >= 1

    def test_detects_fingerprint_repeats(self, pr_tmp):
        from pattern_report import analyze
        fp_path = pr_tmp / "error-fingerprints.jsonl"
        for _ in range(3):
            record = {
                "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "fingerprint": "abcdef123456",
                "scope": "test-scope",
            }
            with open(fp_path, "a") as f:
                f.write(json.dumps(record) + "\n")
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        result = analyze(cutoff)
        assert "abcdef123456" in result["fingerprint_repeats"]


class TestTop3Patterns:
    def test_returns_at_most_3(self, pr_tmp):
        from pattern_report import _top3_patterns
        findings = {
            "fingerprint_repeats": {"abc": 5, "def": 4},
            "hot_files": {"file.py": 12},
            "test_regressions": ["scope-a"],
            "blocks": {"total": 10, "by_scope": {"x": 10}},
            "t11_fires": [],
            "storms_run": {},
            "event_breakdown": {},
        }
        patterns = _top3_patterns(findings)
        assert len(patterns) <= 3

    def test_returns_empty_when_no_issues(self, pr_tmp):
        from pattern_report import _top3_patterns
        findings = {
            "fingerprint_repeats": {},
            "hot_files": {},
            "test_regressions": [],
            "blocks": {"total": 0, "by_scope": {}},
            "t11_fires": [],
            "storms_run": {},
            "event_breakdown": {},
        }
        patterns = _top3_patterns(findings)
        assert patterns == []


class TestGenerateReport:
    def test_report_contains_headers(self, pr_tmp):
        from pattern_report import generate_report
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        content = generate_report(cutoff)
        assert "# 888 Storm Pattern Report" in content
        assert "## Top-3 Patterns" in content
        assert "## Recommended T11 Storm Scopes" in content

    def test_report_is_markdown(self, pr_tmp):
        from pattern_report import generate_report
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        content = generate_report(cutoff)
        assert isinstance(content, str)
        assert len(content) > 100


class TestRunDaily:
    def test_writes_report_file(self, pr_tmp):
        from pattern_report import run_daily
        content = run_daily(write=True)
        audit_dir = pr_tmp / "_storm_audit"
        reports = list(audit_dir.glob("pattern_report_*.md"))
        assert len(reports) >= 1

    def test_returns_content_string(self, pr_tmp):
        from pattern_report import run_daily
        content = run_daily(write=False)
        assert isinstance(content, str)
        assert len(content) > 50

    def test_does_not_block(self, pr_tmp):
        """Pattern report should never raise exceptions on empty data."""
        from pattern_report import run_daily
        content = run_daily(write=False)
        assert "Pattern Report" in content
