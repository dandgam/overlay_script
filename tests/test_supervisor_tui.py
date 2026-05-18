"""Tests for Supervisor TUI panel."""

from __future__ import annotations

from bmad_orchestrator.cli.tui import DashboardSnapshot, _supervisor_panel, render_once


def test_supervisor_panel_empty():
    snap = DashboardSnapshot(supervisor_judge_state="idle")
    panel = _supervisor_panel(snap)
    assert panel.title == "supervisor"


def test_supervisor_panel_with_decisions():
    snap = DashboardSnapshot(
        supervisor_recent=[
            {
                "tier": 0,
                "action": "pause_workers",
                "confidence": 1.0,
                "reason": "budget hit",
            },
            {
                "tier": 1,
                "action": "auto_respond",
                "confidence": 0.92,
                "reason": "clear case",
            },
            {
                "tier": 2,
                "action": "escalate_human",
                "confidence": 0.4,
                "reason": "uncertain",
            },
        ],
        supervisor_judge_state="waiting",
        supervisor_rate_capacity="3/10",
    )
    panel = _supervisor_panel(snap)
    assert panel.title == "supervisor"


def test_render_once_includes_supervisor_section():
    """Full render contains the supervisor panel title."""
    snap = DashboardSnapshot(
        status="running",
        project="proj",
        wave="1a",
        supervisor_recent=[
            {"tier": 0, "action": "auto_respond", "confidence": 0.99, "reason": "ok"}
        ],
        supervisor_judge_state="idle",
        supervisor_rate_capacity="1/10",
    )
    text = render_once(snap)
    assert "supervisor" in text.lower()


def test_render_once_supervisor_when_empty():
    snap = DashboardSnapshot(status="running")
    text = render_once(snap)
    # Panel title still rendered even when no decisions yet
    assert "supervisor" in text.lower()
