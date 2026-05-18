"""Initiative #2C — orchestration glue for auto-split + sub-story execution.

Brings together three pieces that ship separately:

* ``runtime.story_splitter.evaluate_split`` (S4) — should we split?
* ``runtime.story_splitter.DECOMPOSITION_PROMPT`` + ``validate_decomposition``
  (S4) — LLM prompt template + schema-validated sub-story list.
* ``runtime.sub_story_executor.execute_sub_stories`` + ``squash_sub_stories``
  (S5) — sequential dispatch into parent worktree + N→1 squash.

This module is the single call site that ``_run_real_pilot_body`` invokes per
candidate story (gated behind ``BMAD_AUTO_SPLIT=1`` env flag, see ``agent/run.py``).
Default off — existing pipelines see no behaviour change. Opt-in pilots route
large stories through decomposer → executor → squash before the rest of the
pipeline (code-review / merge) observes a single squashed commit.

The decomposer call is a callable injected by the caller (``decompose_fn``) so
this module stays free of the Claude SDK boundary — tests inject a stub that
returns canned sub-story JSON without spinning a real ``claude -p`` process.

Event translation: callers pass ``bus`` (event_loop.EventLoop) and the module
returns an ``on_event`` shim that converts executor dicts → typed bus events
(STORY_SPLIT_TRIGGERED / SUB_STORY_STARTED / SUB_STORY_COMPLETED /
SUB_STORY_SQUASH_DONE / SUB_STORY_SQUASH_SKIPPED). Downstream subscribers
(retro, telemetry, code-review timing) hook the bus instead of the executor.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from bmad_orchestrator.runtime.event_loop import EventLoop, EventType
from bmad_orchestrator.runtime.story_splitter import (
    DECOMPOSITION_PROMPT,
    DecompositionError,
    evaluate_split,
    validate_decomposition,
)
from bmad_orchestrator.runtime.sub_story_executor import (
    SquashResult,
    SubStoryResult,
    execute_sub_stories,
    squash_sub_stories,
)

log = structlog.get_logger(__name__)

AUTO_SPLIT_ENV_VAR = "BMAD_AUTO_SPLIT"

#: Decomposer signature — caller supplies (prompt, story) → raw JSON string.
#: Stays free of any Anthropic SDK type so tests can swap in a synchronous stub.
DecomposeFn = Callable[[str, dict[str, Any]], Awaitable[str]]


@dataclass(frozen=True)
class AutoSplitOutcome:
    """Final state of one auto-split attempt for a single parent story."""

    parent_story_id: str
    attempted: bool
    decision: str
    rules_hit: tuple[str, ...]
    sub_ids: tuple[str, ...]
    sub_story_results: tuple[SubStoryResult, ...]
    squash: SquashResult | None
    fallback_reason: str | None
    error: str | None

    @property
    def succeeded(self) -> bool:
        """True when split actually executed AND every sub-story succeeded."""
        if not self.attempted or self.error is not None:
            return False
        if self.squash is None:
            return False
        return all(r.succeeded for r in self.sub_story_results)


def auto_split_enabled() -> bool:
    """``BMAD_AUTO_SPLIT=1`` (or truthy) → opt-in to auto-split pipeline path."""
    return os.environ.get(AUTO_SPLIT_ENV_VAR, "").strip().lower() in {"1", "true", "yes"}


def make_bus_bridge(
    bus: EventLoop,
    *,
    parent_story_id: str,
) -> Callable[[dict[str, Any]], None]:
    """Return an ``on_event`` callback for the executor that emits typed bus events.

    Translation table:
      executor ``event_type``           → bus ``EventType``
      ─────────────────────────────────   ──────────────────────────────
      sub_story_started                  → SUB_STORY_STARTED
      sub_story_completed                → SUB_STORY_COMPLETED
      sub_story_squash_done              → SUB_STORY_SQUASH_DONE
      sub_story_squash_skipped           → SUB_STORY_SQUASH_SKIPPED

    The executor invokes the callback synchronously (it has no event loop
    reference), so we schedule the emit via ``asyncio.create_task`` if a loop is
    running, otherwise drop the emit with a warning. Callers running inside
    ``asyncio.run`` always have a loop, which is the only supported pilot path.
    """

    type_map = {
        "sub_story_started": EventType.SUB_STORY_STARTED,
        "sub_story_completed": EventType.SUB_STORY_COMPLETED,
        "sub_story_squash_done": EventType.SUB_STORY_SQUASH_DONE,
        "sub_story_squash_skipped": EventType.SUB_STORY_SQUASH_SKIPPED,
    }
    # Keep strong references so create_task results survive until done — without
    # this set the GC may drop the coroutine mid-emit (RUF006 root cause).
    pending: set[asyncio.Task[Any]] = set()

    def _bridge(payload: dict[str, Any]) -> None:
        evt_type = type_map.get(payload.get("event_type", ""))
        if evt_type is None:
            return
        payload_copy = dict(payload)
        payload_copy.pop("event_type", None)
        payload_copy.setdefault("parent_story_id", parent_story_id)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.warning(
                "auto_split_bus_bridge_no_loop",
                event_type=str(evt_type),
                parent_story_id=parent_story_id,
            )
            return
        task = loop.create_task(bus.emit(evt_type, **payload_copy))
        pending.add(task)
        task.add_done_callback(pending.discard)

    return _bridge


def _build_story_prompt(prompt: str, story: dict[str, Any]) -> str:
    """Render the decomposer prompt with a serialised story payload appended.

    Trims fields irrelevant to decomposition (raw markdown body kept; sprint
    metadata stripped) so the LLM sees a focused brief. JSON-encoded to avoid
    Python repr noise.
    """
    relevant_keys = (
        "id",
        "title",
        "epic_id",
        "scope",
        "estimated_minutes",
        "estimated_tokens",
        "acceptance",
        "ac",
        "touches_files",
        "touches_shared",
        "splittable",
        "body",
    )
    distilled = {k: story[k] for k in relevant_keys if k in story}
    return prompt + "\n\nStory:\n" + json.dumps(distilled, ensure_ascii=False, indent=2)


def _parse_decomposer_output(raw: str) -> list[dict[str, Any]]:
    """Strip optional markdown fence then ``json.loads``. Raises ``DecompositionError``."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DecompositionError(f"decomposer output is not valid JSON: {exc}") from exc
    if not isinstance(payload, list):
        raise DecompositionError(
            f"decomposer output is not a list: got {type(payload).__name__}"
        )
    return payload


