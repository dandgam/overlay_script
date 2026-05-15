"""Spawn tools (spec §17 — 4 tools).

create_worktree, spawn_worker, sync_skill_patches, cleanup_worktree.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool


@tool(
    "create_worktree",
    "Create new git worktree at sibling path for a feature branch.",
    {"story_id": str, "branch": str},
)
async def create_worktree(args: dict[str, Any]) -> dict[str, Any]:
    """git worktree add /home/server/<proj>-wt-N branch (sibling layout, spec §10)."""
    return {"content": [{"type": "text", "text": "TODO: create worktree"}]}


@tool(
    "spawn_worker",
    "Spawn `claude -p` subprocess running /bmad-auto-dev skill in worktree.",
    {"worktree": str, "model": str, "budget_cap_usd": float},
)
async def spawn_worker(args: dict[str, Any]) -> dict[str, Any]:
    """asyncio.create_subprocess_exec + JSONL stream parser."""
    return {"content": [{"type": "text", "text": "TODO: spawn worker"}]}


@tool(
    "sync_skill_patches",
    "Pull latest bmad-auto-dev skill patches into a worktree (file-watch synced).",
    {"worktree": str},
)
async def sync_skill_patches(args: dict[str, Any]) -> dict[str, Any]:
    """Spec §3 capability #6 — Skill patches sync between worktrees."""
    return {"content": [{"type": "text", "text": "TODO: sync skill patches"}]}


@tool(
    "cleanup_worktree",
    "Remove worktree after merge (git worktree remove + branch -d).",
    {"worktree": str},
)
async def cleanup_worktree(args: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": "TODO: cleanup worktree"}]}
