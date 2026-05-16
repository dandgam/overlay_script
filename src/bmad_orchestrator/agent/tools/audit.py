"""Audit tool — exposes `audit_event` to the agent itself.

Используется агентом для записи bookkeeping-событий (decisions, escalations,
manual interventions). Hooks вызывают `record_audit` напрямую — это для тех
случаев когда сам агент решает залогировать что-то существенное.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from bmad_orchestrator.agent.safety.audit import audit_log_path, record_audit
from bmad_orchestrator.agent.tools._common import error, json_ok


@tool(
    "audit_event",
    "Append a structured event to the audit log (JSONL). Use for non-trivial decisions, "
    "manual escalations, budget overrides, security incidents. Returns the recorded entry.",
    {"event_type": str, "summary": str, "payload": dict},
)
async def audit_event(args: dict[str, Any]) -> dict[str, Any]:
    event_type = str(args.get("event_type", ""))
    summary = str(args.get("summary", ""))
    payload = args.get("payload") or {}
    if not event_type:
        return error("require 'event_type'", code="invalid_arg")
    if not isinstance(payload, dict):
        return error("'payload' must be an object", code="invalid_arg")

    entry = record_audit(event_type, summary=summary, **payload)
    return json_ok({"recorded": entry, "audit_log": str(audit_log_path())})


TOOLS = [audit_event]


__all__ = ["TOOLS", "audit_event"]
