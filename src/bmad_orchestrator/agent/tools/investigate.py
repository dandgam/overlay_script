"""Investigate tools — BMad Phase 4 canonical forensic workflow.

Wires `bmad-investigate` skill для deep-dive когда `failure-analyst`
детектит non-trivial failure (3+ retries или unclassified error category).
Mirrors retro.py / sprint_planning.py / correct_course.py spawn pattern.

Auto-trigger sources:
- `runtime/failure_analyst_subscriber.py` — на retry_count >= 3 или
  error_category="unclassified" вызывает `should_investigate()` → spawn.
- CLI: `bmad-orchestrator investigate <subject> --reason "..."` (manual).
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

SUBPROCESS_TIMEOUT_SEC = 900  # forensic deep-dive — longer cap than dev

_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()

# Heuristic thresholds for auto-trigger from failure-analyst.
RETRY_THRESHOLD = 3
UNCLASSIFIED_CATEGORIES = frozenset({"unclassified", "unknown", "other"})


def should_investigate(retry_count: int, error_category: str | None) -> bool:
    """Decide if bmad-investigate forensic deep-dive should be auto-spawned.

    Triggered when:
    - retry_count >= 3 (recurring failure pattern), OR
    - error_category is unclassified/unknown (failure-analyst couldn't bucket it).
    """
    if retry_count >= RETRY_THRESHOLD:
        return True
    if error_category and error_category.lower() in UNCLASSIFIED_CATEGORIES:
        return True
    return False


@tool(
    "spawn_investigate_worktree",
    "Spawn bmad-investigate skill for forensic deep-dive (mock by default; real=True spawns `claude -p /bmad-investigate`).",
    {"subject": str, "reason": str, "real": bool},
)
async def spawn_investigate_worktree(args: dict[str, Any]) -> dict[str, Any]:
    subject = str(args.get("subject", "")).strip()
    reason = str(args.get("reason", "")).strip()
    real = bool(args.get("real", False))
    if not subject:
        return error("missing 'subject' (story_id / error_class / incident_id)", code="invalid_arg")
    if not reason:
        return error("missing 'reason' — investigate requires a stated rationale", code="invalid_arg")

    settings = get_settings()
    out_dir = artifacts_dir(settings) / "investigate"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_subject = "".join(c if c.isalnum() or c in "-_." else "_" for c in subject)[:60]
    out = out_dir / f"{safe_subject}-investigate.md"

    if not real:
        if not out.exists():
            out.write_text(
                f"---\nsubject: {subject}\ntriggered: {now_iso()}\nmock: true\n---\n\n"
                f"# Investigation seed — {subject}\n\n"
                f"**Reason:** {reason}\n\n"
                "_TODO: agent must fill via real `/bmad-investigate`._\n",
                encoding="utf-8",
            )
        append_jsonl(
            runs_dir() / "investigate.events.jsonl",
            {
                "event_type": "investigate_mock",
                "ts": now_iso(),
                "subject": subject,
                "reason": reason,
                "path": str(out),
            },
        )
        return json_ok({
            "subject": subject,
            "reason": reason,
            "path": str(out),
            "mock": True,
            "subagent_id": f"investigate-{safe_subject}-mock",
        })

    claude_bin = shutil.which("claude")
    if not claude_bin:
        return error(
            "`claude` binary not found in PATH; cannot spawn real investigate worktree",
            code="claude_missing",
        )

    from bmad_orchestrator.runtime.sandbox import detect_sandbox
    from bmad_orchestrator.runtime.worker_spawn import _build_worker_env

    inv_env = _build_worker_env({
        "ORCHESTRATOR_WORKFLOW": "investigate",
        "ORCHESTRATOR_SUBJECT": subject,
        "ORCHESTRATOR_REASON": reason,
    })

    sandbox = detect_sandbox()
    wrapped_cmd = sandbox.wrap_command(
        [claude_bin, "-p", f"/bmad-investigate {subject}"],
        worktree=out.parent,
        network="none",
        env=inv_env,
    )

    proc = await asyncio.create_subprocess_exec(
        *wrapped_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=inv_env,
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
                    "command": "claude -p /bmad-investigate",
                    "pid": proc.pid,
                    "subject": subject,
                    "timeout_sec": SUBPROCESS_TIMEOUT_SEC,
                },
            )

    task = asyncio.create_task(_wait())
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)

    return json_ok({
        "subject": subject,
        "reason": reason,
        "path": str(out),
        "subagent_id": f"investigate-{safe_subject}-{proc.pid}",
        "mock": False,
        "pid": proc.pid,
        "sandbox_used": sandbox.kind != "none",
        "sandbox_kind": sandbox.kind,
    })


TOOLS = [spawn_investigate_worktree]
