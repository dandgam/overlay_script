"""Correct-course tools — BMad Phase 4 canonical mid-sprint handler.

Wires `bmad-correct-course` skill (uже embedded в `skills_repo.py`) в
supervisor flow + CLI. Trigger sources:

- Supervisor subscriber catches `SPRINT_SCOPE_CHANGE_DETECTED` event → spawns
  this tool to invoke the skill on the affected wave.
- CLI: `bmad-orchestrator correct-course --story X --reason "..."` (manual).

Mirrors retro.py / sprint_planning.py spawn pattern.
"""

from __future__ import annotations

import asyncio
import shutil
from typing import Any

from claude_agent_sdk import tool

from bmad_orchestrator.agent.tools._common import (
    append_jsonl,
    artifacts_dir,
    error,
    get_settings,
    json_ok,
    now_iso,
    runs_dir,
)

SUBPROCESS_TIMEOUT_SEC = 600

_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


@tool(
    "spawn_correct_course_worktree",
    "Spawn bmad-correct-course skill for mid-sprint scope change (mock by default; real=True spawns `claude -p /bmad-correct-course`).",
    {"story_id": str, "reason": str, "real": bool},
)
async def spawn_correct_course_worktree(args: dict[str, Any]) -> dict[str, Any]:
    story_id = str(args.get("story_id", "")).strip()
    reason = str(args.get("reason", "")).strip()
    real = bool(args.get("real", False))
    if not story_id:
        return error("missing 'story_id'", code="invalid_arg")
    if not reason:
        return error("missing 'reason' — correct-course requires a stated rationale", code="invalid_arg")

    settings = get_settings()
    out_dir = artifacts_dir(settings) / "correct-course"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{story_id}-correct-course.md"

    if not real:
        if not out.exists():
            out.write_text(
                f"---\nstory_id: {story_id}\ntriggered: {now_iso()}\nmock: true\n---\n\n"
                f"# Correct-course seed — {story_id}\n\n"
                f"**Reason:** {reason}\n\n"
                "_TODO: agent must fill via real `/bmad-correct-course`._\n",
                encoding="utf-8",
            )
        append_jsonl(
            runs_dir() / "correct_course.events.jsonl",
            {
                "event_type": "correct_course_mock",
                "ts": now_iso(),
                "story_id": story_id,
                "reason": reason,
                "path": str(out),
            },
        )
        return json_ok({
            "story_id": story_id,
            "reason": reason,
            "path": str(out),
            "mock": True,
            "subagent_id": f"correct-course-{story_id}-mock",
        })

    claude_bin = shutil.which("claude")
    if not claude_bin:
        return error(
            "`claude` binary not found in PATH; cannot spawn real correct-course worktree",
            code="claude_missing",
        )

    from bmad_orchestrator.runtime.sandbox import detect_sandbox
    from bmad_orchestrator.runtime.worker_spawn import _build_worker_env

    cc_env = _build_worker_env({
        "ORCHESTRATOR_WORKFLOW": "correct-course",
        "ORCHESTRATOR_STORY_ID": story_id,
        "ORCHESTRATOR_REASON": reason,
    })

    sandbox = detect_sandbox()
    wrapped_cmd = sandbox.wrap_command(
        [claude_bin, "-p", f"/bmad-correct-course {story_id}"],
        worktree=out.parent,
        network="none",
        env=cc_env,
    )

    proc = await asyncio.create_subprocess_exec(
        *wrapped_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=cc_env,
    )

    async def _wait() -> None:
        try:
            await asyncio.wait_for(proc.wait(), timeout=SUBPROCESS_TIMEOUT_SEC)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            append_jsonl(
                runs_dir() / "subprocess.events.jsonl",
                {
                    "event_type": "subprocess_timeout",
                    "ts": now_iso(),
                    "command": "claude -p /bmad-correct-course",
                    "pid": proc.pid,
                    "story_id": story_id,
                    "timeout_sec": SUBPROCESS_TIMEOUT_SEC,
                },
            )

    task = asyncio.create_task(_wait())
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)

    return json_ok({
        "story_id": story_id,
        "reason": reason,
        "path": str(out),
        "subagent_id": f"correct-course-{story_id}-{proc.pid}",
        "mock": False,
        "pid": proc.pid,
        "sandbox_used": sandbox.kind != "none",
        "sandbox_kind": sandbox.kind,
    })


TOOLS = [spawn_correct_course_worktree]
