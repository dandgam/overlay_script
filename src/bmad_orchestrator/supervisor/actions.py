"""Action executor — maps SupervisorDecision → bus events / tool calls.

Keeps the side-effect surface tiny: emit one bus event per requested action,
let the existing handlers (pause_worker tool, escalate_to_human flow, etc.)
do the heavy lifting. This avoids the supervisor module taking on direct
subprocess responsibilities.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

from bmad_orchestrator.runtime.worker_cancellation import (
    cancel_worker as runtime_cancel_worker,
)
from bmad_orchestrator.runtime.worker_cancellation import (
    get_token_for_story as _get_token_for_story,
)
from bmad_orchestrator.supervisor.policy import SupervisorDecision

if TYPE_CHECKING:
    from bmad_orchestrator.runtime.event_loop import EventLoop

log = structlog.get_logger("supervisor.actions")

async def _drain_pending_success_completions(bus: EventLoop) -> int:
    """NEW-42 — drain pending WORKER_COMPLETED(status=success) events from the
    bus queue before the orchestrator aborts, giving already-finished stories a
    chance to reach the merge gate and integration branch.

    Only drains events that are *already in the queue* at abort time (idle
    poll timeout = 0.05 s — same as ``EventLoop.drain``).  Does NOT wait for
    new events to arrive.  Stops when the queue is empty or a non-success
    WORKER_COMPLETED event is at the head (those are re-queued for normal
    dispatch).  Returns the count of drained events.
    """
    from bmad_orchestrator.runtime.event_loop import EventType

    # idle_timeout matches EventLoop.drain default — short enough that the
    # function returns promptly when the queue is empty.
    _idle_timeout = 0.05
    drained = 0
    while True:
        event = await bus.dispatch_one(timeout=_idle_timeout)
        if event is None:
            break
        drained += 1
        payload = event.payload or {}
        if (
            event.type == EventType.WORKER_COMPLETED
            and payload.get("status") == "success"
        ):
            log.info(
                "abort_drain_worker_completed",
                story_id=payload.get("story_id"),
                drained_count=drained,
            )
        else:
            # Non-target event — re-queue it and stop draining.
            await bus.emit(event)
            break
    return drained


async def execute_decision(
    decision: SupervisorDecision,
    *,
    source_event_type: str,
    source_payload: dict[str, Any],
    bus: EventLoop,
    drain_pending: bool = True,
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
        # NEW-42 — drain pending WORKER_COMPLETED(success) events before
        # emitting the abort HUMAN_QUERY.  Stories that already finished dev
        # but whose merge_gate hasn't run yet get a last chance to reach the
        # integration branch.  Controlled by ``drain_pending`` (default True,
        # mapped from policy ``defaults.abort_pipeline_drain_pending``).
        if drain_pending:
            drained = await _drain_pending_success_completions(bus)
            if drained:
                log.info(
                    "abort_drain_completed",
                    drained_events=drained,
                    reason=decision.reason,
                )
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
            from bmad_orchestrator.runtime.worker_cancellation import active_worker_ids

            tok = _get_token_for_story(story_id)
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

    if decision.action == "respawn_worker":
        # NEW-38: cancel the existing worker + emit WORKER_RESPAWN_REQUESTED
        # so the orchestrator main loop re-queues the story.  This function
        # does NOT spawn the worker directly — avoids race conditions with the
        # main loop's scheduling logic.
        story_id: str | None = source_payload.get("story_id")
        reason = decision.reason
        max_iteration = 1
        dev_prompt_hint: str | None = None
        for tc in decision.tool_calls:
            if tc.name != "respawn_worker":
                continue
            args = tc.args or {}
            story_id = args.get("story_id") or story_id
            reason = args.get("reason") or reason
            max_iteration = int(args.get("max_iteration", 1))
            dev_prompt_hint = args.get("dev_prompt_hint")
        if story_id is None:
            log.warning(
                "supervisor_respawn_worker_missing_story_id",
                source=source_event_type,
            )
            return
        # Step 1: cancel the running worker (best-effort; may already be dead).
        cancelled_worker_id: str | None = None
        tok = _get_token_for_story(story_id)
        if tok is not None:
            cancelled_worker_id = tok.worker_id
            try:
                await asyncio.wait_for(
                    runtime_cancel_worker(
                        tok.worker_id,
                        reason=f"respawn requested: {reason}",
                        cancelled_by="supervisor",
                    ),
                    timeout=30.0,
                )
            except TimeoutError:
                log.warning(
                    "supervisor_respawn_cancel_timeout",
                    worker_id=tok.worker_id,
                    story_id=story_id,
                )
            except Exception as exc:
                log.warning(
                    "supervisor_respawn_cancel_failed",
                    worker_id=tok.worker_id,
                    story_id=story_id,
                    error=str(exc),
                )
        # Step 2: signal main loop via WORKER_RESPAWN_REQUESTED.
        await bus.emit(
            EventType.WORKER_RESPAWN_REQUESTED,
            story_id=story_id,
            reason=reason,
            iteration=max_iteration,
            dev_prompt_hint=dev_prompt_hint,
            cancelled_worker_id=cancelled_worker_id,
        )
        log.info(
            "supervisor_respawn_worker",
            story_id=story_id,
            reason=reason,
            iteration=max_iteration,
            has_hint=dev_prompt_hint is not None,
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
