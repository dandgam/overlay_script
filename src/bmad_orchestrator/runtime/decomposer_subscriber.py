"""Decomposer subscriber — Initiative pilot_findings_closure S3 (#3 rycag 2).

Subscribes to ``WORKER_HALT_FILE`` events. When the payload carries
``halt_reason="loc_cap_exceeded"`` (Sonnet auto-fix exceeded the 300-LOC diff
budget per Patch B safety guard in ``bmad-auto-dev-runner.sh``), this subscriber
triggers the existing decomposer pipeline (``runtime.auto_split``) on the
parent story instead of leaving it stuck on a manual override.

Wiring intent:
  * Subscriber is opt-in via ``BMAD_AUTO_SPLIT=1`` (same gate as the inline
    ``_run_real_pilot`` path) — when off, we emit ``STORY_AUTO_SPLIT`` with
    ``triggered=False`` so observers see why no split happened.
  * On trigger, emit ``STORY_AUTO_SPLIT`` carrying the parent story id, the
    halt reason, and any decomposer outcome (success → sub_ids; failure →
    error string). Actual ``auto_split_and_execute`` invocation requires a
    decompose_fn + worktree context; this subscriber emits the event and
    delegates execution to the orchestrator pilot loop, which already wires
    the production decomposer (``runtime/decomposer.py``).

Idempotency:
  * Events without ``halt_reason="loc_cap_exceeded"`` are ignored silently.
  * Re-emission of the same WORKER_HALT_FILE (same story+halt_reason)
    produces another STORY_AUTO_SPLIT; downstream consumers de-dupe by
    ``parent_story_id`` + ``halted_at`` if needed.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from bmad_orchestrator.runtime.auto_split import AUTO_SPLIT_ENV_VAR
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType

log = structlog.get_logger("decomposer_subscriber")

LOC_CAP_EXCEEDED_REASON = "loc_cap_exceeded"


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def auto_split_subscriber_enabled(
    env: dict[str, str] | os._Environ[str] | None = None,
) -> bool:
    """Subscriber active only when ``BMAD_AUTO_SPLIT`` env flag is truthy.

    Same gate as the inline ``auto_split_enabled()`` in ``runtime/auto_split.py``
    so the subscriber and the production pilot path move together.
    """
    env_map = env if env is not None else os.environ
    return _truthy(env_map.get(AUTO_SPLIT_ENV_VAR))


async def decomposer_subscriber(event: Event, bus: EventLoop) -> None:
    """EventLoop callback — convert qualifying WORKER_HALT_FILE → STORY_AUTO_SPLIT.

    Filters:
      * event.type must be WORKER_HALT_FILE.
      * payload['halt_reason'] must equal ``loc_cap_exceeded``.
      * payload['story_id'] must be a non-empty string.

    On match, emits one STORY_AUTO_SPLIT carrying:
      * parent_story_id
      * halt_reason
      * triggered: bool (False when BMAD_AUTO_SPLIT is off)
      * source_event_at: original WORKER_HALT_FILE timestamp (for dedup)
    """
    if event.type != EventType.WORKER_HALT_FILE:
        return

    payload = dict(event.payload or {})
    halt_reason = payload.get("halt_reason")
    if halt_reason != LOC_CAP_EXCEEDED_REASON:
        return

    story_id = payload.get("story_id")
    if not isinstance(story_id, str) or not story_id:
        log.warning(
            "decomposer_subscriber_skip_missing_story_id",
            payload=payload,
        )
        return

    triggered = auto_split_subscriber_enabled()
    out_payload: dict[str, Any] = {
        "parent_story_id": story_id,
        "halt_reason": halt_reason,
        "triggered": triggered,
        "source_event_at": event.emitted_at,
        "source": "decomposer_subscriber",
    }
    worktree = payload.get("worktree")
    if isinstance(worktree, str) and worktree:
        out_payload["worktree"] = worktree

    if not triggered:
        log.info(
            "decomposer_subscriber_disabled",
            story_id=story_id,
            hint=f"set {AUTO_SPLIT_ENV_VAR}=1 to enable auto-split on LOC-cap halts",
        )
    else:
        log.info(
            "decomposer_subscriber_triggered",
            story_id=story_id,
            halt_reason=halt_reason,
        )

    await bus.emit(EventType.STORY_AUTO_SPLIT, **out_payload)


SubscriberCallable = Callable[[Event, EventLoop], Awaitable[None]]


__all__ = [
    "LOC_CAP_EXCEEDED_REASON",
    "SubscriberCallable",
    "auto_split_subscriber_enabled",
    "decomposer_subscriber",
]
