"""WORKER_ELICITATION subscriber — wires ElicitationEngine into the event bus.

See spec/spec_auto_elicitation_engine.md §3 (wiring section).

Flow:
    WORKER_ELICITATION → engine.decide(payload) → Decision
        ├── action="auto_resolve"  → log control event + (future) deliver answer
        └── action="escalate"      → emit HUMAN_QUERY event for operator
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import structlog

from bmad_orchestrator.agent.tools._common import append_jsonl, now_iso, runs_dir
from bmad_orchestrator.elicitation import (
    Decision,
    ElicitationEngine,
    ElicitationPolicy,
    load_policy,
)
from bmad_orchestrator.elicitation.policy import PolicyNotFoundError
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType

log = structlog.get_logger("elicitation_routing")

# Default fallback policy path (used when caller passes no explicit path).
DEFAULT_POLICY_PATH = Path("examples/elicitation-policy.example.yaml")


def load_engine(policy_path: Path | None = None) -> ElicitationEngine:
    """Build an ElicitationEngine from a YAML path.

    Falls back to DEFAULT_POLICY_PATH if ``policy_path`` is None. If the
    fallback also doesn't exist, returns an engine with an empty policy so
    Tier 0 hard-overrides + Tier 2 stub-judge still protect security topics.
    """
    target = policy_path or DEFAULT_POLICY_PATH
    try:
        policy = load_policy(target)
        log.info("elicitation_policy_loaded", path=str(target), rules=len(policy.rules))
    except PolicyNotFoundError:
        log.warning(
            "elicitation_policy_missing_using_empty",
            attempted=str(target),
            hint="Tier 0 hard-overrides + Tier 2 stub-judge still active",
        )
        policy = ElicitationPolicy(version=1)
    return ElicitationEngine(policy)


def _log_decision(payload: dict[str, Any], decision: Decision) -> None:
    """Append decision audit row to control.events.jsonl for forensics."""
    try:
        path = runs_dir() / "control.events.jsonl"
        append_jsonl(
            path,
            {
                "event_type": "elicitation_decision",
                "ts": now_iso(),
                "story_id": payload.get("story_id"),
                "question_preview": str(payload.get("question", ""))[:120],
                "tier": decision.tier,
                "action": decision.action,
                "risk": decision.risk,
                "rule_id": decision.rule_id,
                "reason": decision.reason,
            },
        )
    except Exception as exc:
        log.warning("elicitation_audit_log_failed", error=str(exc))


def make_elicitation_subscriber(
    engine: ElicitationEngine,
) -> Callable[[Event, EventLoop], Awaitable[None]]:
    """Build an EventLoop-compatible subscriber bound to ``engine``."""

    async def elicitation_subscriber(event: Event, bus: EventLoop) -> None:
        if event.type is not EventType.WORKER_ELICITATION:
            return

        payload = dict(event.payload)
        decision = await engine.decide(payload)
        _log_decision(payload, decision)

        if decision.action == "escalate":
            await bus.emit(
                Event(
                    type=EventType.HUMAN_QUERY,
                    payload={
                        "source": "elicitation_router",
                        "story_id": payload.get("story_id"),
                        "question": payload.get("question"),
                        "topics": payload.get("topics", []),
                        "tier": decision.tier,
                        "risk": decision.risk,
                        "rule_id": decision.rule_id,
                        "reason": decision.reason,
                    },
                )
            )
            log.info(
                "elicitation_escalated",
                story_id=payload.get("story_id"),
                tier=decision.tier,
                rule_id=decision.rule_id,
            )
        else:
            # auto_resolve — answer logged to audit row above. Actual delivery
            # back to the waiting worker rides on the existing
            # respond_to_elicitation control path (S3 deliverable). When the
            # delivery channel lands, hook it here.
            log.info(
                "elicitation_auto_resolved",
                story_id=payload.get("story_id"),
                tier=decision.tier,
                rule_id=decision.rule_id,
                answer_length=len(decision.answer or ""),
            )

    return elicitation_subscriber
