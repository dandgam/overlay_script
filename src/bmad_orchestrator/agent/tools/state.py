"""State tools (spec §17 — 5 tools).

read_sprint_status, list_worktrees, get_worker_status, tail_worker_jsonl, get_budget.

Mock-mode implementation:
- read_sprint_status — parses sprint-status.yaml under target_project/_bmad-output/.
- list_worktrees — reads `git worktree list --porcelain` under target_project; if no
  git repo present, falls back to scanning <target>/.worktrees/.
- get_worker_status — combines PID liveness (psutil) + JSONL tail.
- tail_worker_jsonl — returns last N lines from the worker's JSONL events file.
- get_budget — queries state.db (most-recent agent_session) for budget_tracker rows.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from bmad_orchestrator.agent.tools._common import (
    append_jsonl,
    error,
    get_settings,
    is_pid_alive,
    json_ok,
    jsonl_tail,
    now_iso,
    read_sprint_status_yaml,
    runs_dir,
    worker_jsonl_path,
    worktree_root,
)
from bmad_orchestrator.state import StateDB, connect

# FS3 H15 — same timeout policy as merge.py.
SUBPROCESS_TIMEOUT_SEC = 300


@tool(
    "read_sprint_status",
    "Read story statuses from sprint-status.yaml of target project.",
    {"project": str},
)
async def read_sprint_status(args: dict[str, Any]) -> dict[str, Any]:
    project = str(args.get("project", ""))
    data = read_sprint_status_yaml()
    payload: dict[str, Any] = {"project": project, "sprint_status": data}
    return json_ok(payload)


@tool(
    "list_worktrees",
    "List active git worktrees and their state.",
    {},
)
async def list_worktrees(args: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    repo = settings.target_project
    worktrees: list[dict[str, Any]] = []

    if (repo / ".git").exists():
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(repo),
            "worktree",
            "list",
            "--porcelain",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=SUBPROCESS_TIMEOUT_SEC
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            append_jsonl(
                runs_dir() / "subprocess.events.jsonl",
                {
                    "event_type": "subprocess_timeout",
                    "ts": now_iso(),
                    "command": "git worktree list",
                    "timeout_sec": SUBPROCESS_TIMEOUT_SEC,
                },
            )
            return error(
                f"git worktree list timed out after {SUBPROCESS_TIMEOUT_SEC}s",
                code="subprocess_timeout",
            )
        worktrees.extend(_parse_git_worktree_porcelain(stdout.decode("utf-8")))
    else:
        root = worktree_root(settings)
        if root.exists():
            for sub in sorted(root.iterdir()):
                if sub.is_dir():
                    worktrees.append({"path": str(sub), "branch": sub.name})

    return json_ok({"worktrees": worktrees, "count": len(worktrees)})


def _parse_git_worktree_porcelain(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            if current:
                out.append(current)
                current = {}
            continue
        key, _, val = line.partition(" ")
        if key == "worktree":
            current["path"] = val
        elif key == "branch":
            current["branch"] = val.removeprefix("refs/heads/")
        elif key == "HEAD":
            current["head"] = val
        elif key == "detached":
            current["detached"] = True
    if current:
        out.append(current)
    return out


@tool(
    "get_worker_status",
    "Status of one worker by worktree path.",
    {"worktree": str},
)
async def get_worker_status(args: dict[str, Any]) -> dict[str, Any]:
    worktree = str(args.get("worktree", ""))
    if not worktree:
        return error("missing 'worktree' arg", code="invalid_arg")
    jsonl = worker_jsonl_path(worktree)
    events = jsonl_tail(jsonl, 50)
    pid = _extract_pid(events)
    last_event = events[-1] if events else None
    payload: dict[str, Any] = {
        "worktree": worktree,
        "pid": pid,
        "alive": is_pid_alive(pid) if pid else False,
        "events_seen": len(events),
        "last_event": last_event,
    }
    return json_ok(payload)


def _extract_pid(events: list[dict[str, Any]]) -> int:
    for ev in reversed(events):
        pid = ev.get("pid") or ev.get("worker_pid")
        if isinstance(pid, int) and pid > 0:
            return pid
    return 0


@tool(
    "tail_worker_jsonl",
    "Read last N events from worker's JSONL stream.",
    {"worktree": str, "n": int},
)
async def tail_worker_jsonl(args: dict[str, Any]) -> dict[str, Any]:
    worktree = str(args.get("worktree", ""))
    n = int(args.get("n", 50))
    if not worktree:
        return error("missing 'worktree' arg", code="invalid_arg")
    jsonl = worker_jsonl_path(worktree)
    events = jsonl_tail(jsonl, n)
    return json_ok({"worktree": worktree, "n": n, "events": events})


@tool(
    "get_budget",
    "Current spend at given scope (story|batch|wave|day).",
    {"scope": str},
)
async def get_budget(args: dict[str, Any]) -> dict[str, Any]:
    scope = str(args.get("scope", ""))
    if scope not in ("story", "batch", "wave", "day", "phase"):
        return error(f"invalid scope: {scope!r}", code="invalid_arg")

    settings = get_settings()
    db_path: Path = settings.state_db
    if not db_path.exists():
        return json_ok({"scope": scope, "rows": [], "note": "state.db not initialized"})

    db = StateDB(db_path)
    await db.init()
    rows: list[dict[str, Any]] = []
    async with connect(db_path) as conn:
        cur = await conn.execute(
            """
            SELECT scope, scope_target_id, spent_usd, spent_tokens,
                   alarm_threshold, halt_threshold, breached_alarm, breached_halt
              FROM budget_tracker
             WHERE scope = ?
             ORDER BY id DESC
            """,
            (scope,),
        )
        async for row in cur:
            rows.append(
                {
                    "scope": row["scope"],
                    "scope_target_id": row["scope_target_id"],
                    "spent_usd": row["spent_usd"],
                    "spent_tokens": row["spent_tokens"],
                    "alarm_threshold": row["alarm_threshold"],
                    "halt_threshold": row["halt_threshold"],
                    "breached_alarm": bool(row["breached_alarm"]),
                    "breached_halt": bool(row["breached_halt"]),
                }
            )
    return json_ok({"scope": scope, "rows": rows, "count": len(rows)})


# Used by skill registry + tests to assert presence.
TOOLS = [
    read_sprint_status,
    list_worktrees,
    get_worker_status,
    tail_worker_jsonl,
    get_budget,
]
