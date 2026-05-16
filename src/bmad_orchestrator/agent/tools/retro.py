"""Retrospective tools (spec §6.1 + §17).

detect_wave_boundary, spawn_retro_worktree, gen_wave2_prd_draft.

Hard gates: agent физически не может перейти к next wave если retrospective.md
не существует. detect_wave_boundary возвращает {complete: bool, retro_done: bool}
— wave_coordinator skill использует это.

Mock-mode:
- detect_wave_boundary — все stories=done в sprint-status И retrospective.md exists.
- spawn_retro_worktree — синтетический subagent id; writes seed retrospective.md.
- gen_wave2_prd_draft — пишет skeleton PRD под planning-artifacts/.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from bmad_orchestrator.agent.tools._common import (
    artifacts_dir,
    error,
    get_settings,
    json_ok,
    memory_dir,
    now_iso,
    read_sprint_status_yaml,
)


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

    retro_path = memory_dir() / "per-wave" / f"{wave}-retrospective.md"
    retro_done = retro_path.exists()

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
    "Spawn ephemeral retrospective worktree (fresh `claude -p /bmad-retrospective`).",
    {"wave": str, "level": str},
)
async def spawn_retro_worktree(args: dict[str, Any]) -> dict[str, Any]:
    wave = str(args.get("wave", "")).strip()
    level = str(args.get("level", "wave"))
    if not wave:
        return error("missing 'wave'", code="invalid_arg")
    if level not in ("wave", "epic", "phase"):
        return error(f"invalid level: {level!r}", code="invalid_arg")

    out = memory_dir() / "per-wave" / f"{wave}-retrospective.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists():
        out.write_text(
            f"---\nwave: {wave}\nlevel: {level}\ncreated: {now_iso()}\n---\n\n"
            "# Retrospective seed\n\n_TODO: agent must fill from per-story lessons._\n",
            encoding="utf-8",
        )
    subagent_id = f"retro-{wave}-{abs(hash((wave, level))) % 10_000:04d}"
    return json_ok(
        {
            "wave": wave,
            "level": level,
            "retrospective_path": str(out),
            "subagent_id": subagent_id,
            "mock": True,
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
