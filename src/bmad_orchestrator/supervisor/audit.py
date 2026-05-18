"""JSONL audit log writer for Supervisor decisions.

See spec/spec_supervisor_llm_loop.md §7 Q6 — inline in control.events.jsonl
under `event_type=supervisor_decision`.
"""

from __future__ import annotations

from typing import Any

import structlog

from bmad_orchestrator.agent.tools._common import append_jsonl, now_iso, runs_dir
from bmad_orchestrator.supervisor.policy import SupervisorDecision

log = structlog.get_logger("supervisor.audit")


def log_decision(
    event_type: str,
    event_payload: dict[str, Any],
    decision: SupervisorDecision,
) -> None:
    """Append one supervisor_decision row to control.events.jsonl.

    Failures are logged at WARNING but never raise — audit writes must not
    crash the event bus.
    """
    try:
        path = runs_dir() / "control.events.jsonl"
        append_jsonl(
            path,
            {
                "event_type": "supervisor_decision",
                "ts": now_iso(),
                "source_event_type": event_type,
                "story_id": event_payload.get("story_id"),
                "tier": decision.tier,
                "action": decision.action,
                "confidence": decision.confidence,
                "rule_id": decision.rule_id,
                "reason": decision.reason,
                "tool_calls": [
                    {"name": tc.name, "args": tc.args} for tc in decision.tool_calls
                ],
            },
        )
    except Exception as exc:
        log.warning("supervisor_audit_failed", error=str(exc))


__all__ = ["log_decision"]
