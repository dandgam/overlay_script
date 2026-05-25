"""Tests for test_regression.py — 888 Storm §19 R4."""

import json
from pathlib import Path

import pytest

from tests.conftest_storm import STORM_DIR


@pytest.fixture()
def tr_tmp(tmp_path, monkeypatch):
    import _common as c
    monkeypatch.setattr(c, "STORM_DIR", tmp_path)
    monkeypatch.setattr(c, "TEST_FAILURES_FILE", tmp_path / "test-failures.jsonl")
    monkeypatch.setattr(c, "EVENTS_FILE", tmp_path / "events.jsonl")
    monkeypatch.setattr(c, "STATE_FILE", tmp_path / "state.json")
    (tmp_path / "state.json").write_text('{"initiatives": []}')
    (tmp_path / "events.jsonl").touch()
    (tmp_path / "test-failures.jsonl").touch()

    import test_regression as tr
    monkeypatch.setattr(tr, "TEST_FAILURES_FILE", tmp_path / "test-failures.jsonl")
    monkeypatch.setattr(tr, "STORM_DIR", tmp_path)
    return tmp_path


PYTEST_PASS_OUTPUT = """\
============================= test session results ==============================
collected 5 items

tests/test_foo.py::test_bar PASSED
tests/test_foo.py::test_baz PASSED

============================== 5 passed in 0.1s ==============================
"""

PYTEST_FAIL_OUTPUT = """\
============================= test session results ==============================
collected 5 items

tests/test_foo.py::test_bar FAILED

FAILED tests/test_foo.py::test_bar - AssertionError: expected 1 but got 2

============================== 1 failed, 4 passed in 0.2s ==============================
"""

CARGO_FAIL_OUTPUT = """\
test module::test_one ... ok
test module::test_two ... FAILED
test module::test_three ... ok

failures:
    module::test_two

test result: FAILED. 2 passed; 1 failed
"""

NPM_FAIL_OUTPUT = """\
  FAIL src/test/foo.test.js
  ✕ should handle edge case (5ms)
  ✕ should return correct result (2ms)

Test Suites: 1 failed, 1 total
Tests:       2 failed, 3 total
"""


class TestParsedTests:
    def test_parse_pytest_failed(self):
        from test_regression import parse_failed_tests
        result = parse_failed_tests("pytest", PYTEST_FAIL_OUTPUT)
        assert "tests/test_foo.py::test_bar" in result

    def test_parse_pytest_pass_empty(self):
        from test_regression import parse_failed_tests
        result = parse_failed_tests("pytest", PYTEST_PASS_OUTPUT)
        assert result == []

    def test_parse_cargo_failed(self):
        from test_regression import parse_failed_tests
        result = parse_failed_tests("cargo", CARGO_FAIL_OUTPUT)
        assert "module::test_two" in result

    def test_parse_npm_failed(self):
        from test_regression import parse_failed_tests
        result = parse_failed_tests("npm", NPM_FAIL_OUTPUT)
        assert len(result) >= 1

    def test_parse_unknown_runner(self):
        from test_regression import parse_failed_tests
        result = parse_failed_tests("unknown", "some output with errors")
        assert result == []


class TestRecordResult:
    def test_record_pass_appended(self, tr_tmp):
        from test_regression import record_result
        record_result("my-scope", "pytest", 0, PYTEST_PASS_OUTPUT)
        lines = (tr_tmp / "test-failures.jsonl").read_text().strip().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["result"] == "passed"
        assert rec["scope"] == "my-scope"

    def test_record_fail_appended(self, tr_tmp):
        from test_regression import record_result
        record_result("my-scope", "pytest", 1, PYTEST_FAIL_OUTPUT)
        lines = (tr_tmp / "test-failures.jsonl").read_text().strip().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["result"] == "failed"
        assert len(rec["failed_tests"]) > 0

    def test_no_t11_on_single_fail(self, tr_tmp):
        from test_regression import record_result
        record_result("scope", "pytest", 1, PYTEST_FAIL_OUTPUT)
        events = (tr_tmp / "events.jsonl").read_text()
        assert "regression_detected" not in events


class TestCheckRegression:
    def test_detect_failed_passed_failed_pattern(self, tr_tmp):
        from test_regression import record_result, check
        # Pattern: fail, pass, fail
        record_result("scope", "pytest", 1, PYTEST_FAIL_OUTPUT)
        record_result("scope", "pytest", 0, PYTEST_PASS_OUTPUT)
        record_result("scope", "pytest", 1, PYTEST_FAIL_OUTPUT)
        result = check("scope")
        assert result is True

    def test_no_regression_on_always_fail(self, tr_tmp):
        from test_regression import record_result, check
        for _ in range(3):
            record_result("scope2", "pytest", 1, PYTEST_FAIL_OUTPUT)
        result = check("scope2")
        assert result is False

    def test_no_regression_on_always_pass(self, tr_tmp):
        from test_regression import record_result, check
        for _ in range(3):
            record_result("scope3", "pytest", 0, PYTEST_PASS_OUTPUT)
        result = check("scope3")
        assert result is False

    def test_t11_event_recorded(self, tr_tmp):
        from test_regression import record_result, check
        record_result("rscope", "pytest", 1, PYTEST_FAIL_OUTPUT)
        record_result("rscope", "pytest", 0, PYTEST_PASS_OUTPUT)
        record_result("rscope", "pytest", 1, PYTEST_FAIL_OUTPUT)
        check("rscope")
        events = (tr_tmp / "events.jsonl").read_text()
        assert "regression_detected" in events

    def test_few_records_no_regression(self, tr_tmp):
        from test_regression import record_result, check
        record_result("sparse", "pytest", 1, PYTEST_FAIL_OUTPUT)
        result = check("sparse")
        assert result is False
