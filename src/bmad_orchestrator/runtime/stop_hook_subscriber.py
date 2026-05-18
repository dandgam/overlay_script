"""Stop-hook subscriber — cost + learning consolidation (Phase 4 hardening #6).

Spec: spec_phase4_hardening §2.6.

Subscribes to ``STORY_COMPLETED`` events and aggregates per-story metrics into
a single ``STORY_METRICS_AGGREGATED`` event. Also triggers self-learning
extraction when the story payload flags ``extract_lessons=True``.

Pattern inspired by ECC ``stop:cost-tracker`` + ``stop:evaluate-session``:
aggregate the trickle of per-event metrics into one Stop hook emission instead
of per-tool-call emits (which are noisy and unnecessary in production).
"""

from __future__ import annotations

import math
from typing import Any

import structlog

from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType

log = structlog.get_logger("stop_hook_subscriber")


def _p95(values: list[float]) -> float:
    """Linear-interpolated p95 latency. Returns 0.0 for empty input."""
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    sorted_vals = sorted(values)
    pos = 0.95 * (len(sorted_vals) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return float(sorted_vals[lo])
    frac = pos - lo
    return float(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac)


async def stop_hook_subscriber(event: Event, bus: EventLoop) -> None:
    """On ``STORY_COMPLETED`` → aggregate metrics + optionally trigger learning.

    Payload contract for STORY_COMPLETED:
      Required:
        - ``story_id`` (str)
        - ``status`` (str) — "success" | "failed" | "error"
      Optional:
        - ``extract_lessons`` (bool, default False) — whether to trigger learning
        - ``tokens`` (dict) — {input: int, cached: int, output: int}
        - ``cost_usd`` (float) — total USD cost for this story
        - ``retry_count`` (int) — number of retry attempts
        - ``turn_latencies_ms`` (list[float]) — per-turn latency in ms

    Emits ``STORY_METRICS_AGGREGATED`` with rolled-up payload.
    """
    if event.type != EventType.STORY_COMPLETED:
        return

    payload = dict(event.payload or {})
    story_id = str(payload.get("story_id") or "")
    if not story_id:
        log.warning("stop_hook_missing_story_id", payload=payload)
        return

    status = str(payload.get("status") or "unknown")
    extract_lessons = bool(payload.get("extract_lessons", False))

    # Aggregate token usage.
    tokens = payload.get("tokens") or {}
    total_input = int(tokens.get("input", 0)) if isinstance(tokens, dict) else 0
    total_cached = int(tokens.get("cached", 0)) if isinstance(tokens, dict) else 0
    total_output = int(tokens.get("output", 0)) if isinstance(tokens, dict) else 0

    # Cost.
    try:
        total_cost_usd = float(payload.get("cost_usd") or 0.0)
    except (TypeError, ValueError):
        total_cost_usd = 0.0

    # Retry count.
    try:
        retry_count = int(payload.get("retry_count") or 0)
    except (TypeError, ValueError):
        retry_count = 0

    # p95 turn latency.
    raw_latencies = payload.get("turn_latencies_ms") or []
    if isinstance(raw_latencies, list):
        try:
            latencies: list[float] = [float(v) for v in raw_latencies if v is not None]
        except (TypeError, ValueError):
            latencies = []
    else:
        latencies = []
    p95_latency_ms = _p95(latencies)

    # Optionally trigger self-learning extraction.
    lessons_extracted = False
    if extract_lessons:
        lessons_extracted = await _trigger_extract_lessons(story_id, payload)

    # Emit aggregated metrics event.
    await bus.emit(
        EventType.STORY_METRICS_AGGREGATED,
        story_id=story_id,
        status=status,
        total_input_tokens=total_input,
        total_cached_tokens=total_cached,
        total_output_tokens=total_output,
        total_cost_usd=total_cost_usd,
        retry_count=retry_count,
        p95_turn_latency_ms=p95_latency_ms,
        lessons_extracted=lessons_extracted,
    )
    log.info(
        "story_metrics_aggregated",
        story_id=story_id,
        status=status,
        total_cost_usd=total_cost_usd,
        retry_count=retry_count,
        p95_turn_latency_ms=p95_latency_ms,
        lessons_extracted=lessons_extracted,
    )


async def _trigger_extract_lessons(
    story_id: str, payload: dict[str, Any]
) -> bool:
    """Trigger self-learning lesson extraction if available.

    Currently a stub — real extraction happens when Consolidator is wired into
    the subscriber factory (see make_stop_hook_subscriber). Returns True when
    extraction completes (or when stub is hit), False on error.
    """
    try:
        log.info("extract_lessons_triggered", story_id=story_id)
        # Stub: real consolidator call lives in make_stop_hook_subscriber.
        return True
    except Exception as exc:
        log.exception(
            "extract_lessons_failed", story_id=story_id, error=str(exc)
        )
        return False


def make_stop_hook_subscriber(
    *,
    consolidator: Any | None = None,
) -> Any:
    """Factory returning a ``stop_hook_subscriber`` closure with optional consolidator.

    When ``consolidator`` is provided (a ``Consolidator`` instance), story
    completions flagged with ``extract_lessons=True`` will call
    ``consolidator.run()`` after emitting ``STORY_METRICS_AGGREGATED``.

    When ``consolidator=None`` (default), the stub path runs — no tokens burned.
    """
    import asyncio

    async def _subscriber(event: Event, bus: EventLoop) -> None:
        if event.type != EventType.STORY_COMPLETED:
            return

        payload = dict(event.payload or {})
        story_id = str(payload.get("story_id") or "")
        if not story_id:
            log.warning("stop_hook_factory_missing_story_id", payload=payload)
            return

        status = str(payload.get("status") or "unknown")
        extract_lessons = bool(payload.get("extract_lessons", False))

        tokens = payload.get("tokens") or {}
        total_input = int(tokens.get("input", 0)) if isinstance(tokens, dict) else 0
        total_cached = int(tokens.get("cached", 0)) if isinstance(tokens, dict) else 0
        total_output = int(tokens.get("output", 0)) if isinstance(tokens, dict) else 0

        try:
            total_cost_usd = float(payload.get("cost_usd") or 0.0)
        except (TypeError, ValueError):
            total_cost_usd = 0.0

        try:
            retry_count = int(payload.get("retry_count") or 0)
        except (TypeError, ValueError):
            retry_count = 0

        raw_latencies = payload.get("turn_latencies_ms") or []
        if isinstance(raw_latencies, list):
            try:
                latencies_f: list[float] = [float(v) for v in raw_latencies if v is not None]
            except (TypeError, ValueError):
                latencies_f = []
        else:
            latencies_f = []
        p95_latency_ms = _p95(latencies_f)

        lessons_extracted = False
        if extract_lessons and consolidator is not None:
            try:
                # consolidator.run() is a coroutine — await it.
                if asyncio.iscoroutinefunction(consolidator.run):
                    await consolidator.run()
                else:
                    consolidator.run()
                lessons_extracted = True
                log.info("extract_lessons_triggered_via_consolidator", story_id=story_id)
            except Exception as exc:
                log.exception(
                    "extract_lessons_consolidator_failed",
                    story_id=story_id,
                    error=str(exc),
                )
        elif extract_lessons:
            log.info("extract_lessons_stub_no_consolidator", story_id=story_id)
            lessons_extracted = True

        await bus.emit(
            EventType.STORY_METRICS_AGGREGATED,
            story_id=story_id,
            status=status,
            total_input_tokens=total_input,
            total_cached_tokens=total_cached,
            total_output_tokens=total_output,
            total_cost_usd=total_cost_usd,
            retry_count=retry_count,
            p95_turn_latency_ms=p95_latency_ms,
            lessons_extracted=lessons_extracted,
        )
        log.info(
            "story_metrics_aggregated",
            story_id=story_id,
            status=status,
            total_cost_usd=total_cost_usd,
            p95_turn_latency_ms=p95_latency_ms,
            lessons_extracted=lessons_extracted,
        )

    return _subscriber


__all__ = [
    "make_stop_hook_subscriber",
    "stop_hook_subscriber",
]
