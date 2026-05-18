"""Action executor — maps SupervisorDecision → bus events / tool calls.

Keeps the side-effect surface tiny: emit one bus event per requested action,
let the existing handlers (pause_worker tool, escalate_to_human flow, etc.)
do the heavy lifting. This avoids the supervisor module taking on direct
subprocess responsibilities.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from bmad_orchestrator.supervisor.policy import SupervisorDecision

if TYPE_CHECKING:
    from bmad_orchestrator.runtime.event_loop import EventLoop

log = structlog.get_logger("supervisor.actions")


async def execute_decision(
    decision: SupervisorDecision,
    *,
    source_event_type: str,
    source_payload: dict[str, Any],
    bus: EventLoop,
) -> None:
    """Translate a Supervisor decision into bus events.

    All actions go through the bus so the audit trail (control.events.jsonl)
    + downstream subscribers stay coherent. Emits one HUMAN_QUERY for
    ``escalate_human``, one synthetic control event for everything else.
    """
    from bmad_orchestrator.runtime.event_loop import Event, EventType

    if decision.action == "no_op":
        log.debug("supervisor_noop", source=source_event_type)
        return

    if decision.action == "escalate_human":
        await bus.emit(
            Event(
                type=EventType.HUMAN_QUERY,
                payload={
                    "source": "supervisor",
                    "source_event_type": source_event_type,
                    "story_id": source_payload.get("story_id"),
                    "tier": decision.tier,
                    "confidence": decision.confidence,
                    "reason": decision.reason,
                    "escalation_text": decision.escalation_text,
                    "tool_calls": [
                        {"name": tc.name, "args": tc.args} for tc in decision.tool_calls
                    ],
                },
            )
        )
        log.info(
            "supervisor_escalated",
            source=source_event_type,
            tier=decision.tier,
            confidence=decision.confidence,
        )
        return

    if decision.action == "pause_workers":
        await bus.emit(
            Event(
                type=EventType.HUMAN_QUERY,
                payload={
                    "source": "supervisor",
                    "action": "pause_workers",
                    "source_event_type": source_event_type,
                    "story_id": source_payload.get("story_id"),
                    "reason": decision.reason,
                    "tier": decision.tier,
                },
            )
        )
        log.warning(
            "supervisor_pause_workers",
            source=source_event_type,
            reason=decision.reason,
        )
        return

    if decision.action == "abort_pipeline":
        # Abort is a strong signal — emit on bus, let stop_orchestrator wire
        # handle the actual halt path.
        await bus.emit(
            Event(
                type=EventType.HUMAN_QUERY,
                payload={
                    "source": "supervisor",
                    "action": "abort_pipeline",
                    "source_event_type": source_event_type,
                    "reason": decision.reason,
                    "tier": decision.tier,
                },
            )
        )
        log.error(
            "supervisor_abort_pipeline",
            source=source_event_type,
            reason=decision.reason,
        )
        return

    if decision.action == "auto_respond":
        # Auto-response = tool call request. We surface it as a HUMAN_QUERY with
        # a `source=supervisor + action=auto_respond` discriminator so the
        # downstream intent-router subscriber can dispatch the actual tool
        # invocation (or the operator can review it).
        await bus.emit(
            Event(
                type=EventType.HUMAN_QUERY,
                payload={
                    "source": "supervisor",
                    "action": "auto_respond",
                    "source_event_type": source_event_type,
                    "story_id": source_payload.get("story_id"),
                    "tool_calls": [
                        {"name": tc.name, "args": tc.args} for tc in decision.tool_calls
                    ],
                    "confidence": decision.confidence,
                    "reason": decision.reason,
                    "tier": decision.tier,
                },
            )
        )
        log.info(
            "supervisor_auto_respond",
            source=source_event_type,
            tools=[tc.name for tc in decision.tool_calls],
            confidence=decision.confidence,
        )
        return


__all__ = ["execute_decision"]
