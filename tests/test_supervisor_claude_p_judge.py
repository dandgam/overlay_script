"""Unit tests for ClaudePJudge — subscription-mode supervisor judge.

All tests mock ``asyncio.create_subprocess_exec``: NO real ``claude -p``
calls are made.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bmad_orchestrator.supervisor.judges.claude_p_judge import (
    ClaudePJudge,
    _build_prompt,
    _parse_verdict,
    _strip_fences,
)
from bmad_orchestrator.supervisor.llm_judge import JudgeError, JudgeInput

# ── helpers ──────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a pipeline supervisor. Classify events into: "
    "auto_respond | pause_workers | abort_pipeline | escalate_human | no_op. "
    "Output strict JSON."
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


def _judge(**kwargs: Any) -> ClaudePJudge:
    """Convenience factory with defaults."""
    return ClaudePJudge(
        system_prompt=kwargs.pop("system_prompt", _SYSTEM_PROMPT),
        **kwargs,
    )


def _mock_proc(stdout: bytes, stderr: bytes = b"", returncode: int = 0) -> MagicMock:
    """Build a minimal mock mimicking asyncio.subprocess.Process."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


# ── 1. Happy path — clean JSON output ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_happy_path_clean_json():
    """Clean JSON stdout → correct JudgeVerdict."""
    raw = json.dumps(
        {"action": "auto_respond", "confidence": 0.92, "reason": "routine query"}
    )
    proc = _mock_proc(raw.encode())

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        verdict = await _judge().classify(_input())

    assert verdict.action == "auto_respond"
    assert verdict.confidence == pytest.approx(0.92)
    assert verdict.reason == "routine query"
    assert verdict.tool_calls == ()


# ── 2. Output with ```json fencing ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_strips_json_fences():
    """Output wrapped in ```json ... ``` fences is still parsed correctly."""
    inner = json.dumps({"action": "no_op", "confidence": 0.75, "reason": "nothing"})
    fenced = f"```json\n{inner}\n```"
    proc = _mock_proc(fenced.encode())

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        verdict = await _judge().classify(_input())

    assert verdict.action == "no_op"
    assert verdict.reason == "nothing"


# ── 3. Commentary before JSON ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_extracts_json_after_commentary():
    """Commentary prefix before the JSON block is stripped via regex extraction."""
    inner = json.dumps(
        {"action": "escalate_human", "confidence": 0.6, "reason": "unclear"}
    )
    output = f"Sure, here is my answer:\n{inner}"
    proc = _mock_proc(output.encode())

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        verdict = await _judge().classify(_input())

    assert verdict.action == "escalate_human"


# ── 4. Trailing text after JSON ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_parses_json_with_trailing_text():
    """Trailing text after JSON is tolerated via first-{}-block extraction."""
    inner = json.dumps({"action": "pause_workers", "confidence": 0.8, "reason": "budget"})
    output = inner + "\n\nNote: workers paused."
    proc = _mock_proc(output.encode())

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        # json.loads(full) fails on trailing text → fence strip → {} regex extract
        verdict = await _judge().classify(_input())

    assert verdict.action == "pause_workers"


# ── 5. Timeout → JudgeError ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_timeout_raises_judge_error():
    """asyncio.TimeoutError during communicate → JudgeError with 'timed out'."""
    proc = MagicMock()
    proc.communicate = AsyncMock(side_effect=TimeoutError())

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        with pytest.raises(JudgeError, match="timed out"):
            await _judge(timeout_seconds=0.001).classify(_input())


# ── 6. Non-zero exit code → JudgeError ────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_nonzero_exit_raises_judge_error():
    """proc.returncode != 0 → JudgeError containing the stderr text."""
    proc = _mock_proc(b"", b"error: authentication failed", returncode=1)

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        with pytest.raises(JudgeError, match="exited with code 1"):
            await _judge().classify(_input())


# ── 7. Empty stdout → JudgeError ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_empty_stdout_raises_judge_error():
    """Empty stdout → JudgeError."""
    proc = _mock_proc(b"")

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        with pytest.raises(JudgeError, match="empty stdout"):
            await _judge().classify(_input())


