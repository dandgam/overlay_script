"""Bus subscriber wiring SupervisorEngine into the event loop.

Subscribes to 5 event types (HUMAN_QUERY, WORKER_HALT_FILE,
BUDGET_THRESHOLD_HIT, WORKER_SILENT_FAILURE, COMPLIANCE_SWEEP_NEEDED) and runs
the supervisor decision pipeline. Filters out events the supervisor itself
emitted (source=supervisor) so the loop can't feed itself.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import structlog

from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.supervisor import (
    SupervisorEngine,
    SupervisorPolicy,
    load_policy,
    log_decision,
)
from bmad_orchestrator.supervisor.actions import execute_decision
from bmad_orchestrator.supervisor.llm_judge import LLMJudgeProtocol
from bmad_orchestrator.supervisor.policy import PolicyNotFoundError

log = structlog.get_logger("supervisor_subscriber")

# Default fallback policy path.
DEFAULT_POLICY_PATH = Path("config/supervisor-policy.yaml")

# Event types the supervisor watches. Order matters only for documentation —
# the subscriber filters via membership.
WATCHED_EVENT_TYPES: frozenset[EventType] = frozenset(
    {
        EventType.HUMAN_QUERY,
        EventType.WORKER_HALT_FILE,
        EventType.BUDGET_THRESHOLD_HIT,
        EventType.WORKER_SILENT_FAILURE,
        EventType.COMPLIANCE_SWEEP_NEEDED,
        # NEW-33.3 — Tier 0 hard rule пропускает к decide() события от
        # cumulative stuck-worker watchdog. По умолчанию политика
        # escalate_human, real Sonnet (M4) сможет автомиатически решать
        # retry vs abort на основе истории.
        EventType.WORKER_STUCK_TIMEOUT,
    }
)


def load_supervisor_engine(
    policy_path: Path | None = None,
    judge: LLMJudgeProtocol | None = None,
    judge_factory: Callable[[], LLMJudgeProtocol] | None = None,
) -> SupervisorEngine:
    """Build a SupervisorEngine from YAML path.

    Falls back to DEFAULT_POLICY_PATH if path is None. If the fallback also
    doesn't exist, returns an engine with empty policy (Tier 0 = no rules,
    Tier 1 = StubJudge → always escalate, fail-safe).
    """
    target = policy_path or DEFAULT_POLICY_PATH
    try:
        policy = load_policy(target)
        log.info(
            "supervisor_policy_loaded",
            path=str(target),
            hard_rules=len(policy.hard_rules),
        )
    except PolicyNotFoundError:
        log.warning(
            "supervisor_policy_missing_using_empty",
            attempted=str(target),
            hint="Tier 1 StubJudge will fail-safe escalate every event",
        )
        policy = SupervisorPolicy(version=1)
    # NEW-33.3 — resolution order: explicit ``judge`` > ``judge_factory()`` >
    # SupervisorEngine default (StubJudge). The factory hook lets callers
    # (agent/run.py) plug an env-gated real-LLM judge without importing
    # Anthropic types at the supervisor module boundary.
    resolved_judge = judge
    if resolved_judge is None and judge_factory is not None:
        try:
            resolved_judge = judge_factory()
        except Exception as exc:
            log.warning(
                "supervisor_judge_factory_failed",
                error=str(exc),
                hint="falling back to StubJudge — Tier 1 will escalate",
            )
            resolved_judge = None
    return SupervisorEngine(policy, judge=resolved_judge)


def make_supervisor_subscriber(
    engine: SupervisorEngine,
) -> Callable[[Event, EventLoop], Awaitable[None]]:
    """Build an EventLoop-compatible subscriber bound to ``engine``."""

    async def supervisor_subscriber(event: Event, bus: EventLoop) -> None:
        if event.type not in WATCHED_EVENT_TYPES:
            return
        # Anti-loop: skip events the supervisor itself emitted.
        if (event.payload or {}).get("source") == "supervisor":
            return

        payload = dict(event.payload or {})
        try:
            decision = await engine.decide(event.type.value.upper(), payload)
        except Exception as exc:
            log.exception("supervisor_decide_failed", error=str(exc))
            return

        log_decision(
            event_type=event.type.value.upper(),
            event_payload=payload,
            decision=decision,
        )
        try:
            await execute_decision(
                decision,
                source_event_type=event.type.value.upper(),
                source_payload=payload,
                bus=bus,
            )
        except Exception as exc:
            log.exception("supervisor_execute_failed", error=str(exc))

    return supervisor_subscriber


__all__ = [
    "DEFAULT_POLICY_PATH",
    "WATCHED_EVENT_TYPES",
    "load_supervisor_engine",
    "make_supervisor_subscriber",
]
