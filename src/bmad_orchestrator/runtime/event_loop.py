"""Event-driven wakeup loop (spec §4 — 13 event types + 5-min backstop).

Producers (S3+): worker_spawn (subprocess watchers), bot/telegram_bot, safety guards,
scheduled tasks. Consumer: agent/loop top-level orchestrator (one consumer per session).

Spec §4 events:
    worker_completed, worker_halt_file, worker_elicitation, budget_threshold_hit,
    wave_boundary_reached, epic_boundary_reached, user_chat_message,
    monthly_review_scheduled, voice_message_received, story_split_triggered,
    phase4_complete, human_response, scheduled_wakeup_5min.

Initiative #2C extension (sub-story life-cycle) — see runtime/sub_story_executor.py
+ runtime/auto_split.py. Bridge translates executor ``on_event`` dicts into typed
event-bus emissions so downstream observers (telemetry, code-review timing, retro)
can subscribe without depending on the executor's callback contract.

Backstop: `start_backstop_task()` запускает фоновый таск который раз в N секунд
(default 300s = 5min) emits SCHEDULED_WAKEUP — гарантирует пробуждение даже если
ни одного «настоящего» event'а не пришло.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

BACKSTOP_INTERVAL_SECONDS_DEFAULT = 300


class EventType(StrEnum):
    """Spec §4 — все 13 типов + FS4 HUMAN_QUERY. Phase 4 hardening adds up to 29 total."""

    WORKER_COMPLETED = "worker_completed"
    WORKER_HALT_FILE = "worker_halt_file"
    WORKER_ELICITATION = "worker_elicitation"
    WORKER_SILENT_FAILURE = "worker_silent_failure"
    COST_TRACKING_UNAVAILABLE = "cost_tracking_unavailable"
    BUDGET_THRESHOLD_HIT = "budget_threshold_hit"
    WAVE_BOUNDARY_REACHED = "wave_boundary_reached"
    EPIC_BOUNDARY_REACHED = "epic_boundary_reached"
    USER_CHAT_MESSAGE = "user_chat_message"
    MONTHLY_REVIEW_SCHEDULED = "monthly_review_scheduled"
    VOICE_MESSAGE_RECEIVED = "voice_message_received"
    STORY_SPLIT_TRIGGERED = "story_split_triggered"
    # Initiative pilot_findings_closure S3 (#3 rycag 2): emitted by
    # ``runtime/decomposer_subscriber.py`` when a WORKER_HALT_FILE arrives with
    # ``halt_reason="loc_cap_exceeded"`` and the auto-split decomposer is
    # invoked. Distinct from STORY_SPLIT_TRIGGERED (heuristic-only); this fires
    # on the recovery path after Sonnet hit the 300-LOC diff cap.
    STORY_AUTO_SPLIT = "story_auto_split"
    SUB_STORY_STARTED = "sub_story_started"
    SUB_STORY_COMPLETED = "sub_story_completed"
    SUB_STORY_SQUASH_DONE = "sub_story_squash_done"
    SUB_STORY_SQUASH_SKIPPED = "sub_story_squash_skipped"
    PHASE4_COMPLETE = "phase4_complete"
    HUMAN_QUERY = "human_query"
    HUMAN_RESPONSE = "human_response"
    SCHEDULED_WAKEUP = "scheduled_wakeup_5min"
    CODE_REVIEW_VERDICT = "code_review_verdict"
    SECURITY_REVIEW_PASSED = "security_review_passed"
    COMPLIANCE_SWEEP_NEEDED = "compliance_sweep_needed"
    # BMad Phase 4 canonical workflow triggers (gap-closure 2026-05-19).
    SPRINT_SCOPE_CHANGE_DETECTED = "sprint_scope_change_detected"
    FORENSIC_INVESTIGATION_NEEDED = "forensic_investigation_needed"
    # Phase 4 hardening #2 — PreCompact memory persistence observability.
    WORKER_STATE_PERSISTED = "worker_state_persisted"
    # Phase 4 hardening #5 — Two-stage merge gate split.
    # Payload: {stage: "spec"|"quality", verdict: str, findings_count: int}
    MERGE_GATE_STAGE_COMPLETED = "merge_gate_stage_completed"
    # Phase 4 hardening #6 — Stop-hook cost + learning consolidation.
    # STORY_COMPLETED: emitted when a story finishes (all stages including merge gate).
    # Payload: {story_id, status, extract_lessons: bool, tokens: {input, cached, output},
    #           cost_usd, retry_count, turn_latencies_ms: [float, ...]}
    STORY_COMPLETED = "story_completed"
    # STORY_METRICS_AGGREGATED: emitted by stop_hook_subscriber after aggregating per-story metrics.
    # Payload: {story_id, total_input_tokens, total_cached_tokens, total_output_tokens,
    #           total_cost_usd, retry_count, p95_turn_latency_ms, lessons_extracted: bool}
    STORY_METRICS_AGGREGATED = "story_metrics_aggregated"


ALL_EVENT_TYPES: tuple[EventType, ...] = tuple(EventType)


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(slots=True)
class Event:
    type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    emitted_at: str = field(default_factory=_utc_iso)


EventCallback = Callable[[Event], Awaitable[None]]


class EventLoop:
    """Single-process producer/multi-consumer event queue.

    Подписки (`on(...)`) выполняются sequentially per event под `asyncio.Lock`,
    чтобы порядок (FIFO) и effect'ы оставались детерминированными при
    нескольких subscriber'ах.
    """

    def __init__(self) -> None:
        self.queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subs: list[EventCallback] = []
        self._lock = asyncio.Lock()
        self._backstop_task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()
        self._corr_futures: dict[str, asyncio.Future[Event]] = {}

    # ── public producer API ──────────────────────────────────────────────────

    async def emit(self, event_or_type: Event | EventType, **payload: Any) -> Event:
        """Emit an event. Accept either Event or (EventType, **payload)."""
        if isinstance(event_or_type, EventType):
            event = Event(type=event_or_type, payload=dict(payload))
        else:
            event = event_or_type
        await self.queue.put(event)
        self._resolve_correlation(event)
        return event

    # ── public consumer API ──────────────────────────────────────────────────

    async def next(self, timeout: float | None = None) -> Event | None:
        """Block for next event. `timeout=None` blocks forever; returns None on timeout."""
        if timeout is None:
            return await self.queue.get()
        try:
            return await asyncio.wait_for(self.queue.get(), timeout=timeout)
        except TimeoutError:
            return None

    def on(self, callback: EventCallback) -> None:
        """Register a subscriber called per dispatched event."""
        self._subs.append(callback)

    async def dispatch_one(self, timeout: float | None = None) -> Event | None:
        """Pull one event and run all subscribers. Returns the event (or None on timeout)."""
        event = await self.next(timeout=timeout)
        if event is None:
            return None
        self._resolve_correlation(event)
        async with self._lock:
            for cb in list(self._subs):
                await cb(event)
        return event

    # ── single-shot correlation futures (W5 — bot ↔ intent-router bridge) ────

    def subscribe_one_correlation(self, corr_id: str) -> asyncio.Future[Event]:
        """Return an asyncio.Future that resolves on the first ``HUMAN_RESPONSE``
        event whose payload's ``corr_id`` matches.

        Single-shot: resolved on first match, then removed from the registry.
        Callers MUST call :meth:`unsubscribe_correlation` in a ``finally`` block
        if they abandon the wait (timeout, cancellation), otherwise the entry
        leaks until process exit.

        If an outstanding (not-yet-resolved) future already exists for the same
        ``corr_id``, that same future is returned so concurrent callers join the
        single wait. A done/cancelled future is replaced with a fresh one — the
        old corr_id has effectively expired.
        """
        existing = self._corr_futures.get(corr_id)
        if existing is not None and not existing.done():
            return existing
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Event] = loop.create_future()
        self._corr_futures[corr_id] = fut
        return fut

    def unsubscribe_correlation(self, corr_id: str) -> None:
        """Drop the future registered for ``corr_id``. Idempotent."""
        self._corr_futures.pop(corr_id, None)

    def _resolve_correlation(self, event: Event) -> None:
        """Resolve any pending future whose ``corr_id`` matches this event.

        Fired from both :meth:`emit` (producer-side, wakes awaiter even when
        nobody drains the queue) and :meth:`dispatch_one` (consumer-side, kept
        as defence-in-depth — a future could have been registered after the
        emit but before dispatch). Pop semantics make the resolution idempotent.
        """
        if event.type is not EventType.HUMAN_RESPONSE:
            return
        payload = event.payload or {}
        corr_id = payload.get("corr_id")
        if not isinstance(corr_id, str):
            return
        fut = self._corr_futures.pop(corr_id, None)
        if fut is not None and not fut.done():
            fut.set_result(event)

    # ── backstop wakeup ──────────────────────────────────────────────────────

    def start_backstop_task(
        self, interval_seconds: int = BACKSTOP_INTERVAL_SECONDS_DEFAULT
    ) -> asyncio.Task[None]:
        """Spawn a background task that emits SCHEDULED_WAKEUP every `interval_seconds`.

        Safe to call once per loop instance; second call is a no-op.
        """
        if self._backstop_task is not None and not self._backstop_task.done():
            return self._backstop_task

        async def _runner() -> None:
            while not self._stopped.is_set():
                with suppress(asyncio.CancelledError):
                    try:
                        await asyncio.wait_for(
                            self._stopped.wait(), timeout=float(interval_seconds)
                        )
                    except TimeoutError:
                        await self.emit(
                            EventType.SCHEDULED_WAKEUP,
                            interval_seconds=interval_seconds,
                        )

        self._backstop_task = asyncio.create_task(_runner(), name="event_loop_backstop")
        return self._backstop_task

    async def stop(self) -> None:
        """Signal stop, cancel backstop task, drain (best-effort)."""
        self._stopped.set()
        if self._backstop_task is not None:
            self._backstop_task.cancel()
            with suppress(asyncio.CancelledError, BaseException):
                await self._backstop_task
            self._backstop_task = None


__all__ = [
    "ALL_EVENT_TYPES",
    "BACKSTOP_INTERVAL_SECONDS_DEFAULT",
    "Event",
    "EventCallback",
    "EventLoop",
    "EventType",
]
