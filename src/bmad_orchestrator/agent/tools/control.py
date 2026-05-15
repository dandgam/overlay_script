"""Control tools (spec §17 — 4 tools).

pause_worker, resume_worker, respond_to_elicitation, spawn_fixer.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool


@tool(
    "pause_worker",
    "Send SIGSTOP to worker process. Reversible via resume.",
    {"pid": int},
)
async def pause_worker(args: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": "TODO: pause"}]}


@tool(
    "resume_worker",
    "Send SIGCONT to worker process.",
    {"pid": int},
)
async def resume_worker(args: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": "TODO: resume"}]}


@tool(
    "respond_to_elicitation",
    "Send an answer to a worker waiting on an elicitation prompt.",
    {"pid": int, "answer": str},
)
async def respond_to_elicitation(args: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": "TODO: respond"}]}


@tool(
    "spawn_fixer",
    "Spawn fresh `claude -p /bmad-code-fix` subagent to address review findings.",
    {"worktree": str, "findings": list[dict]},
)
async def spawn_fixer(args: dict[str, Any]) -> dict[str, Any]:
    """Role 2 from spec §7 — Manual fixer как fresh subagent."""
    return {"content": [{"type": "text", "text": "TODO: spawn fixer"}]}
