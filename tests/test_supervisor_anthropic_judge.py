"""Unit tests for AnthropicJudge — M4 real LLM supervisor judge.

All tests use a mock AsyncAnthropic client: NO real API calls are made.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from bmad_orchestrator.supervisor.judges.anthropic_judge import (
    AnthropicJudge,
    _build_system_blocks,
    _build_user_message,
    _parse_verdict,
)
from bmad_orchestrator.supervisor.llm_judge import JudgeError, JudgeInput

# ── helpers ──────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a pipeline supervisor. Classify events into: "
    "auto_respond | pause_workers | abort_pipeline | escalate_human | no_op. "
    "Output strict JSON: {action, confidence, reason}."
)


def _make_message_response(text: str) -> MagicMock:
    """Build a minimal mock that mimics anthropic.types.Message."""
    content_block = MagicMock()
    content_block.text = text
    msg = MagicMock()
    msg.content = [content_block]
    return msg


def _judge(client: Any | None = None, **kwargs: Any) -> AnthropicJudge:
    """Convenience factory so tests don't repeat the system_prompt arg."""
    return AnthropicJudge(
        client=client,
        system_prompt=kwargs.pop("system_prompt", _SYSTEM_PROMPT),
        **kwargs,
    )


def _input(
    event_type: str = "HUMAN_QUERY",
    payload: dict[str, Any] | None = None,
    history: tuple[str, ...] = (),
) -> JudgeInput:
    return JudgeInput(
        event_type=event_type,
        payload=payload or {"story_id": "1.1"},
        history=history,
    )


# ── 1. Happy path ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_happy_path_returns_verdict():
    """Classify returns a valid JudgeVerdict on a well-formed JSON response."""
    raw = json.dumps(
        {"action": "auto_respond", "confidence": 0.92, "reason": "routine query"}
    )
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=_make_message_response(raw))
    judge = _judge(client=client)

    verdict = await judge.classify(_input())

    assert verdict.action == "auto_respond"
    assert verdict.confidence == pytest.approx(0.92)
    assert verdict.reason == "routine query"
    assert verdict.tool_calls == ()


# ── 2. JSON parse fail → JudgeError ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_json_parse_fail_raises_judge_error():
    """Both initial and repair response return garbage → JudgeError."""
    garbage = "not valid json at all {{{ broken"
    client = AsyncMock()
    # Both the original call and the repair retry return garbage.
    client.messages.create = AsyncMock(
        return_value=_make_message_response(garbage)
    )
    judge = _judge(client=client)

    with pytest.raises(JudgeError, match="JSON parse failed"):
        await judge.classify(_input())


# ── 3. Network timeout → JudgeError ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_timeout_raises_judge_error():
    """asyncio.wait_for timeout → JudgeError with timeout message."""

    client = AsyncMock()
    client.messages.create = AsyncMock(side_effect=TimeoutError())
    judge = _judge(client=client, timeout_seconds=0.001)

    with pytest.raises(JudgeError, match="timed out"):
        await judge.classify(_input())


# ── 4. Empty system_prompt → ValueError ──────────────────────────────────────


def test_empty_system_prompt_raises_value_error():
    """AnthropicJudge must refuse an empty system prompt at construction."""
    with pytest.raises(ValueError, match="system_prompt must not be empty"):
        AnthropicJudge(system_prompt="")


def test_whitespace_only_system_prompt_raises_value_error():
    """Whitespace-only system prompt is also invalid."""
    with pytest.raises(ValueError, match="system_prompt must not be empty"):
        AnthropicJudge(system_prompt="   \t\n")


# ── 5. Long history serialised correctly ─────────────────────────────────────


@pytest.mark.asyncio
async def test_long_history_serialised_in_user_message():
    """10 history items are correctly included in the user message."""
    history = tuple(f"reason_{i}" for i in range(10))
    input_ = _input(history=history)
    user_msg = _build_user_message(input_)

    # All 10 history reasons should appear in the serialised message.
    for reason in history:
        assert reason in user_msg

    # Verify the key is present.
    assert "recent_decisions" in user_msg


@pytest.mark.asyncio
async def test_long_history_truncated_to_max_history():
    """History longer than _MAX_HISTORY (10) is capped — oldest dropped."""
    history = tuple(f"reason_{i}" for i in range(15))  # 15 > _MAX_HISTORY=10
    input_ = _input(history=history)
    user_msg = _build_user_message(input_)

    # Newest 10 should be present.
    for i in range(5, 15):
        assert f"reason_{i}" in user_msg

    # Oldest 5 should NOT be present.
    for i in range(5):
        assert f'"reason_{i}"' not in user_msg


# ── 6. Prompt caching — cache_control in system block ─────────────────────────


