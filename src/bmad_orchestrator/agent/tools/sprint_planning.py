"""Sprint-planning tools — BMad Phase 4 canonical init workflow.

Closes the gap «DAG planner раньше init'ил sprint-status.yaml ad-hoc; canonical
`bmad-sprint-planning` skill не вызывался». Mirrors the pattern in `retro.py`:

- `spawn_sprint_planning_worktree` — mock (write seed yaml from epics.md) или
  real (`claude -p /bmad-sprint-planning` в target worktree).
- `ensure_sprint_status_initialized` — guard helper, читается DAG planner'ом
  на bootstrap. Если sprint-status.yaml exists → no-op. Если missing + setting
  `auto_init_sprint_status=True` → spawn skill (mock by default). Else raise.

Used by:
- `runtime/dag_planner.py::DagPlanner.from_target()` (auto-init на старте wave)
- CLI `bmad-orchestrator sprint-planning` (manual trigger)
"""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from typing import Any

import yaml
from claude_agent_sdk import tool

from bmad_orchestrator.agent.tools._common import (
    append_jsonl,
    error,
    get_settings,
    json_ok,
    now_iso,
    runs_dir,
    sprint_status_path,
)
from bmad_orchestrator.config import Settings

SUBPROCESS_TIMEOUT_SEC = 300

_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


class SprintStatusMissingError(RuntimeError):
    """Raised when sprint-status.yaml absent AND auto_init disabled."""


def _epics_md_candidates(settings: Settings) -> tuple[Path, ...]:
    """Where to look for epics.md (mirrors sprint_status_path search order)."""
    s = settings
    return (
        s.target_project / s.artifacts_dir_name / "planning-artifacts" / "epics.md",
        s.target_project / "_bmad" / "planning-artifacts" / "epics.md",
        s.target_project / "_bmad" / "output" / "planning" / "epics.md",
    )


def _find_epics_md(settings: Settings) -> Path | None:
    for cand in _epics_md_candidates(settings):
        if cand.exists():
            return cand
    return None


_EPIC_HEADER_RE = re.compile(r"^##+\s+Epic\s+(\d+)\b[:.]?\s*(.*)$", re.MULTILINE | re.IGNORECASE)
_STORY_HEADER_RE = re.compile(
    r"^###+\s+Story\s+(\d+)[.-](\d+)\b[:.]?\s*(.*)$", re.MULTILINE | re.IGNORECASE
)


def _parse_epics_md(text: str) -> dict[str, Any]:
    """Minimal epics.md → sprint-status dict parser.

    Extracts `## Epic N: Title` and `### Story N.M: Title` headings into the
    canonical BMad shape `{epics: {1: {title, stories: {"1.1": {status, ...}}}}}`.
    Lossy by design — full parsing is the job of the real bmad-sprint-planning
    skill. This is a mock seed for tests and pre-real-init bootstrap.
    """
    epics: dict[str, Any] = {}
    current_epic: str | None = None
    for line in text.splitlines():
        m_e = _EPIC_HEADER_RE.match(line)
        if m_e:
            current_epic = m_e.group(1)
            epics[current_epic] = {
                "title": m_e.group(2).strip(),
                "status": "backlog",
                "stories": {},
            }
            continue
        m_s = _STORY_HEADER_RE.match(line)
        if m_s and current_epic:
            sid = f"{m_s.group(1)}.{m_s.group(2)}"
            epics[current_epic]["stories"][sid] = {
                "title": m_s.group(3).strip(),
                "status": "backlog",
            }
    return {
        "generated": now_iso(),
        "last_updated": now_iso(),
        "project": "auto-init",
        "epics": epics,
    }


