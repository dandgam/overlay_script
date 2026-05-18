"""Event-loop subscribers wiring BMad Phase 4 canonical workflows.

Closes the gap «correct-course и investigate skills есть в EMBEDDED, но никто
их не вызывает». Two subscribers:

- ``correct_course_subscriber`` — listens for
  ``SPRINT_SCOPE_CHANGE_DETECTED`` events (emitted by CLI flag, supervisor,
  or future scope-drift detector) and spawns ``bmad-correct-course`` for
  the affected story.
- ``investigate_subscriber`` — listens for ``FORENSIC_INVESTIGATION_NEEDED``
  events (emitted by ``failure-analyst`` when ``should_investigate()``
  threshold is hit) and spawns ``bmad-investigate`` for forensic deep-dive.

Both subscribers use mock mode by default (artifact seed only) so the bus
loop stays fast in tests. ``real=True`` payload field forks ``claude -p``.
"""

from __future__ import annotations

import structlog

from bmad_orchestrator.agent.tools.correct_course import (
    spawn_correct_course_worktree,
)
from bmad_orchestrator.agent.tools.investigate import (
    should_investigate,
    spawn_investigate_worktree,
)
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType

log = structlog.get_logger("phase4_subscribers")


async def correct_course_subscriber(event: Event, bus: EventLoop) -> None:
    """Handle SPRINT_SCOPE_CHANGE_DETECTED → spawn bmad-correct-course.

    Payload contract:
      - ``story_id`` (required) — affected story.
      - ``reason`` (required) — rationale (PM scope drop, AC change, etc).
      - ``real`` (optional, default False) — if True, fork real subprocess.
      - ``source`` (optional) — emitter id; events with source=phase4 are
        skipped to avoid self-trigger loops.
    """
    if event.type != EventType.SPRINT_SCOPE_CHANGE_DETECTED:
        return
    payload = dict(event.payload or {})
    if payload.get("source") == "phase4":
        return

    story_id = str(payload.get("story_id", "")).strip()
    reason = str(payload.get("reason", "")).strip()
    if not story_id or not reason:
        log.warning(
            "correct_course_subscriber_missing_payload",
            story_id=story_id or "<missing>",
            has_reason=bool(reason),
        )
        return

    real = bool(payload.get("real", False))
    try:
        reply = await spawn_correct_course_worktree.handler(
            {"story_id": story_id, "reason": reason, "real": real}
        )
        log.info(
            "correct_course_spawned",
            story_id=story_id,
            real=real,
            reply_meta=_extract_meta(reply),
        )
    except Exception as exc:
        log.exception(
            "correct_course_spawn_failed", story_id=story_id, error=str(exc)
        )


async def investigate_subscriber(event: Event, bus: EventLoop) -> None:
    """Handle FORENSIC_INVESTIGATION_NEEDED → maybe spawn bmad-investigate.

    Payload contract:
      - ``subject`` (required) — story_id / error_class / incident_id.
      - ``reason`` (required) — what the failure-analyst observed.
      - ``retry_count`` (optional, default 0) — for heuristic gate.
      - ``error_category`` (optional) — same.
      - ``real`` (optional, default False).
      - ``source`` (optional) — events with source=phase4 are skipped.

    Heuristic: skips spawn unless ``should_investigate(retry_count,
    error_category)`` returns True, OR the event explicitly sets
    ``force=True`` (manual CLI trigger bypasses heuristic).
    """
    if event.type != EventType.FORENSIC_INVESTIGATION_NEEDED:
        return
    payload = dict(event.payload or {})
    if payload.get("source") == "phase4":
        return

    subject = str(payload.get("subject", "")).strip()
    reason = str(payload.get("reason", "")).strip()
    if not subject or not reason:
        log.warning(
            "investigate_subscriber_missing_payload",
            subject=subject or "<missing>",
            has_reason=bool(reason),
        )
        return

    force = bool(payload.get("force", False))
    retry_count = int(payload.get("retry_count", 0))
    error_category = payload.get("error_category")
    if not force and not should_investigate(retry_count, error_category):
        log.debug(
            "investigate_skipped_below_threshold",
            subject=subject,
            retry_count=retry_count,
            error_category=error_category,
        )
        return

    real = bool(payload.get("real", False))
    try:
        reply = await spawn_investigate_worktree.handler(
            {"subject": subject, "reason": reason, "real": real}
        )
        log.info(
            "investigate_spawned",
            subject=subject,
            real=real,
            retry_count=retry_count,
            error_category=error_category,
            reply_meta=_extract_meta(reply),
        )
    except Exception as exc:
        log.exception(
            "investigate_spawn_failed", subject=subject, error=str(exc)
        )


def _extract_meta(reply: dict[str, object]) -> dict[str, object]:
    """Pull a small summary from an SdkMcpTool reply for log fields."""
    try:
        text = reply["content"][0]["text"]  # type: ignore[index,call-overload]
        import json

        data = json.loads(text)
        if not isinstance(data, dict):
            return {}
        return {k: data.get(k) for k in ("action", "mock", "subagent_id")}
    except (KeyError, IndexError, TypeError, ValueError):
        return {}


__all__ = ["correct_course_subscriber", "investigate_subscriber"]
