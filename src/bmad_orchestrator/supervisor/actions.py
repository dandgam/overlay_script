"""Action executor — maps SupervisorDecision → bus events / tool calls.

Keeps the side-effect surface tiny: emit one bus event per requested action,
let the existing handlers (pause_worker tool, escalate_to_human flow, etc.)
do the heavy lifting. This avoids the supervisor module taking on direct
subprocess responsibilities.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from bmad_orchestrator.runtime.worker_cancellation import (
    cancel_worker as runtime_cancel_worker,
)
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

    if decision.action == "cancel_worker":
        # Initiative pilot_findings_closure S4 (#4 R1): per-worker cancellation.
        # We require ``tool_calls=[{name="cancel_worker", args={worker_id: ...}}]``
        # OR a ``worker_id`` in the source payload (e.g. WORKER_SILENT_FAILURE).
        worker_id: str | None = None
        story_id: str | None = source_payload.get("story_id")
        for tc in decision.tool_calls:
            if tc.name != "cancel_worker":
                continue
            args = tc.args or {}
            worker_id = args.get("worker_id") or worker_id
            story_id = args.get("story_id") or story_id
        if worker_id is None:
            worker_id = source_payload.get("worker_id")
        if worker_id is None and story_id is not None:
            # Fallback: look up by story_id in the registry. Avoids forcing the
            # judge to know PID-suffixed worker_ids that orchestrator built.
            from bmad_orchestrator.runtime.worker_cancellation import (
                active_worker_ids,
                get_token_for_story,
            )
            tok = get_token_for_story(story_id)
            if tok is not None:
                worker_id = tok.worker_id
            else:
                log.warning(
                    "supervisor_cancel_worker_no_match",
                    story_id=story_id,
                    active=active_worker_ids(),
                )
        if worker_id is None:
            log.warning(
                "supervisor_cancel_worker_missing_id",
                source=source_event_type,
                reason=decision.reason,
            )
            return
        cancelled_by = "supervisor"
        result = await runtime_cancel_worker(
            worker_id,
            reason=decision.reason,
            cancelled_by=cancelled_by,
        )
        log.info(
            "supervisor_cancel_worker",
            worker_id=worker_id,
            story_id=story_id,
            result=result,
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
