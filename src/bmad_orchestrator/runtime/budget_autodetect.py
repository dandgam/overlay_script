"""Subscription-mode budget auto-disable.

Initiative pilot_findings_closure S6 (#6 P2).

Background: when running on a Claude subscription (no ``ANTHROPIC_API_KEY``),
per-token billing is unavailable so the dollar-budget gates (cap / daily /
story alarm) operate on synthetic estimates and produce misleading halt
events. The historical workaround was to set ``BMAD_DISABLE_BUDGET=1``
manually before every run.

This module replaces that hand-set knob with automatic detection: at the
top of every pilot loop the orchestrator builds a :class:`BudgetAutoDisableState`
and calls :func:`evaluate_budget_disabled` once per spawn round. The helper
returns ``True`` when budget gates should be skipped and emits
``BUDGET_AUTO_DISABLED`` to the event bus exactly once per run on the auto
path. The manual ``BMAD_DISABLE_BUDGET=1`` path is honoured but does *not*
emit the event — it is a deliberate operator action, not an auto-recovery.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass

from bmad_orchestrator.runtime.event_loop import EventLoop, EventType

log = logging.getLogger(__name__)


@dataclass(slots=True)
class BudgetAutoDisableState:
    """Idempotency tracker for :func:`evaluate_budget_disabled`.

    Caller allocates one instance per pilot run and passes it on every
    invocation. ``triggered`` flips True the first time auto-detection
    fires; subsequent calls keep returning ``disabled=True`` without
    re-emitting the bus event.
    """

    triggered: bool = False


def _subscription_auto_detected(env: Mapping[str, str]) -> bool:
    """Detect subscription auth — ANTHROPIC_API_KEY absent AND no manual flag.

    ``BMAD_DISABLE_BUDGET=1`` is handled separately (manual disable, no
    auto-detect event) so this returns False when the manual flag is set
    regardless of whether an API key is also present.
    """
    if env.get("BMAD_DISABLE_BUDGET") == "1":
        return False
    return not env.get("ANTHROPIC_API_KEY")


async def evaluate_budget_disabled(
    state: BudgetAutoDisableState,
    bus: EventLoop | None,
    *,
    env: Mapping[str, str] | None = None,
) -> bool:
    """Return True when $-budget gates should be skipped for this round.

    Emits ``BUDGET_AUTO_DISABLED`` exactly once per ``state`` instance on
    the subscription-auto path. The manual ``BMAD_DISABLE_BUDGET=1`` path
    is honoured but never emits — it carries no audit value beyond what the
    operator already knows.

    Args:
        state: Per-run idempotency state. Mutated in place when the auto
            path fires.
        bus: Event bus for emitting ``BUDGET_AUTO_DISABLED``. ``None`` is
            tolerated for tests that don't need the bus side-effect — the
            return value is unaffected.
        env: Environment mapping. Defaults to ``os.environ`` when omitted
            so the production call site does not need to thread it through.
    """
    effective_env: Mapping[str, str] = env if env is not None else os.environ
    manual = effective_env.get("BMAD_DISABLE_BUDGET") == "1"
    auto = _subscription_auto_detected(effective_env)

    if auto and not state.triggered:
        state.triggered = True
        log.warning(
            "budget_auto_disabled reason=subscription_mode "
            "(no ANTHROPIC_API_KEY; skipping $-budget gates)"
        )
        if bus is not None:
            await bus.emit(
                EventType.BUDGET_AUTO_DISABLED,
                reason="subscription_mode",
            )

    return manual or auto


__all__ = [
    "BudgetAutoDisableState",
    "evaluate_budget_disabled",
]
