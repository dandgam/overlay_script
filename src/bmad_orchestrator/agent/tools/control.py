"""Control tools (spec §17 — 4 tools).

pause_worker, resume_worker, respond_to_elicitation, spawn_fixer.

Mock-mode behavior: SIGSTOP/SIGCONT are NOT actually sent to arbitrary PIDs;
the tools only record the intent + perform a liveness check. Real signal
handling will be added in S3 when worker subprocesses are spawned for real.
"""

from __future__ import annotations

import os
import signal
from typing import Any

from claude_agent_sdk import tool

from bmad_orchestrator.agent.tools._common import (
    append_jsonl,
    error,
    is_pid_alive,
    json_ok,
    now_iso,
    runs_dir,
)


def _control_event(pid: int, event_type: str, **payload: Any) -> None:
    path = runs_dir() / "control.events.jsonl"
    append_jsonl(
        path,
        {"event_type": event_type, "pid": pid, "ts": now_iso(), **payload},
    )


@tool(
    "pause_worker",
    "Send SIGSTOP to worker process. Reversible via resume_worker.",
    {"pid": int},
)
async def pause_worker(args: dict[str, Any]) -> dict[str, Any]:
    pid = int(args.get("pid", 0))
    if pid <= 0:
        return error("pid must be positive", code="invalid_arg")
    if not is_pid_alive(pid):
        return error(f"pid {pid} is not alive", code="pid_dead")
    # Real environments: os.kill(pid, signal.SIGSTOP). Mock keeps it out.
    if args.get("real_signal") is True:
        os.kill(pid, signal.SIGSTOP)
    _control_event(pid, "pause_worker", real_signal=bool(args.get("real_signal")))
    return json_ok({"pid": pid, "paused": True})


@tool(
    "resume_worker",
    "Send SIGCONT to worker process.",
    {"pid": int},
)
async def resume_worker(args: dict[str, Any]) -> dict[str, Any]:
    pid = int(args.get("pid", 0))
    if pid <= 0:
        return error("pid must be positive", code="invalid_arg")
    if args.get("real_signal") is True:
        os.kill(pid, signal.SIGCONT)
    _control_event(pid, "resume_worker", real_signal=bool(args.get("real_signal")))
    return json_ok({"pid": pid, "resumed": True})


@tool(
    "respond_to_elicitation",
    "Send an answer to a worker waiting on an elicitation prompt.",
    {"pid": int, "answer": str},
)
async def respond_to_elicitation(args: dict[str, Any]) -> dict[str, Any]:
    pid = int(args.get("pid", 0))
    answer = str(args.get("answer", ""))
    if pid <= 0 or not answer:
        return error("require positive 'pid' and non-empty 'answer'", code="invalid_arg")
    _control_event(pid, "elicitation_response", answer_length=len(answer))
    return json_ok({"pid": pid, "answered": True, "answer_length": len(answer)})


@tool(
    "spawn_fixer",
    "Spawn fresh `claude -p /bmad-code-fix` subagent to address review findings.",
    {"worktree": str, "findings": list[dict[str, Any]]},
)
async def spawn_fixer(args: dict[str, Any]) -> dict[str, Any]:
    worktree = str(args.get("worktree", ""))
    findings = args.get("findings") or []
    if not worktree:
        return error("missing 'worktree' arg", code="invalid_arg")
    if not isinstance(findings, list):
        return error("'findings' must be a list", code="invalid_arg")

    severities = sorted({str(f.get("severity", "low")) for f in findings if isinstance(f, dict)})
    # Mock-mode: synthetic subagent id; real subprocess in S3.
    subagent_id = f"fixer-{abs(hash((worktree, len(findings)))) % 10_000:04d}"
    _control_event(
        0,
        "fixer_spawned",
        worktree=worktree,
        finding_count=len(findings),
        severities=severities,
        subagent_id=subagent_id,
    )
    return json_ok(
        {
            "worktree": worktree,
            "subagent_id": subagent_id,
            "finding_count": len(findings),
            "severities": severities,
            "mock": True,
        }
    )


TOOLS = [pause_worker, resume_worker, respond_to_elicitation, spawn_fixer]


__all__ = [
    "TOOLS",
    "pause_worker",
    "respond_to_elicitation",
    "resume_worker",
    "spawn_fixer",
]
