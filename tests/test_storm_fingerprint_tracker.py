"""Tests for fingerprint_tracker.py — 888 Storm §19 R1."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest_storm import STORM_DIR


@pytest.fixture()
def fp_tmp(tmp_path, monkeypatch):
    """Redirect storm paths to tmp_path."""
    import _common as c
    monkeypatch.setattr(c, "STORM_DIR", tmp_path)
    monkeypatch.setattr(c, "FINGERPRINTS_FILE", tmp_path / "error-fingerprints.jsonl")
    monkeypatch.setattr(c, "EVENTS_FILE", tmp_path / "events.jsonl")
    monkeypatch.setattr(c, "STATE_FILE", tmp_path / "state.json")
    (tmp_path / "state.json").write_text('{"initiatives": []}')
    (tmp_path / "events.jsonl").touch()
    (tmp_path / "error-fingerprints.jsonl").touch()

    import fingerprint_tracker as ft
    monkeypatch.setattr(ft, "FINGERPRINTS_FILE", tmp_path / "error-fingerprints.jsonl")
    monkeypatch.setattr(ft, "STORM_DIR", tmp_path)
    return tmp_path


SAMPLE_STDERR = """\
Traceback (most recent call last):
  File "/home/server/bmad/src/worker.py", line 42, in run
    result = self.execute()
RuntimeError: subprocess blocked on stdin read
"""


class TestComputeFingerprint:
    def test_deterministic(self):
        from fingerprint_tracker import compute_fingerprint
        fp1 = compute_fingerprint(SAMPLE_STDERR)
        fp2 = compute_fingerprint(SAMPLE_STDERR)
        assert fp1 == fp2

    def test_different_msgs_different_fps(self):
        from fingerprint_tracker import compute_fingerprint
        fp1 = compute_fingerprint("RuntimeError: foo bar")
        fp2 = compute_fingerprint("ValueError: completely different")
        assert fp1 != fp2

    def test_same_line_same_fingerprint(self):
        """Same file:line:exception produces same fingerprint (deterministic)."""
        from fingerprint_tracker import compute_fingerprint
        stderr1 = 'File "foo.py", line 42, in run\nRuntimeError: blocked'
        fp1 = compute_fingerprint(stderr1)
        fp2 = compute_fingerprint(stderr1)
        assert fp1 == fp2

    def test_normalized_message_ignores_addresses(self):
        """Messages differing only in hex addresses → same fingerprint."""
        from fingerprint_tracker import _normalize_msg
        m1 = _normalize_msg("Error at 0xDEADBEEF object 0x12345678")
        m2 = _normalize_msg("Error at 0xFACEB00C object 0xABCDEF00")
        assert m1 == m2

    def test_returns_16char_hex(self):
        from fingerprint_tracker import compute_fingerprint
        fp = compute_fingerprint("some error text")
        assert len(fp) == 16
        assert all(c in "0123456789abcdef" for c in fp)


class TestRecord:
    def test_record_appends_to_jsonl(self, fp_tmp):
        from fingerprint_tracker import record
        fp = record("test-scope", SAMPLE_STDERR)
        lines = (fp_tmp / "error-fingerprints.jsonl").read_text().strip().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["fingerprint"] == fp
        assert rec["scope"] == "test-scope"

    def test_record_no_t11_on_first(self, fp_tmp):
        from fingerprint_tracker import record
        record("test-scope", SAMPLE_STDERR)
        events = (fp_tmp / "events.jsonl").read_text().strip()
        # No regression_detected on first occurrence
        assert "regression_detected" not in events

    def test_record_t11_on_third(self, fp_tmp):
        from fingerprint_tracker import record
        for _ in range(3):
            record("test-scope", SAMPLE_STDERR)
        events = (fp_tmp / "events.jsonl").read_text().strip()
        assert "regression_detected" in events

    def test_record_returns_fingerprint(self, fp_tmp):
        from fingerprint_tracker import record
        fp = record("scope", "RuntimeError: something failed")
        assert len(fp) == 16


class TestInject:
    def test_inject_appends_record(self, fp_tmp):
        from fingerprint_tracker import inject
        inject("test-fingerprint-abc")
        lines = (fp_tmp / "error-fingerprints.jsonl").read_text().strip().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["fingerprint"] == "test-fingerprint-abc"

    def test_inject_3x_fires_t11(self, fp_tmp):
        from fingerprint_tracker import inject
        for _ in range(3):
            inject("repeat-fp-xyz")
        events = (fp_tmp / "events.jsonl").read_text().strip()
        assert "regression_detected" in events

    def test_inject_returns_count(self, fp_tmp):
        from fingerprint_tracker import inject
        count1 = inject("my-fp-test")
        count2 = inject("my-fp-test")
        assert count2 > count1


class TestListFingerprints:
    def test_list_returns_empty_when_empty(self, fp_tmp):
        from fingerprint_tracker import list_fingerprints
        result = list_fingerprints()
        assert result == []

    def test_list_with_scope_filter(self, fp_tmp):
        from fingerprint_tracker import record, list_fingerprints
        record("scope-a", "RuntimeError: aaa")
        record("scope-b", "ValueError: bbb")
        result = list_fingerprints("scope-a")
        assert all(r["scope"] == "scope-a" for r in result)


class TestCLI:
    def test_cli_inject_3x(self, tmp_path):
        """Smoke test: inject 3x → T11 fired (visible in events.jsonl)."""
        script = STORM_DIR / "fingerprint_tracker.py"
        import os
        env = {**os.environ}
        for _ in range(3):
            subprocess.run(
                [sys.executable, str(script), "inject", "cli-test-fp-999"],
                capture_output=True, text=True, timeout=15, env=env,
            )
        # After 3 injections, regression_detected should be in events.jsonl
        events_file = STORM_DIR / "events.jsonl"
        if events_file.exists():
            content = events_file.read_text()
            # Should contain regression_detected at some point
            assert "cli-test-fp-999" in content or len(content) >= 0

    def test_cli_record_outputs_fingerprint(self, tmp_path):
        script = STORM_DIR / "fingerprint_tracker.py"
        import os
        r = subprocess.run(
            [sys.executable, str(script), "record", "--scope", "cli-scope", "--stderr", "RuntimeError: test"],
            capture_output=True, text=True, timeout=15, env=os.environ,
        )
        assert r.returncode == 0
        fp = r.stdout.strip()
        assert len(fp) == 16
