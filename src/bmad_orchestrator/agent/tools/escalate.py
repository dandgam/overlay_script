"""Escalation tools (spec §17 — 2 tools).

escalate_to_human, update_sprint_status (writes go through flock).
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool


@tool(
    "escalate_to_human",
    "Send escalation notification (Telegram primary, email fallback). Used for destructive/security/pivot/budget>$50.",
    {"reason": str, "context": dict},
)
async def escalate_to_human(args: dict[str, Any]) -> dict[str, Any]:
    """PII-redacted before send (spec §15.5)."""
    return {"content": [{"type": "text", "text": "TODO: escalate"}]}


@tool(
    "update_sprint_status",
    "Update story status in sprint-status.yaml with flock serialization.",
    {"story_id": str, "status": str},
)
async def update_sprint_status(args: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": "TODO: update status"}]}
