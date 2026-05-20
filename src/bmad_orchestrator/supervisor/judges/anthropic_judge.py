"""Anthropic-backed Tier 1 LLM judge for SupervisorEngine.

Implements ``LLMJudgeProtocol`` using ``anthropic.AsyncAnthropic`` (Sonnet).

Design notes
------------
* **Prompt caching** — system prompt is sent with ``cache_control={"type":
  "ephemeral"}`` so repeated calls within the same session hit the 5-min cache
  at Anthropic's edge. Cache hit rate <50% is considered a bug.
* **JSON-only output** — system prompt instructs strict JSON; we parse the
  response text. On first parse fail we do a single retry with an explicit
  repair instruction. On second fail we raise ``JudgeError``.
* **Timeout** — ``asyncio.wait_for`` wraps the SDK call with
  ``timeout_seconds`` (default 5s). The underlying ``httpx`` client also
  receives the same timeout.
* **Failure modes → JudgeError**:
  - ``asyncio.TimeoutError`` (deadline exceeded)
  - ``anthropic.APIConnectionError`` / ``anthropic.APIError`` (network / HTTP)
  - ``json.JSONDecodeError`` after two parse attempts
  - Pydantic ``ValidationError`` when parsed JSON doesn't match expected schema
  - Confidence out of [0.0, 1.0] range
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import anthropic

from bmad_orchestrator.supervisor.llm_judge import (
    JudgeError,
    JudgeInput,
    JudgeVerdict,
)
from bmad_orchestrator.supervisor.policy import ToolCall

log = logging.getLogger(__name__)

# Actions the judge is allowed to return — used for enum validation.
_VALID_ACTIONS: frozenset[str] = frozenset(
    {
        "auto_respond",
        "pause_workers",
        "abort_pipeline",
        "escalate_human",
        "no_op",
    }
)

# Maximum number of history reason strings serialised into user message.
_MAX_HISTORY = 10

# One retry prompt when first JSON parse fails.
_REPAIR_PROMPT = (
    "Your previous response was not valid JSON. "
    "Return only a JSON object with keys: action, confidence, reason. "
    "No markdown, no code fences, no preamble."
)


def _build_system_blocks(system_prompt: str) -> list[dict[str, Any]]:
    """Return system content list with prompt-caching header."""
    return [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _build_user_message(input_: JudgeInput) -> str:
    """Serialise JudgeInput into a compact user message."""
    parts = [
        f"event_type: {input_.event_type}",
        f"payload: {json.dumps(input_.payload, default=str)}",
    ]
    if input_.history:
        recent = list(input_.history)[-_MAX_HISTORY:]
        parts.append(f"recent_decisions: {json.dumps(recent)}")
    parts.append(
        "Respond with strict JSON: "
        '{"action": "<action>", "confidence": <float>, "reason": "<str>", '
        '"tool_calls": [{"name": "<str>", "args": {}}]}'
        " tool_calls is optional."
    )
    return "\n".join(parts)


def _parse_verdict(raw: str) -> JudgeVerdict:
    """Parse LLM text → JudgeVerdict, raise JudgeError on any mismatch."""
    raw = raw.strip()
    # Strip code fences if the model ignores the "no markdown" instruction.
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(
            line for line in lines if not line.startswith("```")
        ).strip()

    try:
        data: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise JudgeError(f"JSON parse failed: {exc}. Raw: {raw[:200]!r}") from exc

    action = data.get("action", "")
    if action not in _VALID_ACTIONS:
        raise JudgeError(
            f"invalid action {action!r}; expected one of {sorted(_VALID_ACTIONS)}"
        )

    try:
        confidence = float(data["confidence"])
    except (KeyError, TypeError, ValueError) as exc:
        raise JudgeError(f"confidence missing or not float: {exc}") from exc

    if not (0.0 <= confidence <= 1.0):
        raise JudgeError(
            f"confidence {confidence} out of [0.0, 1.0] range"
        )

    reason = str(data.get("reason", ""))

    raw_tool_calls: list[Any] = data.get("tool_calls") or []
    tool_calls: tuple[ToolCall, ...] = ()
    if raw_tool_calls:
        try:
            tool_calls = tuple(
                ToolCall(name=tc["name"], args=tc.get("args", {}))
                for tc in raw_tool_calls
                if isinstance(tc, dict) and "name" in tc
            )
        except Exception as exc:
            raise JudgeError(f"tool_calls parse failed: {exc}") from exc

    return JudgeVerdict(
        action=action,  # type: ignore[arg-type]
        confidence=confidence,
        reason=reason,
        tool_calls=tool_calls,
    )


class AnthropicJudge:
    """Sonnet-backed supervisor judge.

    Implements ``LLMJudgeProtocol``; drop-in replacement for ``StubJudge``.

    Parameters
    ----------
    client:
        Optional pre-built ``AsyncAnthropic`` instance (test injection).
        If not provided, a new client is created with the ``ANTHROPIC_API_KEY``
        env var and the specified timeout.
    model:
        Anthropic model ID. Default ``claude-sonnet-4-6``.
    system_prompt:
        Supervisor instruction prompt. Sent with ``cache_control`` for 5-min
        caching. Must not be empty — raises ``ValueError`` at init time.
    timeout_seconds:
        Per-call deadline (both asyncio and httpx).  Default 5.0 s.
    max_tokens:
        Maximum tokens in the judge response.  512 is ample for a JSON blob.
    """

    def __init__(
        self,
        client: anthropic.AsyncAnthropic | None = None,
        model: str = "claude-sonnet-4-6",
        system_prompt: str = "",
        timeout_seconds: float = 30.0,
        max_tokens: int = 512,
    ) -> None:
        if not system_prompt.strip():
            raise ValueError("AnthropicJudge system_prompt must not be empty")
        self._model = model
        self._system_prompt = system_prompt
        self._timeout = timeout_seconds
        self._max_tokens = max_tokens
        self._client: anthropic.AsyncAnthropic = client or anthropic.AsyncAnthropic(
            timeout=timeout_seconds,
        )

    async def classify(self, input_: JudgeInput) -> JudgeVerdict:
        """Classify an event; raise ``JudgeError`` on any failure."""
        user_msg = _build_user_message(input_)
        system_blocks = _build_system_blocks(self._system_prompt)

        try:
            response = await asyncio.wait_for(
                self._client.messages.create(
                    model=self._model,
                    max_tokens=self._max_tokens,
                    system=system_blocks,  # type: ignore[arg-type]
                    messages=[{"role": "user", "content": user_msg}],
                ),
                timeout=self._timeout,
            )
        except TimeoutError as exc:
            raise JudgeError(
                f"Anthropic judge timed out after {self._timeout}s"
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise JudgeError(f"Anthropic connection error: {exc}") from exc
        except anthropic.APIError as exc:
            raise JudgeError(f"Anthropic API error {exc.status_code}: {exc}") from exc

        raw_text = response.content[0].text if response.content else ""
        try:
            return _parse_verdict(raw_text)
        except JudgeError:
            # Single repair retry with explicit correction instruction.
            log.debug(
                "anthropic_judge_first_parse_failed_retrying",
                event_type=input_.event_type,
                raw_preview=raw_text[:100],
            )
        try:
            repair_response = await asyncio.wait_for(
                self._client.messages.create(
                    model=self._model,
                    max_tokens=self._max_tokens,
                    system=system_blocks,  # type: ignore[arg-type]
                    messages=[
                        {"role": "user", "content": user_msg},
                        {"role": "assistant", "content": raw_text},
                        {"role": "user", "content": _REPAIR_PROMPT},
                    ],
                ),
                timeout=self._timeout,
            )
        except TimeoutError as exc:
            raise JudgeError(
                f"Anthropic judge repair call timed out after {self._timeout}s"
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise JudgeError(f"Anthropic connection error on repair: {exc}") from exc
        except anthropic.APIError as exc:
            raise JudgeError(
                f"Anthropic API error on repair {exc.status_code}: {exc}"
            ) from exc

        repair_text = (
            repair_response.content[0].text if repair_response.content else ""
        )
        return _parse_verdict(repair_text)  # raises JudgeError on second fail


__all__ = ["AnthropicJudge"]
