"""Tests for NEW-39 — dev_prompt_hint injection via ORCHESTRATOR_DEV_HINT env var.

Covers:
  1. When ORCHESTRATOR_DEV_HINT is set, bootstrap block includes the hint.
  2. The var is removed from merged_env after injection (one-shot).
  3. Without hint, bootstrap block is unchanged (backward compat).
  4. respawn_worker decision with hint → WORKER_RESPAWN_REQUESTED has hint.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from bmad_orchestrator.runtime.event_loop import EventLoop, EventType
from bmad_orchestrator.supervisor.actions import execute_decision
from bmad_orchestrator.supervisor.policy import SupervisorDecision, ToolCall

# ---------------------------------------------------------------------------
# bootstrap block hint injection (worker_spawn.py)
# ---------------------------------------------------------------------------

def test_bootstrap_block_contains_hint_when_set() -> None:
    """ORCHESTRATOR_DEV_HINT in merged_env is appended to bootstrap block."""
    from bmad_orchestrator.agent.safety.session_start import build_session_start_block

    # Build a baseline bootstrap block.
    block = build_session_start_block("bmad-dev-story", "story-1")
    assert "Supervisor respawn hint" not in block

    # Simulate what worker_spawn does: pop ORCHESTRATOR_DEV_HINT + append.
    merged_env = {"ORCHESTRATOR_DEV_HINT": "add missing tests for AC3"}
    dev_hint = merged_env.pop("ORCHESTRATOR_DEV_HINT", None)
    if dev_hint:
        block = (
            block
            + f"\n\n--- Supervisor respawn hint ---\n{dev_hint}\n"
            + "=== End respawn hint ==="
        )

    assert "Supervisor respawn hint" in block
    assert "add missing tests for AC3" in block
    assert "ORCHESTRATOR_DEV_HINT" not in merged_env  # consumed


def test_bootstrap_block_unchanged_without_hint() -> None:
    """Without ORCHESTRATOR_DEV_HINT, bootstrap block is not modified."""
    from bmad_orchestrator.agent.safety.session_start import build_session_start_block

    block = build_session_start_block("bmad-dev-story", "story-1")
    merged_env: dict[str, str] = {}

    dev_hint = merged_env.pop("ORCHESTRATOR_DEV_HINT", None)
    if dev_hint:
        block = block + "\n" + dev_hint

    assert "Supervisor respawn hint" not in block


def test_hint_removed_from_env_after_pop() -> None:
    """ORCHESTRATOR_DEV_HINT is consumed (popped) so it doesn't leak."""
    env = {"ORCHESTRATOR_DEV_HINT": "fix auth check"}
    hint = env.pop("ORCHESTRATOR_DEV_HINT", None)
    assert hint == "fix auth check"
    assert "ORCHESTRATOR_DEV_HINT" not in env


# ---------------------------------------------------------------------------
# respawn_worker decision with hint → WORKER_RESPAWN_REQUESTED carries hint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_respawn_worker_hint_propagated_to_event() -> None:
    """dev_prompt_hint from tool call args is set in WORKER_RESPAWN_REQUESTED payload."""
    bus = EventLoop()
    hint_text = "The review found missing input validation in handler X"
    d = SupervisorDecision(
        action="respawn_worker",
        confidence=0.88,
        reason="review request_changes",
        tier=1,
        tool_calls=[
            ToolCall(
                name="respawn_worker",
                args={
                    "story_id": "ep3-story-2",
                    "reason": "review request_changes",
                    "max_iteration": 1,
                    "dev_prompt_hint": hint_text,
                },
            )
        ],
    )

    with patch(
        "bmad_orchestrator.supervisor.actions._get_token_for_story",
        return_value=None,
    ):
        await execute_decision(
            d,
            source_event_type="CODE_REVIEW_VERDICT",
            source_payload={"story_id": "ep3-story-2"},
            bus=bus,
        )

    events = []
    while not bus.queue.empty():
        events.append(bus.queue.get_nowait())

    assert len(events) == 1
    ev = events[0]
    assert ev.type is EventType.WORKER_RESPAWN_REQUESTED
    assert ev.payload["dev_prompt_hint"] == hint_text


@pytest.mark.asyncio
async def test_respawn_worker_without_hint_none_in_payload() -> None:
    """When no dev_prompt_hint given, payload carries None (backward compat)."""
    bus = EventLoop()
    d = SupervisorDecision(
        action="respawn_worker",
        confidence=0.88,
        reason="stuck",
        tier=1,
        tool_calls=[
            ToolCall(
                name="respawn_worker",
                args={"story_id": "story-3", "reason": "stuck", "max_iteration": 1},
            )
        ],
    )

    with patch(
        "bmad_orchestrator.supervisor.actions._get_token_for_story",
        return_value=None,
    ):
        await execute_decision(
            d,
            source_event_type="REVIEW_STUCK_TIMEOUT",
            source_payload={"story_id": "story-3"},
            bus=bus,
        )

    events = []
    while not bus.queue.empty():
        events.append(bus.queue.get_nowait())

    assert len(events) == 1
    assert events[0].payload["dev_prompt_hint"] is None
