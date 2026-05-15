"""State tools (spec §17 — 5 tools).

read_sprint_status, list_worktrees, get_worker_status, tail_worker_jsonl, get_budget.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool


@tool(
    "read_sprint_status",
    "Read story statuses from sprint-status.yaml of target project.",
    {"project": str},
)
async def read_sprint_status(args: dict[str, Any]) -> dict[str, Any]:
    """Read sprint-status.yaml + parse statuses."""
    # TODO: implement
    return {"content": [{"type": "text", "text": "TODO: read sprint-status"}]}


@tool(
    "list_worktrees",
    "List active git worktrees and their state.",
    {},
)
async def list_worktrees(args: dict[str, Any]) -> dict[str, Any]:
    """git worktree list + parse."""
    return {"content": [{"type": "text", "text": "TODO: list worktrees"}]}


@tool(
    "get_worker_status",
    "Status of one worker by worktree path.",
    {"worktree": str},
)
async def get_worker_status(args: dict[str, Any]) -> dict[str, Any]:
    """Check PID alive + recent JSONL events + halt-reason."""
    return {"content": [{"type": "text", "text": "TODO: worker status"}]}


@tool(
    "tail_worker_jsonl",
    "Read last N events from worker's JSONL stream.",
    {"worktree": str, "n": int},
)
async def tail_worker_jsonl(args: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": "TODO: tail jsonl"}]}


@tool(
    "get_budget",
    "Current spend at given scope (story|batch|wave|day).",
    {"scope": str},
)
async def get_budget(args: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": "TODO: budget snapshot"}]}
