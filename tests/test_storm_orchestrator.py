"""Tests for storm-orchestrator.py — 888 Storm §6.2 + §18."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest_storm import STORM_DIR


@pytest.fixture()
def orch_tmp(tmp_path, monkeypatch):
    """Redirect storm paths and cwd to tmp_path."""
    import _common as c
    monkeypatch.setattr(c, "STORM_DIR", tmp_path / ".storm")
    monkeypatch.setattr(c, "EVENTS_FILE", tmp_path / ".storm" / "events.jsonl")
    monkeypatch.setattr(c, "STATE_FILE", tmp_path / ".storm" / "state.json")
    monkeypatch.setattr(c, "DECISION_LOG", tmp_path / ".storm" / "decision-log.md")
    (tmp_path / ".storm").mkdir(parents=True)
    (tmp_path / ".storm" / "state.json").write_text('{"initiatives": []}')
    (tmp_path / ".storm" / "events.jsonl").touch()
    (tmp_path / ".storm" / "decision-log.md").write_text("# 888 Storm Decision Log\n\n")
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestIsHeadless:
    def test_headless_env(self, monkeypatch):
        monkeypatch.setenv("HEADLESS_MODE", "1")
        from storm_orchestrator import _is_headless
        assert _is_headless() is True

    def test_not_headless_env_returns_bool(self, monkeypatch):
        monkeypatch.setenv("HEADLESS_MODE", "0")
        from storm_orchestrator import _is_headless
        result = _is_headless()
        assert isinstance(result, bool)


class TestSelectMethods:
    def test_required_methods_always_included(self, monkeypatch):
        monkeypatch.setenv("HEADLESS_MODE", "1")
        from storm_orchestrator import _select_methods, _TRIGGER_REQUIRED_METHODS
        selection = _select_methods("T1", "my-feature", [], headless=True)
        for req in _TRIGGER_REQUIRED_METHODS["T1"]:
            assert req in selection["final_methods"]

    def test_security_scope_triggers_static_rules(self, monkeypatch):
        monkeypatch.setenv("HEADLESS_MODE", "1")
        from storm_orchestrator import _select_methods
        selection = _select_methods("T1", "payment-security-gateway", [], headless=True)
        assert len(selection["static_rules_matched"]) > 0

    def test_no_match_triggers_llm_judge_stub(self, monkeypatch):
        monkeypatch.setenv("HEADLESS_MODE", "1")
        from storm_orchestrator import _select_methods
        # Use a scope with no matching static rules for T1
        selection = _select_methods("T1", "totally-obscure-zzz-scope", [], headless=True)
        # Either static matched something or llm_judge was invoked
        assert selection["llm_judge_invoked"] or len(selection["static_rules_matched"]) > 0

    def test_cli_method_overrides_applied(self, monkeypatch):
        monkeypatch.setenv("HEADLESS_MODE", "1")
        from storm_orchestrator import _select_methods
        selection = _select_methods("T1", "my-slug", [99, 88], headless=True)
        assert selection["user_override"] == [99, 88]
        assert 99 in selection["final_methods"]

    def test_no_duplicate_methods(self, monkeypatch):
        monkeypatch.setenv("HEADLESS_MODE", "1")
        from storm_orchestrator import _select_methods
        selection = _select_methods("T1", "payment-gateway", [], headless=True)
        final = selection["final_methods"]
        assert len(final) == len(set(final))


class TestRun:
    def test_creates_artifact(self, orch_tmp, monkeypatch):
        monkeypatch.setenv("HEADLESS_MODE", "1")
        from storm_orchestrator import run
        artifact = run("T1", "test-feature")
        assert artifact.exists()
        content = artifact.read_text()
        assert "T1" in content
        assert "test-feature" in content

    def test_artifact_path_correct(self, orch_tmp, monkeypatch):
        monkeypatch.setenv("HEADLESS_MODE", "1")
        from storm_orchestrator import run
        artifact = run("T1", "my-slug")
        assert "feature" in str(artifact)
        assert "my-slug" in str(artifact)
        assert artifact.suffix == ".md"

    def test_t2_artifact_named_agent(self, orch_tmp, monkeypatch):
        monkeypatch.setenv("HEADLESS_MODE", "1")
        from storm_orchestrator import run
        artifact = run("T2", "my-agent")
        assert "agent" in str(artifact)

    def test_updates_state_json(self, orch_tmp, monkeypatch):
        monkeypatch.setenv("HEADLESS_MODE", "1")
        import _common as c
        from storm_orchestrator import run
        run("T1", "state-test")
        state = c.load_json(orch_tmp / ".storm" / "state.json")
        assert any(init["slug"] == "state-test" for init in state["initiatives"])

    def test_records_events(self, orch_tmp, monkeypatch):
        monkeypatch.setenv("HEADLESS_MODE", "1")
        from storm_orchestrator import run
        run("T1", "event-test")
        events_lines = (orch_tmp / ".storm" / "events.jsonl").read_text().strip().splitlines()
        event_types = {json.loads(l)["event"] for l in events_lines if l}
        assert "storm_started" in event_types
        assert "storm_method_selection" in event_types
        assert "storm_completed" in event_types

    def test_artifact_contains_required_methods(self, orch_tmp, monkeypatch):
        monkeypatch.setenv("HEADLESS_MODE", "1")
        from storm_orchestrator import run, _TRIGGER_REQUIRED_METHODS
        artifact = run("T2", "method-check")
        content = artifact.read_text()
        # All required method IDs should be present
        for mid in _TRIGGER_REQUIRED_METHODS["T2"]:
            assert f"#{mid}" in content

    def test_t7_enumeration_scope(self, orch_tmp, monkeypatch):
        monkeypatch.setenv("HEADLESS_MODE", "1")
        from storm_orchestrator import run
        artifact = run("T7-enumeration", "virgil-sandbox")
        assert artifact.exists()

    def test_t3_change_artifact(self, orch_tmp, monkeypatch):
        monkeypatch.setenv("HEADLESS_MODE", "1")
        from storm_orchestrator import run
        artifact = run("T3", "my-agent")
        assert "change" in str(artifact)

    def test_storm_method_selection_logged(self, orch_tmp, monkeypatch):
        monkeypatch.setenv("HEADLESS_MODE", "1")
        from storm_orchestrator import run
        run("T1", "sel-log")
        events = [
            json.loads(l)
            for l in (orch_tmp / ".storm" / "events.jsonl").read_text().strip().splitlines()
            if l
        ]
        sel_events = [e for e in events if e["event"] == "storm_method_selection"]
        assert len(sel_events) == 1
        sel = sel_events[0]
        assert "required" in sel
        assert "final_methods" in sel
        assert "mode" in sel


class TestCLI:
    def test_cli_headless_creates_artifact(self, tmp_path):
        script = STORM_DIR / "storm-orchestrator.py"
        env = {"HEADLESS_MODE": "1", "HOME": str(Path.home()), "PATH": "/usr/bin:/bin"}
        import os
        full_env = {**os.environ, "HEADLESS_MODE": "1"}
        r = subprocess.run(
            [sys.executable, str(script), "T1", "smoke-test-cli"],
            capture_output=True, text=True, timeout=30,
            cwd=str(tmp_path),
            env=full_env,
        )
        artifact = tmp_path / "spec" / "feature_smoke-test-cli_storm.md"
        assert artifact.exists(), f"stderr: {r.stderr}, stdout: {r.stdout}"
        assert r.returncode == 0
        # Cleanup
        artifact.unlink(missing_ok=True)

    def test_cli_no_args_exits_error(self):
        script = STORM_DIR / "storm-orchestrator.py"
        r = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=15,
        )
        assert r.returncode != 0
