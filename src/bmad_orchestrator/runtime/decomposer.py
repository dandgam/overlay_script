"""Production decomposer for auto-split (Initiative #2 — S2 LLM-driven split).

Closes the last auto-split gap. ``agent/run.py::_DECOMPOSER`` defaulted to
``None``, so even with ``BMAD_AUTO_SPLIT=1`` the auto-split diversion in
``_run_real_pilot_body`` stayed inert (the gate is
``auto_split_enabled() and _DECOMPOSER is not None``).

This module supplies a real :data:`DecomposeFn` — :func:`claude_decomposer` —
that spawns ``claude -p --model <opus>`` headless, pipes the rendered
decomposition prompt via stdin, and returns raw stdout. The caller
(``auto_split._parse_decomposer_output``) strips an optional markdown fence
then ``json.loads`` the sub-story list.

Wiring: ``_run_real_pilot`` calls ``set_decomposer(claude_decomposer)`` — but
only when no decomposer was pre-injected, so tests that inject a stub via
``set_decomposer`` keep their stub.

Failure model: any spawn problem (binary missing / non-zero exit / timeout)
raises :class:`DecomposerSpawnError`. ``auto_split_and_execute`` wraps the
``decompose_fn`` call in ``except Exception`` and falls back to the legacy
single-worker pipeline — a decomposer failure never loses the story.
"""

from __future__ import annotations

import asyncio
import shutil
from typing import Any

import structlog

from bmad_orchestrator.config import load_settings

log = structlog.get_logger(__name__)

#: Upper bound on the decomposer subprocess. Decomposition is a single LLM
#: call (one parent story → N sub-stories); 5 min is generous headroom.
DECOMPOSER_TIMEOUT_SEC = 300


class DecomposerSpawnError(RuntimeError):
    """``claude -p`` decomposer subprocess failed — binary missing, non-zero
    exit, or timeout. Caught by ``auto_split_and_execute`` → legacy fallback."""


async def claude_decomposer(prompt: str, story: dict[str, Any]) -> str:
    """:data:`DecomposeFn` — spawn ``claude -p --model <opus>``, return raw stdout.

    Args:
      prompt: fully-rendered decomposition prompt (``auto_split._build_story_prompt``
        already appended the serialised story payload).
      story:  parent story dict — used only for log/error context here.

    Returns:
      Raw stdout of the ``claude -p`` call — expected to be a JSON list of
      sub-stories, possibly wrapped in a ```` ``` ```` fence. Parsing/validation
      is the caller's job.

    Raises:
      DecomposerSpawnError: binary not found, non-zero exit, or timeout.
    """
    claude_bin = shutil.which("claude")
    if claude_bin is None:
        raise DecomposerSpawnError("`claude` binary not found in PATH")

    # Reuse the worker env allow-list so the decomposer subprocess does not
    # inherit secret-bearing vars (ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, ...).
    from bmad_orchestrator.runtime.worker_spawn import _build_worker_env

    env = _build_worker_env({"ORCHESTRATOR_WORKFLOW": "decomposer"})
    model = load_settings().models.planner
    story_id = str(story.get("id") or "<unknown>")

    proc = await asyncio.create_subprocess_exec(
        claude_bin,
        "-p",
        "--model",
        model,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=prompt.encode("utf-8")),
            timeout=DECOMPOSER_TIMEOUT_SEC,
        )
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise DecomposerSpawnError(
            f"decomposer for story {story_id} timed out after "
            f"{DECOMPOSER_TIMEOUT_SEC}s"
        ) from exc

    if proc.returncode != 0:
        tail = stderr.decode("utf-8", errors="replace")[-500:]
        raise DecomposerSpawnError(
            f"claude -p decomposer exited {proc.returncode} for story "
            f"{story_id}: {tail}"
        )

    out = stdout.decode("utf-8", errors="replace")
    log.info(
        "decomposer_completed",
        story_id=story_id,
        model=model,
        output_chars=len(out),
    )
    return out


__all__ = ["DECOMPOSER_TIMEOUT_SEC", "DecomposerSpawnError", "claude_decomposer"]