# ── 8. JSON fail → repair retry → still fails → JudgeError ──────────────────


@pytest.mark.asyncio
async def test_classify_repair_retry_fails_raises_judge_error():
    """First and repair calls return garbage → JudgeError after repair attempt."""
    garbage = b"not json at all {{ broken"
    # Both calls return garbage.
    proc1 = _mock_proc(garbage)
    proc2 = _mock_proc(garbage)

    with patch(
        "asyncio.create_subprocess_exec", side_effect=[proc1, proc2]
    ):
        with pytest.raises(JudgeError):
            await _judge().classify(_input())


# ── 8b. JSON fail → repair retry → repair succeeds ───────────────────────────


@pytest.mark.asyncio
async def test_classify_repair_retry_succeeds():
    """First call returns garbage; repair call returns valid JSON → success."""
    garbage = b"not json"
    good = json.dumps(
        {"action": "pause_workers", "confidence": 0.88, "reason": "budget exceeded"}
    ).encode()

    proc1 = _mock_proc(garbage)
    proc2 = _mock_proc(good)

    with patch("asyncio.create_subprocess_exec", side_effect=[proc1, proc2]):
        verdict = await _judge().classify(_input())

    assert verdict.action == "pause_workers"
    assert verdict.confidence == pytest.approx(0.88)


# ── 9. Action not in enum → JudgeError ───────────────────────────────────────


def test_parse_verdict_invalid_action_raises():
    """Unknown action value → JudgeError."""
    raw = json.dumps({"action": "delete_everything", "confidence": 0.9, "reason": "!"})
    with pytest.raises(JudgeError, match="invalid action"):
        _parse_verdict(raw)


# ── 10. Confidence > 1.0 → JudgeError ────────────────────────────────────────


def test_parse_verdict_confidence_above_one_raises():
    """confidence > 1.0 → JudgeError."""
    raw = json.dumps({"action": "auto_respond", "confidence": 1.5, "reason": "x"})
    with pytest.raises(JudgeError, match=r"out of \[0\.0, 1\.0\]"):
        _parse_verdict(raw)


# ── 11. tool_calls parsed correctly ──────────────────────────────────────────


def test_parse_verdict_with_tool_calls():
    """tool_calls list is parsed into ToolCall tuple."""
    raw = json.dumps(
        {
            "action": "auto_respond",
            "confidence": 0.91,
            "reason": "compliance sweep",
            "tool_calls": [
                {"name": "trigger_compliance_sweep", "args": {"mode": "full"}},
            ],
        }
    )
    verdict = _parse_verdict(raw)
    assert len(verdict.tool_calls) == 1
    assert verdict.tool_calls[0].name == "trigger_compliance_sweep"
    assert verdict.tool_calls[0].args == {"mode": "full"}


# ── 12. History (10 items) present in prompt ─────────────────────────────────


def test_build_prompt_history_included():
    """10 prior decisions appear in the constructed prompt string."""
    history = tuple(f"decision_{i}" for i in range(10))
    inp = _input(history=history)
    prompt = _build_prompt(_SYSTEM_PROMPT, inp)

    for reason in history:
        assert reason in prompt


def test_build_prompt_history_truncated_to_10():
    """History longer than 10 is truncated to the most recent 10."""
    history = tuple(f"h_{i}" for i in range(15))
    inp = _input(history=history)
    prompt = _build_prompt(_SYSTEM_PROMPT, inp)

    # Most recent 10 present.
    for i in range(5, 15):
        assert f"h_{i}" in prompt

    # Oldest 5 absent.
    for i in range(5):
        assert f'"h_{i}"' not in prompt


# ── 13. _strip_fences unit tests ─────────────────────────────────────────────


def test_strip_fences_backtick_json():
    inner = '{"a": 1}'
    assert _strip_fences(f"```json\n{inner}\n```") == inner


def test_strip_fences_plain_backticks():
    inner = '{"b": 2}'
    assert _strip_fences(f"```\n{inner}\n```") == inner


def test_strip_fences_no_fences_passthrough():
    raw = '{"c": 3}'
    assert _strip_fences(raw) == raw
