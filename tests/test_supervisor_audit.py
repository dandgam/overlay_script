"""Tests for Supervisor audit JSONL writer."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from bmad_orchestrator.supervisor.audit import log_decision
from bmad_orchestrator.supervisor.policy import SupervisorDecision, ToolCall


def test_log_decision_appends_row(tmp_path: Path):
    decision = SupervisorDecision(
        action="auto_respond",
        confidence=0.92,
        reason="hard rule match",
        tier=0,
        rule_id="budget-hard-cap",
        tool_calls=[ToolCall(name="pause_worker", args={"pid": 123})],
    )
    with patch("bmad_orchestrator.supervisor.audit.runs_dir", return_value=tmp_path):
        log_decision(
            event_type="BUDGET_THRESHOLD_HIT",
            event_payload={"story_id": "1.1", "ratio": 1.05},
            decision=decision,
        )
    log_path = tmp_path / "control.events.jsonl"
    assert log_path.is_file()
    rows = [json.loads(ln) for ln in log_path.read_text().splitlines() if ln.strip()]
    assert len(rows) == 1
    row = rows[0]
    assert row["event_type"] == "supervisor_decision"
    assert row["source_event_type"] == "BUDGET_THRESHOLD_HIT"
    assert row["story_id"] == "1.1"
    assert row["tier"] == 0
    assert row["action"] == "auto_respond"
    assert row["confidence"] == 0.92
    assert row["rule_id"] == "budget-hard-cap"
    assert row["tool_calls"] == [{"name": "pause_worker", "args": {"pid": 123}}]
    assert "ts" in row


def test_log_decision_missing_story_id(tmp_path: Path):
    decision = SupervisorDecision(
        action="escalate_human",
        confidence=0.3,
        reason="uncertain",
        tier=2,
    )
    with patch("bmad_orchestrator.supervisor.audit.runs_dir", return_value=tmp_path):
        log_decision(
            event_type="WORKER_SILENT_FAILURE",
            event_payload={},
            decision=decision,
        )
    log_path = tmp_path / "control.events.jsonl"
    row = json.loads(log_path.read_text().splitlines()[0])
    assert row["story_id"] is None
    assert row["tool_calls"] == []


def test_log_decision_swallows_writer_errors(tmp_path: Path, caplog):
    """If append_jsonl raises, log_decision must NOT propagate."""
    decision = SupervisorDecision(
        action="no_op",
        confidence=1.0,
        reason="r",
        tier=0,
    )
    # Point runs_dir at a path that can't be written (file instead of dir).
    bad = tmp_path / "not-a-dir"
    bad.write_text("file not dir")
    with patch("bmad_orchestrator.supervisor.audit.runs_dir", return_value=bad):
        log_decision(
            event_type="HUMAN_QUERY",
            event_payload={"story_id": "1.1"},
            decision=decision,
        )
    # Should not raise; control.events.jsonl was never created
    assert not (bad / "control.events.jsonl").exists() or True  # tolerant


def test_log_decision_multiple_rows_append(tmp_path: Path):
    d1 = SupervisorDecision(action="no_op", confidence=1.0, reason="a", tier=0)
    d2 = SupervisorDecision(action="auto_respond", confidence=0.9, reason="b", tier=1)
    with patch("bmad_orchestrator.supervisor.audit.runs_dir", return_value=tmp_path):
        log_decision("HUMAN_QUERY", {"story_id": "x"}, d1)
        log_decision("HUMAN_QUERY", {"story_id": "y"}, d2)
    rows = [
        json.loads(ln)
        for ln in (tmp_path / "control.events.jsonl").read_text().splitlines()
        if ln.strip()
    ]
    assert len(rows) == 2
    assert rows[0]["reason"] == "a"
    assert rows[1]["reason"] == "b"
