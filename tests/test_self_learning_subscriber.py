"""Tests for runtime/self_learning_subscriber — M3."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.runtime.self_learning_subscriber import (
    WATCHED_EVENT_TYPES,
    load_self_learning_config,
    make_self_learning_subscriber,
)
from bmad_orchestrator.self_learning.config import SelfLearningConfig, SelfLearningDefaults
from bmad_orchestrator.self_learning.consolidator import Consolidator


@pytest.fixture()
def default_config() -> SelfLearningConfig:
    return SelfLearningConfig(
        version=1, enabled=True, defaults=SelfLearningDefaults()
    )


def test_watched_event_types_contains_four_events() -> None:
    assert EventType.WAVE_BOUNDARY_REACHED in WATCHED_EVENT_TYPES
    assert EventType.EPIC_BOUNDARY_REACHED in WATCHED_EVENT_TYPES
    assert EventType.PHASE4_COMPLETE in WATCHED_EVENT_TYPES
    assert EventType.MONTHLY_REVIEW_SCHEDULED in WATCHED_EVENT_TYPES
    assert len(WATCHED_EVENT_TYPES) == 4


def test_load_config_missing_file_returns_disabled() -> None:
    cfg = load_self_learning_config(Path("/nonexistent/path.yaml"))
    assert cfg.enabled is False


def test_load_config_from_real_yaml() -> None:
    cfg = load_self_learning_config(Path("config/self-learning.yaml"))
    assert cfg.enabled is True
    assert cfg.version == 1


def test_subscriber_ignores_non_watched_events(default_config: SelfLearningConfig) -> None:
    consolidator = MagicMock(spec=Consolidator)
    sub = make_self_learning_subscriber(consolidator)
    bus = EventLoop()
    event = Event(type=EventType.WORKER_COMPLETED, payload={})
    asyncio.run(sub(event, bus))
    consolidator.run.assert_not_called()


def test_subscriber_ignores_self_emitted_events(default_config: SelfLearningConfig) -> None:
    consolidator = MagicMock(spec=Consolidator)
    sub = make_self_learning_subscriber(consolidator)
    bus = EventLoop()
    event = Event(
        type=EventType.WAVE_BOUNDARY_REACHED,
        payload={"source": "self_learning"},
    )
    asyncio.run(sub(event, bus))
    consolidator.run.assert_not_called()


def test_subscriber_triggers_consolidator_on_wave_boundary(
    default_config: SelfLearningConfig,
) -> None:
    consolidator = MagicMock(spec=Consolidator)
    consolidator.run = AsyncMock(return_value=MagicMock(total_proposals=0))
    sub = make_self_learning_subscriber(consolidator)
    bus = EventLoop()
    event = Event(type=EventType.WAVE_BOUNDARY_REACHED, payload={})
    asyncio.run(sub(event, bus))
    consolidator.run.assert_called_once_with(trigger_event="wave_boundary_reached")


def test_subscriber_triggers_on_monthly_review(
    default_config: SelfLearningConfig,
) -> None:
    consolidator = MagicMock(spec=Consolidator)
    consolidator.run = AsyncMock(return_value=MagicMock(total_proposals=0))
    sub = make_self_learning_subscriber(consolidator)
    bus = EventLoop()
    event = Event(type=EventType.MONTHLY_REVIEW_SCHEDULED, payload={})
    asyncio.run(sub(event, bus))
    consolidator.run.assert_called_once_with(trigger_event="monthly_review_scheduled")


def test_subscriber_swallows_consolidator_errors(
    default_config: SelfLearningConfig,
) -> None:
    consolidator = MagicMock(spec=Consolidator)
    consolidator.run = AsyncMock(side_effect=RuntimeError("consolidator crash"))
    sub = make_self_learning_subscriber(consolidator)
    bus = EventLoop()
    event = Event(type=EventType.PHASE4_COMPLETE, payload={})
    # Must not raise
    asyncio.run(sub(event, bus))
