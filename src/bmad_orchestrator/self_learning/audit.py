"""JSONL audit log writer for self-learning decisions.

See spec/spec_self_learning_loop.md §3.1 Step 8 + Q7:
audit is inline in control.events.jsonl with event_type=self_learning_decision.

Failures are logged at WARNING but never raise — audit writes must not
crash the event bus or the consolidation pipeline.
"""

from __future__ import annotations

from typing import Any

import structlog

from bmad_orchestrator.agent.tools._common import append_jsonl, now_iso, runs_dir

log = structlog.get_logger("self_learning.audit")


def log_decision(
    trigger_event: str,
    decision_type: str,
    payload: dict[str, Any],
) -> None:
    """Append one self_learning_decision row to control.events.jsonl.

    decision_type: 'applied' | 'rejected' | 'escalated' | 'rolled_back'
    payload: arbitrary dict with context (proposal_id, reason, risk, etc.)
    """
    try:
        path = runs_dir() / "control.events.jsonl"
        append_jsonl(
            path,
            {
                "event_type": "self_learning_decision",
                "ts": now_iso(),
                "trigger_event": trigger_event,
                "decision_type": decision_type,
                **payload,
            },
        )
    except Exception as exc:
        log.warning("self_learning_audit_failed", error=str(exc))


__all__ = ["log_decision"]
