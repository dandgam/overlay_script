"""ClaudePJudge — Tier 1 LLM judge via ``claude -p`` CLI subprocess.

Implements ``LLMJudgeProtocol`` using the Claude Code subscription (no API
key required). Spawns ``claude -p <prompt>`` as an asyncio subprocess and
parses the JSON output into a ``JudgeVerdict``.

Design notes
------------
* **Transport** — ``asyncio.create_subprocess_exec`` with stdin=DEVNULL,
  stdout=PIPE, stderr=PIPE.  Timeout wraps ``proc.communicate()``.
* **Prompt structure** — system_prompt + JSON-encoded JudgeInput + explicit
  "output strict JSON" instruction.  Matches what AnthropicJudge injects via
  SDK messages, but collapsed into a single flat prompt for the CLI.
* **JSON recovery** — three-layer parse attempt:
    1. Clean ``json.loads`` on stripped output.
    2. Regex strip Markdown fences (```...```) then re-parse.
    3. One repair subprocess call with explicit correction instruction.
  On third failure raises ``JudgeError`` (engine falls back to Tier 2).
* **Failure modes → JudgeError**:
  - ``asyncio.TimeoutError`` (process exceeds ``timeout_seconds``)
  - ``proc.returncode != 0`` (CLI non-zero exit)
  - Empty stdout
  - JSON decode failure after fences + 1 repair
  - Action value not in ``_VALID_ACTIONS``
  - Confidence outside [0.0, 1.0]
* **Timeout default = 30 s** — CLI subprocess is slower than SDK (auth,
  process startup, shell init) so the timeout is higher than AnthropicJudge's
  5 s default.

Cf. AnthropicJudge — same LLMJudgeProtocol, different transport.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from bmad_orchestrator.supervisor.llm_judge import (
    JudgeError,
    JudgeInput,
    JudgeVerdict,
)
from bmad_orchestrator.supervisor.policy import ToolCall

log = logging.getLogger(__name__)

# Actions the judge is allowed to return.
_VALID_ACTIONS: frozenset[str] = frozenset(
    {
        "auto_respond",
        "pause_workers",
        "abort_pipeline",
        "escalate_human",
        "no_op",
    }
)

_MAX_HISTORY = 10

# Regex for stripping Markdown code fences (```json ... ``` or ``` ... ```).
_FENCE_RE = re.compile(r"```[a-zA-Z]*\n?(.*?)```", re.DOTALL)

_REPAIR_INSTRUCTION = (
    "Your previous response was not valid JSON. "
    "Return ONLY a JSON object with keys: action, confidence, reason. "
    "No markdown, no code fences, no preamble. Example:\n"
    '{"action":"no_op","confidence":0.9,"reason":"ok"}'
)


def _build_prompt(system_prompt: str, input_: JudgeInput) -> str:
    """Build the flat prompt string sent to ``claude -p``."""
    parts: list[str] = []

    if system_prompt.strip():
        parts.append(system_prompt.strip())
        parts.append("")

    parts.append("Event to classify:")
    parts.append(f"  event_type: {input_.event_type}")
    parts.append(f"  payload: {json.dumps(input_.payload, default=str, indent=2)}")

    if input_.history:
        recent = list(input_.history)[-_MAX_HISTORY:]
        parts.append(f"  recent_decisions: {json.dumps(recent)}")

    parts.append("")
    parts.append(
        'Respond with strict JSON ONLY — no markdown, no commentary, no ``` fences:\n'
        '{"action": "auto_respond|pause_workers|abort_pipeline|escalate_human|no_op",\n'
        ' "confidence": 0.0-1.0,\n'
        ' "reason": "<short explanation>",\n'
        ' "tool_calls": [{"name": "...", "args": {}}]}\n'
        "tool_calls is optional."
    )

    return "\n".join(parts)


def _strip_fences(text: str) -> str:
    """Remove Markdown code fences; return stripped content or original."""
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    # Also handle leading ``` without closing ``` (truncated output).
    if text.strip().startswith("```"):
        lines = text.strip().splitlines()
        inner = [ln for ln in lines if not ln.startswith("```")]
        return "\n".join(inner).strip()
    return text


def _parse_verdict(raw: str) -> JudgeVerdict:
    """Parse LLM text → JudgeVerdict; raises JudgeError on any mismatch."""
    raw = raw.strip()

    # Attempt 1: direct parse.
    try:
        data: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError:
        # Attempt 2: strip fences and surrounding commentary.
        stripped = _strip_fences(raw)
        # If there's still non-JSON prefix (commentary), try to extract the
        # first {...} block via regex.
        json_match = re.search(r"\{.*\}", stripped, re.DOTALL)
        if json_match:
            stripped = json_match.group(0)
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise JudgeError(
                f"JSON parse failed after fence strip: {exc}. Raw: {raw[:200]!r}"
            ) from exc

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
        raise JudgeError(f"confidence {confidence} out of [0.0, 1.0] range")

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


class ClaudePJudge:
    """Supervisor judge via ``claude -p`` CLI (Claude Code subscription).

    Spawns an asyncio subprocess ``claude -p <prompt>`` where the prompt
    encodes the system instruction + JSON-serialised ``JudgeInput``.  Output
    is parsed as JSON into a ``JudgeVerdict``.

    Use this in production environments without ``ANTHROPIC_API_KEY``
    (subscription auth via Claude Code CLI).  No bwrap isolation needed —
    classification calls are short and don't execute generated code.

    Cf. ``AnthropicJudge`` — same protocol, different transport.

    Parameters
    ----------
    claude_bin:
        Path to the ``claude`` CLI binary.  Default matches the production
        install path ``/home/server/.local/bin/claude``.
    model:
        Claude model ID passed via ``--model``.  Default ``claude-sonnet-4-6``.
    system_prompt:
        Supervisor instruction block prepended to every classify call.
    timeout_seconds:
        Per-call wall-clock deadline.  Default 30 s (higher than AnthropicJudge's
        5 s because CLI subprocess has process startup + auth overhead).
    max_repair_attempts:
        Number of repair retry subprocess calls on JSON parse failure.
        Default 1 — one extra call with explicit correction instruction.
    """

    def __init__(
        self,
        claude_bin: str = "/home/server/.local/bin/claude",
        model: str = "claude-sonnet-4-6",
        system_prompt: str = "",
        timeout_seconds: float = 30.0,
        max_repair_attempts: int = 1,
    ) -> None:
        self._claude_bin = claude_bin
        self._model = model
        self._system_prompt = system_prompt
        self._timeout = timeout_seconds
        self._max_repair_attempts = max_repair_attempts

    async def _run_claude(self, prompt: str) -> str:
        """Spawn ``claude -p`` subprocess and return stripped stdout."""
        try:
            proc = await asyncio.create_subprocess_exec(
                self._claude_bin,
                "-p",
                "--model",
                self._model,
                prompt,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout
            )
        except TimeoutError as exc:
            raise JudgeError(
                f"ClaudePJudge timed out after {self._timeout}s"
            ) from exc

        if proc.returncode != 0:
            stderr_text = stderr_bytes.decode(errors="replace").strip()
            raise JudgeError(
                f"claude -p exited with code {proc.returncode}: {stderr_text[:300]}"
            )

        return stdout_bytes.decode(errors="replace").strip()

    async def classify(self, input_: JudgeInput) -> JudgeVerdict:
        """Classify an event; raise ``JudgeError`` on any failure."""
        prompt = _build_prompt(self._system_prompt, input_)
        raw = await self._run_claude(prompt)

        if not raw:
            raise JudgeError("claude -p returned empty stdout")

        try:
            return _parse_verdict(raw)
        except JudgeError:
            log.debug(
                "claude_p_judge_first_parse_failed_retrying",
                event_type=input_.event_type,
                raw_preview=raw[:100],
            )

        if self._max_repair_attempts < 1:
            raise JudgeError(
                f"JSON parse failed and max_repair_attempts=0. Raw: {raw[:200]!r}"
            )

        # Single repair call: send the bad output back + correction instruction.
        repair_prompt = (
            f"{prompt}\n\n"
            f"Previous response (invalid):\n{raw}\n\n"
            f"{_REPAIR_INSTRUCTION}"
        )
        repair_raw = await self._run_claude(repair_prompt)
        return _parse_verdict(repair_raw)  # raises JudgeError on second fail


__all__ = ["ClaudePJudge"]
