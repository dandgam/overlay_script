"""Worker cost tracking (W3 — wave_1a_pilot_wiring).

Parses ``usage`` blocks from worker JSONL events emitted by ``claude -p`` and
accumulates a per-worker :class:`TokenUsage` so the master orchestrator can:

  * attribute the running spend toward the daily cap via
    :meth:`BudgetGuard.attribute_usd` (event-by-event);
  * log a final ``worker_cost_final`` structured event including
    ``total_usd`` and ``cache_hit_ratio`` for forensics;
  * feed the realised story cost back into
    :meth:`BudgetGuard.record_story_cost` so subsequent reservations use an
    adaptive estimate (last-3 stories) rather than the bootstrap heuristic.

The JSONL contract from ``runtime/worker_spawn.py`` preserves the raw Anthropic
CLI line: SDK shape ``{"usage": {...}}`` for streaming deltas and
message-wrapped ``{"message": {"usage": {...}}}`` for the final assistant
message. Missing-usage events return ``Decimal("0")`` (e.g. ``stdout_line``,
``worker_spawned``, terminal ``worker_completed``).
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from bmad_orchestrator.runtime.budget import TokenUsage, usd_cost

__all__ = ["WorkerCostTracker"]


def _coerce_non_negative_int(value: Any) -> int:
    """Best-effort int coercion that ignores junk values (booleans, strings)."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value if value > 0 else 0
    return 0


def _extract_usage_dict(event: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the usage payload from a worker JSONL event.

    Tolerates both SDK-shaped ``{"usage": {...}}`` and message-wrapped
    ``{"message": {"usage": {...}}}`` events. Returns ``None`` when the event
    carries no usage payload (the common case for ``stdout_line`` /
    ``worker_completed`` / ``worker_spawned`` rows).
    """
    if not isinstance(event, Mapping):
        return None
    usage = event.get("usage")
    if usage is None:
        nested = event.get("message")
        if isinstance(nested, Mapping):
            usage = nested.get("usage")
    if not isinstance(usage, Mapping) or not usage:
        return None
    return dict(usage)


class WorkerCostTracker:
    """Per-worker cumulative cost tracker (W3 §W3.1).

    Stateless w.r.t. the event bus — the master pilot calls :meth:`feed` for
    every JSONL line and decides what to do with the delta (typically
    ``budget.attribute_usd``). Cumulative :class:`TokenUsage` is exposed for
    the final ``worker_cost_final`` log entry.
    """

    __slots__ = ("cumulative", "model")

    def __init__(self, model: str) -> None:
        self.model = model
        self.cumulative = TokenUsage()

    def feed(self, event: Mapping[str, Any]) -> Decimal:
        """Process one worker JSONL event; return the delta USD cost.

        Returns ``Decimal("0")`` when the event has no usage block or the
        model is unknown (logged once via :func:`usd_cost` raising a
        :class:`ValueError`, which we swallow because the worker may emit
        events for a model the orchestrator does not yet price).
        """
        usage_dict = _extract_usage_dict(event)
        if usage_dict is None:
            return Decimal("0")
        delta_usage = TokenUsage(
            input_tokens=_coerce_non_negative_int(usage_dict.get("input_tokens", 0)),
            cache_creation_input_tokens=_coerce_non_negative_int(
                usage_dict.get("cache_creation_input_tokens", 0)
            ),
            cache_read_input_tokens=_coerce_non_negative_int(
                usage_dict.get("cache_read_input_tokens", 0)
            ),
            output_tokens=_coerce_non_negative_int(usage_dict.get("output_tokens", 0)),
        )
        try:
            delta_cost = usd_cost(self.model, delta_usage)
        except ValueError:
            return Decimal("0")
        self.cumulative = TokenUsage(
            input_tokens=self.cumulative.input_tokens + delta_usage.input_tokens,
            cache_creation_input_tokens=(
                self.cumulative.cache_creation_input_tokens
                + delta_usage.cache_creation_input_tokens
            ),
            cache_read_input_tokens=(
                self.cumulative.cache_read_input_tokens
                + delta_usage.cache_read_input_tokens
            ),
            output_tokens=self.cumulative.output_tokens + delta_usage.output_tokens,
        )
        return delta_cost

    @property
    def total_cost(self) -> Decimal:
        """Cumulative USD cost across all fed events (Decimal, exact)."""
        try:
            return usd_cost(self.model, self.cumulative)
        except ValueError:
            return Decimal("0")

    @property
    def cache_hit_ratio(self) -> float:
        """Share of input tokens served from cache (0.0 when no input seen).

        ``cache_read / (input + cache_read)`` per spec W3.1 — measures how
        often the cached system blocks are paying off.
        """
        cum = self.cumulative
        denom = cum.input_tokens + cum.cache_read_input_tokens
        if denom <= 0:
            return 0.0
        return float(cum.cache_read_input_tokens) / float(denom)
