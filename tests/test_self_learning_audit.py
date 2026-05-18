"""Tests for self_learning.audit — M1."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from bmad_orchestrator.self_learning.audit import log_decision


def test_log_decision_writes_row(tmp_path: Path) -> None:
    events_path = tmp_path / "control.events.jsonl"
    with (
        patch("bmad_orchestrator.self_learning.audit.runs_dir", return_value=tmp_path),
        patch("bmad_orchestrator.self_learning.audit.now_iso", return_value="2026-05-18T10:00:00"),
    ):
        log_decision(
            trigger_event="wave_boundary_reached",
            decision_type="applied",
            payload={"proposal_id": "prop-001", "risk": "low"},
        )
    assert events_path.exists()
    row = json.loads(events_path.read_text(encoding="utf-8").strip())
    assert row["event_type"] == "self_learning_decision"
    assert row["trigger_event"] == "wave_boundary_reached"
    assert row["decision_type"] == "applied"
    assert row["proposal_id"] == "prop-001"
    assert row["ts"] == "2026-05-18T10:00:00"


def test_log_decision_appends_multiple_rows(tmp_path: Path) -> None:
    with patch("bmad_orchestrator.self_learning.audit.runs_dir", return_value=tmp_path):
        for i in range(3):
            log_decision(
                trigger_event="monthly_review_scheduled",
                decision_type="rejected",
                payload={"idx": i},
            )
    lines = (tmp_path / "control.events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3


def test_log_decision_swallows_errors() -> None:
    with patch(
        "bmad_orchestrator.self_learning.audit.runs_dir",
        side_effect=RuntimeError("disk full"),
    ):
        # Must not raise
        log_decision("any_event", "applied", {})


def test_log_decision_missing_optional_fields(tmp_path: Path) -> None:
    with patch("bmad_orchestrator.self_learning.audit.runs_dir", return_value=tmp_path):
        log_decision("wave_boundary_reached", "applied", {})
    row = json.loads(
        (tmp_path / "control.events.jsonl").read_text(encoding="utf-8").strip()
    )
    assert row["event_type"] == "self_learning_decision"
    assert row["decision_type"] == "applied"


def test_log_decision_payload_merged_into_row(tmp_path: Path) -> None:
    with patch("bmad_orchestrator.self_learning.audit.runs_dir", return_value=tmp_path):
        log_decision(
            "phase4_complete",
            "rolled_back",
            {"proposal_id": "p-999", "reason": "regression"},
        )
    row = json.loads(
        (tmp_path / "control.events.jsonl").read_text(encoding="utf-8").strip()
    )
    assert row["reason"] == "regression"
    assert row["trigger_event"] == "phase4_complete"
