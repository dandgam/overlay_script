"""Tests for cycle_detector.py — 888 Storm §19 R3."""

import json
from pathlib import Path

import pytest

from tests.conftest_storm import STORM_DIR


@pytest.fixture()
def cd_tmp(tmp_path, monkeypatch):
    import _common as c
    monkeypatch.setattr(c, "STORM_DIR", tmp_path)
    monkeypatch.setattr(c, "COMMIT_HISTORY_FILE", tmp_path / "commit-history.jsonl")
    monkeypatch.setattr(c, "EVENTS_FILE", tmp_path / "events.jsonl")
    monkeypatch.setattr(c, "STATE_FILE", tmp_path / "state.json")
    (tmp_path / "state.json").write_text('{"initiatives": []}')
    (tmp_path / "events.jsonl").touch()
    (tmp_path / "commit-history.jsonl").touch()

    import cycle_detector as cd
    monkeypatch.setattr(cd, "COMMIT_HISTORY_FILE", tmp_path / "commit-history.jsonl")
    monkeypatch.setattr(cd, "STORM_DIR", tmp_path)
    return tmp_path


class TestSimilarity:
    def test_identical_strings(self):
        from cycle_detector import _similarity
        assert _similarity("fix(virgil): sandbox error", "fix(virgil): sandbox error") == 1.0

    def test_very_different_strings(self):
        from cycle_detector import _similarity
        assert _similarity("abc", "xyz xyz xyz xyz") < 0.4

    def test_similar_strings(self):
        from cycle_detector import _similarity
        s1 = "fix(review-runner): stdin blocked"
        s2 = "fix(review-runner): stdin read blocked"
        assert _similarity(s1, s2) >= 0.7


class TestFindCycleCluster:
    def test_no_cluster_when_dissimilar(self):
        from cycle_detector import _find_cycle_cluster
        subjects = ["feat: add login", "fix: broken css", "docs: update readme", "refactor: data model"]
        size, rep = _find_cycle_cluster(subjects)
        assert size < 3

    def test_detects_cluster(self):
        from cycle_detector import _find_cycle_cluster
        subjects = [
            "fix(review-runner): stdin blocked",
            "fix(review-runner): stdin read error",
            "fix(review-runner): stdin subprocess blocked",
        ]
        size, rep = _find_cycle_cluster(subjects)
        assert size >= 3

    def test_single_item(self):
        from cycle_detector import _find_cycle_cluster
        size, rep = _find_cycle_cluster(["fix: something"])
        assert size < 2


class TestRecordCommit:
    def test_records_to_jsonl(self, cd_tmp):
        from cycle_detector import record_commit
        record_commit("virgil", "fix(virgil): sandbox error", 50)
        lines = (cd_tmp / "commit-history.jsonl").read_text().strip().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["scope"] == "virgil"
        assert rec["subject"] == "fix(virgil): sandbox error"


class TestCheck:
    def test_no_cycle_when_few_commits(self, cd_tmp):
        from cycle_detector import record_commit, check
        record_commit("scope", "fix(scope): issue A")
        record_commit("scope", "fix(scope): issue A again")
        result = check("scope")
        assert result is False

    def test_cycle_detected_with_3_similar(self, cd_tmp):
        from cycle_detector import record_commit, check
        for i in range(3):
            record_commit("review-runner", f"fix(review-runner): stdin blocked variant {i}", 60)
        result = check("review-runner")
        # Subjects are similar enough → T11 should fire
        assert result is True
        events = (cd_tmp / "events.jsonl").read_text()
        assert "commit_cycle_detected" in events

    def test_no_false_positive_short_messages(self, cd_tmp):
        from cycle_detector import record_commit, check
        # Short messages (<10 chars) should be ignored
        for _ in range(5):
            record_commit("tiny", "fix typo", 0)
        result = check("tiny")
        # Should NOT fire because "fix typo" is < _MIN_SUBJECT_LEN
        assert result is False

    def test_different_scopes_no_interference(self, cd_tmp):
        from cycle_detector import record_commit, check
        for i in range(3):
            record_commit("scope-a", f"fix(scope-a): error repeated {i}", 80)
        # scope-b should not be affected
        result = check("scope-b")
        assert result is False