def ensure_sprint_status_initialized(
    settings: Settings | None = None,
    *,
    allow_real_spawn: bool = False,
) -> dict[str, Any]:
    """Guard for DAG planner bootstrap.

    Returns:
      {"action": "exists"|"created_mock"|"spawned_real", "path": "..."}.

    Raises:
      SprintStatusMissingError — if file missing AND
      ``settings.auto_init_sprint_status`` is False.
    """
    s = settings or get_settings()
    path = sprint_status_path(s)
    if path.exists():
        return {"action": "exists", "path": str(path)}

    if not s.auto_init_sprint_status:
        raise SprintStatusMissingError(
            f"sprint-status.yaml not found at {path}. "
            "Run `bmad-orchestrator sprint-planning` or "
            "set `auto_init_sprint_status=True` in Settings."
        )

    epics_md = _find_epics_md(s)
    if epics_md is None:
        # Soft path: no canonical source to derive sprint-status from.
        # Don't raise — downstream code (story filter, wave loop) handles
        # the "nothing to run" case with clearer per-call errors. Loud audit
        # entry preserves the gap-closure intent (we tried to auto-init).
        append_jsonl(
            runs_dir() / "sprint_planning.events.jsonl",
            {
                "event_type": "sprint_status_auto_init_skipped",
                "ts": now_iso(),
                "reason": "epics_md_missing",
                "target_project": str(s.target_project),
            },
        )
        return {
            "action": "skipped_no_epics_md",
            "path": str(path),
            "target_project": str(s.target_project),
        }

    seed = _parse_epics_md(epics_md.read_text(encoding="utf-8"))
    if not seed.get("epics"):
        raise SprintStatusMissingError(
            f"epics.md at {epics_md} contains no '## Epic N' headings; "
            "cannot derive sprint-status seed."
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(seed, f, allow_unicode=True, sort_keys=False)

    append_jsonl(
        runs_dir() / "sprint_planning.events.jsonl",
        {
            "event_type": "sprint_status_auto_init",
            "ts": now_iso(),
            "path": str(path),
            "epics_source": str(epics_md),
            "epic_count": len(seed["epics"]),
            "real_spawn": False,
            "allow_real_spawn": allow_real_spawn,
        },
    )
    return {"action": "created_mock", "path": str(path), "epic_count": len(seed["epics"])}


@tool(
    "spawn_sprint_planning_worktree",
    "Spawn bmad-sprint-planning skill (mock by default; real=True spawns `claude -p /bmad-sprint-planning`).",
    {"real": bool},
)
async def spawn_sprint_planning_worktree(args: dict[str, Any]) -> dict[str, Any]:
    real = bool(args.get("real", False))
    settings = get_settings()
    path = sprint_status_path(settings)

    if path.exists():
        return json_ok({"action": "exists", "path": str(path), "mock": not real})

    if not real:
        try:
            result = ensure_sprint_status_initialized(settings)
        except SprintStatusMissingError as exc:
            return error(str(exc), code="sprint_init_failed")
        return json_ok({**result, "mock": True})

    claude_bin = shutil.which("claude")
    if not claude_bin:
        return error(
            "`claude` binary not found in PATH; cannot spawn real sprint-planning worktree",
            code="claude_missing",
        )

    from bmad_orchestrator.runtime.sandbox import detect_sandbox
    from bmad_orchestrator.runtime.worker_spawn import _build_worker_env

    sp_env = _build_worker_env({
        "ORCHESTRATOR_WORKFLOW": "sprint-planning",
    })

    sandbox = detect_sandbox()
    target_root = settings.target_project
    target_root.mkdir(parents=True, exist_ok=True)

    wrapped_cmd = sandbox.wrap_command(
        [claude_bin, "-p", "/bmad-sprint-planning"],
        worktree=target_root,
        network="none",
        env=sp_env,
    )

    proc = await asyncio.create_subprocess_exec(
        *wrapped_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=sp_env,
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
                    "command": "claude -p /bmad-sprint-planning",
                    "pid": proc.pid,
                    "timeout_sec": SUBPROCESS_TIMEOUT_SEC,
                },
            )

    task = asyncio.create_task(_wait())
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)

    return json_ok(
        {
            "action": "spawned_real",
            "path": str(path),
            "subagent_id": f"sprint-planning-{proc.pid}",
            "mock": False,
            "pid": proc.pid,
            "sandbox_used": sandbox.kind != "none",
            "sandbox_kind": sandbox.kind,
        }
    )


TOOLS = [spawn_sprint_planning_worktree]
