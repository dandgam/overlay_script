"""Spawn tools (spec §17 — 4 tools).

create_worktree, spawn_worker, sync_skill_patches, cleanup_worktree.

Mock-mode behavior (S2):
- create_worktree — creates an empty sibling directory under <target>/.worktrees/<story_id>
  and emits a `worktree_created` event into state.db.
- spawn_worker — records `worker_spawned` intent in state.db; returns a synthetic PID 0
  so callers can distinguish mock-mode. Real `claude -p` subprocess wiring → S3.
- sync_skill_patches — diff-by-stat between orchestrator's `.claude/skills/` and the
  worktree's; emits a `skills_synced` event with file count.
- cleanup_worktree — removes the worktree directory (mock); records event.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from bmad_orchestrator.agent.tools._common import (
    append_jsonl,
    error,
    get_settings,
    json_ok,
    now_iso,
    runs_dir,
    worktree_root,
)


def _worker_event(worktree: str, event_type: str, **payload: Any) -> Path:
    """Append an event into the per-worker JSONL stream + return its path."""
    from bmad_orchestrator.agent.tools._common import worker_jsonl_path

    path = worker_jsonl_path(worktree)
    append_jsonl(
        path,
        {
            "event_type": event_type,
            "worktree": worktree,
            "ts": now_iso(),
            **payload,
        },
    )
    return path


@tool(
    "create_worktree",
    "Create a new git worktree at sibling path for a feature branch.",
    {"story_id": str, "branch": str},
)
async def create_worktree(args: dict[str, Any]) -> dict[str, Any]:
    story_id = str(args.get("story_id", ""))
    branch = str(args.get("branch", ""))
    if not story_id or not branch:
        return error("missing 'story_id' or 'branch'", code="invalid_arg")

    settings = get_settings()
    root = worktree_root(settings)
    root.mkdir(parents=True, exist_ok=True)
    path = root / story_id
    if path.exists():
        return error(f"worktree already exists: {path}", code="exists")
    path.mkdir(parents=True)

    _worker_event(str(path), "worktree_created", story_id=story_id, branch=branch)

    return json_ok(
        {
            "path": str(path),
            "branch": branch,
            "story_id": story_id,
            "created_at": now_iso(),
        }
    )


@tool(
    "spawn_worker",
    "Spawn `claude -p` subprocess running /bmad-auto-dev skill in worktree.",
    {"worktree": str, "model": str, "budget_cap_usd": float},
)
async def spawn_worker(args: dict[str, Any]) -> dict[str, Any]:
    worktree = str(args.get("worktree", ""))
    model = str(args.get("model", "claude-sonnet-4-6"))
    cap = float(args.get("budget_cap_usd", 30.0))
    if not worktree:
        return error("missing 'worktree' arg", code="invalid_arg")
    if not Path(worktree).exists():
        return error(f"worktree path not found: {worktree}", code="worktree_missing")

    # S2 mock — record intent; real subprocess in S3.
    pid = 0
    _worker_event(
        worktree,
        "worker_spawned",
        model=model,
        budget_cap_usd=cap,
        pid=pid,
        mock=True,
    )
    return json_ok(
        {
            "worktree": worktree,
            "pid": pid,
            "model": model,
            "budget_cap_usd": cap,
            "mock": True,
        }
    )


@tool(
    "sync_skill_patches",
    "Pull latest bmad-auto-dev skill patches into a worktree (file-watch synced).",
    {"worktree": str},
)
async def sync_skill_patches(args: dict[str, Any]) -> dict[str, Any]:
    worktree = str(args.get("worktree", ""))
    if not worktree:
        return error("missing 'worktree'", code="invalid_arg")
    wt = Path(worktree)
    if not wt.exists():
        return error(f"worktree path not found: {worktree}", code="worktree_missing")

    src = get_settings().orchestrator_home / ".claude" / "skills"
    dst = wt / ".claude" / "skills"

    copied = 0
    if src.exists():
        dst.mkdir(parents=True, exist_ok=True)
        for skill_dir in src.iterdir():
            if skill_dir.is_dir():
                target = dst / skill_dir.name
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(skill_dir, target)
                copied += 1

    _worker_event(worktree, "skills_synced", count=copied)
    return json_ok({"worktree": worktree, "synced_skills": copied})


@tool(
    "cleanup_worktree",
    "Remove worktree after merge (git worktree remove + branch -d).",
    {"worktree": str},
)
async def cleanup_worktree(args: dict[str, Any]) -> dict[str, Any]:
    worktree = str(args.get("worktree", ""))
    if not worktree:
        return error("missing 'worktree'", code="invalid_arg")
    path = Path(worktree)
    settings = get_settings()
    root = worktree_root(settings).resolve()
    # Safety: only remove paths under the worktree root.
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return error(
            f"refused to cleanup path outside worktree root: {path}",
            code="path_traversal",
        )
    if path.exists():
        shutil.rmtree(path)

    # Drop any leftover events file (best-effort).
    runs = runs_dir(settings)
    if runs.exists():
        for jsonl in runs.rglob(f"{path.name}.events.jsonl"):
            jsonl.unlink(missing_ok=True)

    return json_ok({"worktree": worktree, "removed": True})


TOOLS = [create_worktree, spawn_worker, sync_skill_patches, cleanup_worktree]


__all__ = [
    "TOOLS",
    "cleanup_worktree",
    "create_worktree",
    "spawn_worker",
    "sync_skill_patches",
]
