"""Event-driven wakeup loop (spec §4 — 13 event types + 5-min backstop).

Producers (S3+): worker_spawn (subprocess watchers), bot/telegram_bot, safety guards,
scheduled tasks. Consumer: agent/loop top-level orchestrator (one consumer per session).

Spec §4 events:
    worker_completed, worker_halt_file, worker_elicitation, budget_threshold_hit,
    wave_boundary_reached, epic_boundary_reached, user_chat_message,
    monthly_review_scheduled, voice_message_received, story_split_triggered,
    phase4_complete, human_response, scheduled_wakeup_5min.

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
    """Spec §4 — все 13 типов + FS4 HUMAN_QUERY."""

    WORKER_COMPLETED = "worker_completed"
    WORKER_HALT_FILE = "worker_halt_file"
    WORKER_ELICITATION = "worker_elicitation"
    BUDGET_THRESHOLD_HIT = "budget_threshold_hit"
    WAVE_BOUNDARY_REACHED = "wave_boundary_reached"
    EPIC_BOUNDARY_REACHED = "epic_boundary_reached"
    USER_CHAT_MESSAGE = "user_chat_message"
    MONTHLY_REVIEW_SCHEDULED = "monthly_review_scheduled"
    VOICE_MESSAGE_RECEIVED = "voice_message_received"
    STORY_SPLIT_TRIGGERED = "story_split_triggered"
    PHASE4_COMPLETE = "phase4_complete"
    HUMAN_QUERY = "human_query"
    HUMAN_RESPONSE = "human_response"
    SCHEDULED_WAKEUP = "scheduled_wakeup_5min"
    CODE_REVIEW_VERDICT = "code_review_verdict"


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

    # ── public producer API ──────────────────────────────────────────────────

    async def emit(self, event_or_type: Event | EventType, **payload: Any) -> Event:
        """Emit an event. Accept either Event or (EventType, **payload)."""
        if isinstance(event_or_type, EventType):
            event = Event(type=event_or_type, payload=dict(payload))
        else:
            event = event_or_type
        await self.queue.put(event)
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
        async with self._lock:
            for cb in list(self._subs):
                await cb(event)
        return event

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