async def auto_split_and_execute(
    *,
    story: dict[str, Any],
    worktree: Path,
    branch: str,
    base_sha: str,
    decompose_fn: DecomposeFn,
    bus: EventLoop | None = None,
    spawn_fn: Any = None,
    wait_fn: Any = None,
    spawn_kwargs: dict[str, Any] | None = None,
    halt_on_failure: bool = True,
) -> AutoSplitOutcome:
    """Run the full auto-split pipeline for a single parent story.

    Steps:
      1. ``evaluate_split(story)`` — heuristic. ``keep`` → return immediately
         with ``attempted=False``.
      2. Emit ``STORY_SPLIT_TRIGGERED`` on the bus (if provided).
      3. Call ``decompose_fn(prompt, story)`` → raw JSON string.
      4. Parse + ``validate_decomposition`` — schema errors emit
         ``STORY_SPLIT_TRIGGERED`` with ``fallback="keep"`` payload and return
         ``attempted=False``.
      5. ``execute_sub_stories`` — sequential dispatch in shared parent worktree.
      6. ``squash_sub_stories`` — collapse N → 1 commit on the parent branch.

    Returns ``AutoSplitOutcome`` populated for every code path. Never raises on
    the happy path; only re-raises if ``decompose_fn`` itself crashes (caller
    decides whether that should halt the pilot).
    """
    parent_id = str(story.get("id") or "")
    if not parent_id:
        return AutoSplitOutcome(
            parent_story_id="",
            attempted=False,
            decision="keep",
            rules_hit=(),
            sub_ids=(),
            sub_story_results=(),
            squash=None,
            fallback_reason="missing_parent_id",
            error=None,
        )

    decision = evaluate_split(story)
    if decision.decision != "split":
        return AutoSplitOutcome(
            parent_story_id=parent_id,
            attempted=False,
            decision=decision.decision,
            rules_hit=decision.rules_hit,
            sub_ids=(),
            sub_story_results=(),
            squash=None,
            fallback_reason=None,
            error=None,
        )

    if bus is not None:
        await bus.emit(
            EventType.STORY_SPLIT_TRIGGERED,
            parent_story_id=parent_id,
            rules_hit=list(decision.rules_hit),
            decomposer="pending",
        )

    prompt = _build_story_prompt(DECOMPOSITION_PROMPT, story)
    try:
        raw = await decompose_fn(prompt, story)
    except Exception as exc:
        log.warning(
            "auto_split_decomposer_failed",
            parent_story_id=parent_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        if bus is not None:
            await bus.emit(
                EventType.STORY_SPLIT_TRIGGERED,
                parent_story_id=parent_id,
                rules_hit=list(decision.rules_hit),
                decomposer="error",
                error=f"{type(exc).__name__}: {exc}",
                fallback="keep",
            )
        return AutoSplitOutcome(
            parent_story_id=parent_id,
            attempted=False,
            decision=decision.decision,
            rules_hit=decision.rules_hit,
            sub_ids=(),
            sub_story_results=(),
            squash=None,
            fallback_reason="decomposer_error",
            error=f"{type(exc).__name__}: {exc}",
        )

    try:
        parsed = _parse_decomposer_output(raw)
        # Pass parent_touches_files so the H-4 parent-subset gate
        # actually fires; without it the union-⊆-parent check is dead code.
        parent_files = list(story.get("touches_files") or []) or None
        sub_stories = validate_decomposition(
            parsed,
            parent_id=parent_id,
            parent_touches_files=parent_files,
        )
    except DecompositionError as exc:
        log.warning(
            "auto_split_validation_failed",
            parent_story_id=parent_id,
            error=str(exc),
        )
        if bus is not None:
            await bus.emit(
                EventType.STORY_SPLIT_TRIGGERED,
                parent_story_id=parent_id,
                rules_hit=list(decision.rules_hit),
                decomposer="invalid_payload",
                error=str(exc),
                fallback="keep",
            )
        return AutoSplitOutcome(
            parent_story_id=parent_id,
            attempted=False,
            decision=decision.decision,
            rules_hit=decision.rules_hit,
            sub_ids=(),
            sub_story_results=(),
            squash=None,
            fallback_reason="invalid_decomposition",
            error=str(exc),
        )

    sub_ids = tuple(str(s["id"]) for s in sub_stories)
    on_event = make_bus_bridge(bus, parent_story_id=parent_id) if bus is not None else None

    results = await execute_sub_stories(
        parent_story_id=parent_id,
        sub_stories=sub_stories,
        worktree=worktree,
        branch=branch,
        base_sha=base_sha,
        spawn_fn=spawn_fn,
        wait_fn=wait_fn,
        spawn_kwargs=spawn_kwargs,
        halt_on_failure=halt_on_failure,
        on_event=on_event,
    )

    all_ok = bool(results) and all(r.succeeded for r in results)
    if not all_ok:
        last_reason = results[-1].failure_reason if results else "no_sub_stories_executed"
        return AutoSplitOutcome(
            parent_story_id=parent_id,
            attempted=True,
            decision=decision.decision,
            rules_hit=decision.rules_hit,
            sub_ids=sub_ids,
            sub_story_results=tuple(results),
            squash=None,
            fallback_reason=last_reason,
            error=None,
        )

    squash = squash_sub_stories(
        parent_story_id=parent_id,
        worktree=worktree,
        base_sha=base_sha,
        sub_ids=list(sub_ids),
        on_event=on_event,
    )

    return AutoSplitOutcome(
        parent_story_id=parent_id,
        attempted=True,
        decision=decision.decision,
        rules_hit=decision.rules_hit,
        sub_ids=sub_ids,
        sub_story_results=tuple(results),
        squash=squash,
        fallback_reason=None,
        error=None,
    )


__all__ = [
    "AUTO_SPLIT_ENV_VAR",
    "AutoSplitOutcome",
    "DecomposeFn",
    "auto_split_and_execute",
    "auto_split_enabled",
    "make_bus_bridge",
]
