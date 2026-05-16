"""Escalation tools (spec §17 — 2 tools).

escalate_to_human, update_sprint_status.

Mock-mode: escalate_to_human writes a record to escalations.jsonl + memory.
PII redaction lives in `bot/pii_detector.py` (S6) — this tool only emits structured
events; the bot layer is responsible for redaction before user-facing dispatch.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from bmad_orchestrator.agent.tools._common import (
    append_jsonl,
    error,
    json_ok,
    now_iso,
    read_sprint_status_yaml,
    runs_dir,
    write_sprint_status_yaml,
)

_VALID_STATUSES = {
    "backlog",
    "ready-for-dev",
    "in-progress",
    "review",
    "done",
    "superseded",
    "blocked",
}


@tool(
    "escalate_to_human",
    "Send escalation notification (Telegram primary, email fallback).",
    {"reason": str, "context": dict},
)
async def escalate_to_human(args: dict[str, Any]) -> dict[str, Any]:
    reason = str(args.get("reason", "")).strip()
    context = args.get("context") or {}
    if not reason:
        return error("missing 'reason'", code="invalid_arg")
    if not isinstance(context, dict):
        return error("'context' must be a dict", code="invalid_arg")

    event_path = runs_dir() / "escalations.jsonl"
    payload = {
        "event_type": "escalation",
        "reason": reason,
        "context": context,
        "ts": now_iso(),
    }
    append_jsonl(event_path, payload)
    escalation_id = f"esc-{abs(hash((reason, now_iso()))) % 100_000:05d}"
    return json_ok(
        {
            "escalation_id": escalation_id,
            "reason": reason,
            "event_path": str(event_path),
            "delivery_channel": "telegram",
            "mock": True,
        }
    )


@tool(
    "update_sprint_status",
    "Update story status in sprint-status.yaml with flock serialization.",
    {"story_id": str, "status": str},
)
async def update_sprint_status(args: dict[str, Any]) -> dict[str, Any]:
    story_id = str(args.get("story_id", "")).strip()
    status = str(args.get("status", "")).strip()
    if not story_id or not status:
        return error("require 'story_id' and 'status'", code="invalid_arg")
    if status not in _VALID_STATUSES:
        return error(f"invalid status: {status!r}", code="invalid_arg")

    sprint = read_sprint_status_yaml()
    epics = sprint.setdefault("epics", {})
    target_epic_id: str | None = None
    for eid, epic in epics.items():
        if story_id in (epic.get("stories") or {}):
            target_epic_id = str(eid)
            break
    if target_epic_id is None:
        return error(f"unknown story: {story_id!r}", code="unknown_story")

    prev = epics[target_epic_id]["stories"].get(story_id)
    epics[target_epic_id]["stories"][story_id] = status
    write_sprint_status_yaml(sprint)

    append_jsonl(
        runs_dir() / "status_changes.jsonl",
        {
            "event_type": "status_changed",
            "story_id": story_id,
            "prev": prev,
            "next": status,
            "ts": now_iso(),
        },
    )
    return json_ok(
        {
            "story_id": story_id,
            "epic_id": target_epic_id,
            "prev_status": prev,
            "status": status,
        }
    )


TOOLS = [escalate_to_human, update_sprint_status]


__all__ = ["TOOLS", "escalate_to_human", "update_sprint_status"]
