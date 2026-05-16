"""Retrospective tools (spec §6.1 + §17).

Tools:
- `detect_wave_boundary` — hard-gate awareness (`all_stories_done` + `retro_done`).
- `spawn_retro_worktree` — mock by default; `real=True` форкает `claude -p
  /bmad-retrospective <wave> <level>` фоновым subprocess (background task,
  fire-and-forget; completion detected via artifact polling).
- `gen_wave2_prd_draft` — пишет skeleton PRD под planning-artifacts/.

Hard gate semantics (spec §6.1): агент физически не может перейти к next wave
если retrospective.md отсутствует. `detect_wave_boundary` возвращает
`retro_done` отдельным полем — `wave_coordinator` skill consume'ит.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from bmad_orchestrator.agent.tools._common import (
    append_jsonl,
    artifacts_dir,
    error,
    get_settings,
    json_ok,
    now_iso,
    read_sprint_status_yaml,
    runs_dir,
)

# FS3 H15: 300s upper bound on the retro subprocess so a stuck `claude -p`
# child can't pin a background asyncio task forever. Retros are short-lived
# (single skill invocation, a few minutes at worst).
SUBPROCESS_TIMEOUT_SEC = 300

# Module-level set keeps background subprocess watchers from being GC'd
# (RUF006). spawn_worker.py uses the same pattern.
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


def _retro_path_for(wave: str, level: str) -> Path:
    """Compute canonical retro artifact path via agent.memory (lazy import to
    avoid agent.tools ↔ agent.memory import cycle)."""
    from bmad_orchestrator.agent.memory.gates import retro_artifact_path
    from bmad_orchestrator.agent.memory.schedule import RetroId, RetroLevel

    if level == "wave":
        retro_id = RetroId(RetroLevel.WAVE, wave)
    elif level == "epic":
        retro_id = RetroId(RetroLevel.EPIC, wave)
    else:
        retro_id = RetroId(RetroLevel.PHASE, "5")
    return retro_artifact_path(retro_id)


def _retro_slug_for(wave: str, level: str) -> str:
    from bmad_orchestrator.agent.memory.schedule import RetroId, RetroLevel

    if level == "wave":
        return RetroId(RetroLevel.WAVE, wave).slug
    if level == "epic":
        return RetroId(RetroLevel.EPIC, wave).slug
    return RetroId(RetroLevel.PHASE, "5").slug


@tool(
    "detect_wave_boundary",
    "Check if current wave is complete (all stories merged + retro.md present).",
    {"wave": str},
)
async def detect_wave_boundary(args: dict[str, Any]) -> dict[str, Any]:
    wave = str(args.get("wave", "")).strip()
    if not wave:
        return error("missing 'wave'", code="invalid_arg")

    sprint = read_sprint_status_yaml()
    epics = sprint.get("epics") or {}
    statuses: list[str] = []
    for epic in epics.values():
        for st in (epic.get("stories") or {}).values():
            statuses.append(str(st))
    all_done = bool(statuses) and all(s == "done" for s in statuses)

    # N4 (FS6) — content-schema check (frontmatter + body) via is_retro_done,
    # not `st_size > 0`. The seed stub written by spawn_retro_worktree (~100
    # chars, frontmatter + TODO) used to slip past the size check and falsely
    # mark the wave as complete.
    from bmad_orchestrator.agent.memory.gates import is_retro_done
    from bmad_orchestrator.agent.memory.schedule import RetroId, RetroLevel

    retro_done = is_retro_done(RetroId(RetroLevel.WAVE, wave))

    return json_ok(
        {
            "wave": wave,
            "complete": all_done and retro_done,
            "all_stories_done": all_done,
            "retro_done": retro_done,
            "story_count": len(statuses),
        }
    )


@tool(
    "spawn_retro_worktree",
    "Spawn ephemeral retrospective worktree (mock by default; real=True spawns `claude -p /bmad-retrospective`).",
    {"wave": str, "level": str, "real": bool},
)
async def spawn_retro_worktree(args: dict[str, Any]) -> dict[str, Any]:
    wave = str(args.get("wave", "")).strip()
    level = str(args.get("level", "wave"))
    real = bool(args.get("real", False))
    if not wave:
        return error("missing 'wave'", code="invalid_arg")
    if level not in ("wave", "epic", "phase"):
        return error(f"invalid level: {level!r}", code="invalid_arg")

    out = _retro_path_for(wave, level)
    slug = _retro_slug_for(wave, level)
    out.parent.mkdir(parents=True, exist_ok=True)

    if not real:
        if not out.exists():
            out.write_text(
                f"---\nwave: {wave}\nlevel: {level}\ncreated: {now_iso()}\n---\n\n"
                "# Retrospective seed\n\n"
                "_TODO: agent must fill from per-story lessons._\n",
                encoding="utf-8",
            )
        subagent_id = f"retro-{slug}-{abs(hash((wave, level))) % 10_000:04d}"
        return json_ok(
            {
                "wave": wave,
                "level": level,
                "retrospective_path": str(out),
                "subagent_id": subagent_id,
                "mock": True,
            }
        )

    claude_bin = shutil.which("claude")
    if not claude_bin:
        return error(
            "`claude` binary not found in PATH; cannot spawn real retro worktree",
            code="claude_missing",
        )

    # N7 (FS6) — retro subprocess must NOT inherit secret-bearing env vars
    # (ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, etc). Reuse the worker spawn
    # allow-list so the retro subagent runs in the same locked-down env as a
    # regular dev worker.
    from bmad_orchestrator.runtime.sandbox import detect_sandbox
    from bmad_orchestrator.runtime.worker_spawn import _build_worker_env

    retro_env = _build_worker_env({
        "ORCHESTRATOR_WAVE": wave,
        "ORCHESTRATOR_LEVEL": level,
    })

    # FS7 — wrap retro subagent in the same bwrap sandbox as dev workers.
    # Retro needs to read the wave's planning artifacts and write a single
    # retrospective.md, both reside under the canonical retro path
    # `out.parent`; treat that as the writable worktree. Default network=none
    # since retros aggregate local lessons, no remote fetch required.
    sandbox = detect_sandbox()
    wrapped_cmd = sandbox.wrap_command(
        [
            claude_bin,
            "-p",
            f"/bmad-retrospective {wave} {level}",
        ],
        worktree=out.parent,
        network="none",
        env=retro_env,
    )

    proc = await asyncio.create_subprocess_exec(
        *wrapped_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=retro_env,
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
                    "command": "claude -p /bmad-retrospective",
                    "pid": proc.pid,
                    "wave": wave,
                    "level": level,
                    "timeout_sec": SUBPROCESS_TIMEOUT_SEC,
                },
            )

    task = asyncio.create_task(_wait())
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)

    return json_ok(
        {
            "wave": wave,
            "level": level,
            "retrospective_path": str(out),
            "subagent_id": f"retro-{slug}-{proc.pid}",
            "mock": False,
            "pid": proc.pid,
            "sandbox_used": sandbox.kind != "none",
            "sandbox_kind": sandbox.kind,
        }
    )


@tool(
    "gen_wave2_prd_draft",
    "Generate Wave 2 (Thor) PRD draft from Phase 4 retros. Co-design with human.",
    {},
)
async def gen_wave2_prd_draft(args: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    target = artifacts_dir(settings) / "wave-2-thor-prd-draft.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        f"---\ngenerated_at: {now_iso()}\nlevel: phase-5\nstatus: draft\n---\n\n"
        "# Wave 2 — Thor PRD Draft\n\n"
        "_skeleton — fill from Phase 4 retros (see memory/per-wave/*.md)._\n\n"
        "## Vision\nTODO\n\n## Scope\nTODO\n\n## Out of scope\nTODO\n",
        encoding="utf-8",
    )
    return json_ok({"path": str(target), "status": "draft"})


TOOLS = [detect_wave_boundary, spawn_retro_worktree, gen_wave2_prd_draft]


__all__ = [
    "TOOLS",
    "detect_wave_boundary",
    "gen_wave2_prd_draft",
    "spawn_retro_worktree",
]