def test_prompt_caching_cache_control_present():
    """System blocks must contain cache_control=ephemeral for prompt caching."""
    blocks = _build_system_blocks("my prompt")
    assert len(blocks) == 1
    block = blocks[0]
    assert block["type"] == "text"
    assert block["text"] == "my prompt"
    assert block["cache_control"] == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_classify_sends_system_blocks_with_cache_control():
    """AnthropicJudge passes system blocks (with cache_control) to the SDK."""
    raw = json.dumps(
        {"action": "no_op", "confidence": 0.9, "reason": "nothing to do"}
    )
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=_make_message_response(raw))
    judge = _judge(client=client)

    await judge.classify(_input())

    call_kwargs = client.messages.create.call_args.kwargs
    system_arg = call_kwargs.get("system") or client.messages.create.call_args.args[1]
    # system must be a list with at least one block containing cache_control.
    assert isinstance(system_arg, list)
    assert any(
        isinstance(b, dict) and b.get("cache_control") == {"type": "ephemeral"}
        for b in system_arg
    )


# ── 7. Confidence range validation ────────────────────────────────────────────


def test_parse_verdict_confidence_above_one_raises_judge_error():
    """confidence > 1.0 → JudgeError."""
    raw = json.dumps({"action": "auto_respond", "confidence": 1.5, "reason": "x"})
    with pytest.raises(JudgeError, match=r"out of \[0\.0, 1\.0\]"):
        _parse_verdict(raw)


def test_parse_verdict_confidence_below_zero_raises_judge_error():
    """confidence < 0.0 → JudgeError."""
    raw = json.dumps({"action": "no_op", "confidence": -0.1, "reason": "y"})
    with pytest.raises(JudgeError, match=r"out of \[0\.0, 1\.0\]"):
        _parse_verdict(raw)


def test_parse_verdict_confidence_at_boundaries_valid():
    """confidence=0.0 and confidence=1.0 are both valid."""
    for conf in (0.0, 1.0):
        raw = json.dumps({"action": "no_op", "confidence": conf, "reason": "z"})
        verdict = _parse_verdict(raw)
        assert verdict.confidence == pytest.approx(conf)


# ── 8. Action enum validation ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "action",
    [
        "auto_respond",
        "pause_workers",
        "abort_pipeline",
        "escalate_human",
        "no_op",
    ],
)
def test_parse_verdict_all_valid_actions_accepted(action: str):
    """All five valid action values are accepted by _parse_verdict."""
    raw = json.dumps({"action": action, "confidence": 0.8, "reason": "ok"})
    verdict = _parse_verdict(raw)
    assert verdict.action == action


def test_parse_verdict_invalid_action_raises_judge_error():
    """An unknown action value → JudgeError."""
    raw = json.dumps({"action": "delete_everything", "confidence": 0.9, "reason": "!"})
    with pytest.raises(JudgeError, match="invalid action"):
        _parse_verdict(raw)


# ── 9. API error → JudgeError ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_api_error_raises_judge_error():
    """anthropic.APIError (non-200) → JudgeError."""
    import anthropic

    client = AsyncMock()
    api_err = anthropic.APIError(
        message="rate limited", request=MagicMock(), body=None
    )
    api_err.status_code = 429
    client.messages.create = AsyncMock(side_effect=api_err)
    judge = _judge(client=client)

    with pytest.raises(JudgeError, match="Anthropic API error"):
        await judge.classify(_input())


@pytest.mark.asyncio
async def test_classify_connection_error_raises_judge_error():
    """anthropic.APIConnectionError → JudgeError."""
    import anthropic

    client = AsyncMock()
    client.messages.create = AsyncMock(
        side_effect=anthropic.APIConnectionError(request=MagicMock())
    )
    judge = _judge(client=client)

    with pytest.raises(JudgeError, match="Anthropic connection error"):
        await judge.classify(_input())


# ── 10. Repair retry path ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_repair_retry_succeeds_on_second_call():
    """First call returns garbage; repair call returns valid JSON → success."""
    garbage = "not json <<"
    good = json.dumps(
        {"action": "pause_workers", "confidence": 0.88, "reason": "budget exceeded"}
    )
    client = AsyncMock()
    client.messages.create = AsyncMock(
        side_effect=[
            _make_message_response(garbage),
            _make_message_response(good),
        ]
    )
    judge = _judge(client=client)

    verdict = await judge.classify(_input())

    assert verdict.action == "pause_workers"
    assert verdict.confidence == pytest.approx(0.88)
    # Repair call must have been made (2 total calls).
    assert client.messages.create.call_count == 2


# ── 11. Code-fence stripping ──────────────────────────────────────────────────


def test_parse_verdict_strips_code_fences():
    """Model response wrapped in ```json ... ``` is still parsed correctly."""
    inner = json.dumps({"action": "no_op", "confidence": 0.7, "reason": "stripped"})
    fenced = f"```json\n{inner}\n```"
    verdict = _parse_verdict(fenced)
    assert verdict.action == "no_op"
    assert verdict.reason == "stripped"


# ── 12. tool_calls in verdict ─────────────────────────────────────────────────


def test_parse_verdict_with_tool_calls():
    """tool_calls are parsed into ToolCall objects when present."""
    raw = json.dumps(
        {
            "action": "auto_respond",
            "confidence": 0.91,
            "reason": "compliance sweep",
            "tool_calls": [
                {"name": "trigger_compliance_sweep", "args": {}},
            ],
        }
    )
    verdict = _parse_verdict(raw)
    assert len(verdict.tool_calls) == 1
    assert verdict.tool_calls[0].name == "trigger_compliance_sweep"
