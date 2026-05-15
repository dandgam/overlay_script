"""Merge tools (spec §17 — 3 tools).

run_code_review, run_security_review, git_merge.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool


@tool(
    "run_code_review",
    "Run bmad-code-review on worker's diff. ALWAYS required before merge (spec §11).",
    {"worktree": str},
)
async def run_code_review(args: dict[str, Any]) -> dict[str, Any]:
    """Spawn fresh `claude -p /bmad-code-review`. Returns ReviewResult."""
    return {"content": [{"type": "text", "text": "TODO: code review"}]}


@tool(
    "run_security_review",
    "Run bmad-security-review on worker's diff. Required for security-critical epics.",
    {"worktree": str},
)
async def run_security_review(args: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": "TODO: security review"}]}


@tool(
    "git_merge",
    "Merge worktree's branch into target_branch (with flock on shared state).",
    {"worktree": str, "target_branch": str, "message": str},
)
async def git_merge(args: dict[str, Any]) -> dict[str, Any]:
    """--no-ff. flock on sprint-status.yaml during merge. Spec §6.3 lock files."""
    return {"content": [{"type": "text", "text": "TODO: git merge"}]}
