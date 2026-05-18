"""Bus subscriber wiring SelfLearning Consolidator into the event loop.

Subscribes to 4 trigger events:
  WAVE_BOUNDARY_REACHED, EPIC_BOUNDARY_REACHED, PHASE4_COMPLETE,
  MONTHLY_REVIEW_SCHEDULED

Anti-self-loop: events where payload['source'] == 'self_learning' are ignored
to prevent the subscriber from consuming events it emitted itself.

See spec/spec_self_learning_loop.md §3.2.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import structlog

from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.self_learning.config import (
    PolicyNotFoundError,
    PolicyValidationError,
    SelfLearningConfig,
    load_config,
)
from bmad_orchestrator.self_learning.consolidator import Consolidator

log = structlog.get_logger("self_learning_subscriber")

DEFAULT_CONFIG_PATH = Path("config/self-learning.yaml")

WATCHED_EVENT_TYPES: frozenset[EventType] = frozenset(
    {
        EventType.WAVE_BOUNDARY_REACHED,
        EventType.EPIC_BOUNDARY_REACHED,
        EventType.PHASE4_COMPLETE,
        EventType.MONTHLY_REVIEW_SCHEDULED,
    }
)


def load_self_learning_config(path: Path | None = None) -> SelfLearningConfig:
    """Load SelfLearningConfig from YAML path.

    Falls back to DEFAULT_CONFIG_PATH. If not found, returns disabled config
    (fail-safe: no auto-apply if config missing).
    """
    target = path or DEFAULT_CONFIG_PATH
    try:
        cfg = load_config(target)
        log.info("self_learning_config_loaded", path=str(target), enabled=cfg.enabled)
        return cfg
    except PolicyNotFoundError:
        log.warning(
            "self_learning_config_missing_using_disabled",
            attempted=str(target),
            hint="self-learning disabled until config/self-learning.yaml is present",
        )
        return SelfLearningConfig(version=1, enabled=False)
    except PolicyValidationError as exc:
        log.error(
            "self_learning_config_invalid_using_disabled",
            attempted=str(target),
            error=str(exc),
        )
        return SelfLearningConfig(version=1, enabled=False)


def make_self_learning_subscriber(
    consolidator: Consolidator,
) -> Callable[[Event, EventLoop], Awaitable[None]]:
    """Build an EventLoop-compatible subscriber bound to ``consolidator``."""

    async def self_learning_subscriber(event: Event, bus: EventLoop) -> None:
        if event.type not in WATCHED_EVENT_TYPES:
            return

        # Anti-self-loop: skip events we emitted ourselves
        if (event.payload or {}).get("source") == "self_learning":
            return

        trigger = event.type.value
        log.info("self_learning_triggered", event_type=trigger)
        try:
            await consolidator.run(trigger_event=trigger)
        except Exception as exc:
            log.exception("self_learning_consolidator_failed", error=str(exc))

    return self_learning_subscriber


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "WATCHED_EVENT_TYPES",
    "load_self_learning_config",
    "make_self_learning_subscriber",
]
