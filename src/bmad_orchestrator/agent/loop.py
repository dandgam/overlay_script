"""Event-driven wakeup loop. См. spec §4 events.

Event types (spec §4):
- worker_completed       — JSONL stream ended
- worker_halt_file       — halt-reason.txt появился
- worker_elicitation     — worker задал вопрос
- budget_threshold_hit   — deterministic guard сработал
- wave_boundary_reached  — последняя story wave done
- epic_boundary_reached  — последняя story эпика done
- phase4_complete        — все waves done
- human_response         — ответ на эскалацию
- user_chat_message      — ты написала в Telegram
- scheduled_wakeup_5min  — backstop
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any


class EventType(str, Enum):
    WORKER_COMPLETED = "worker_completed"
    WORKER_HALT_FILE = "worker_halt_file"
    WORKER_ELICITATION = "worker_elicitation"
    BUDGET_THRESHOLD_HIT = "budget_threshold_hit"
    WAVE_BOUNDARY_REACHED = "wave_boundary_reached"
    EPIC_BOUNDARY_REACHED = "epic_boundary_reached"
    PHASE4_COMPLETE = "phase4_complete"
    HUMAN_RESPONSE = "human_response"
    USER_CHAT_MESSAGE = "user_chat_message"
    SCHEDULED_WAKEUP = "scheduled_wakeup_5min"


@dataclass
class Event:
    type: EventType
    payload: dict[str, Any]


class EventLoop:
    """Single producer/multi-consumer queue для агентских пробуждений."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[Event] = asyncio.Queue()

    async def emit(self, event: Event) -> None:
        await self.queue.put(event)

    async def next(self) -> Event:
        return await self.queue.get()
