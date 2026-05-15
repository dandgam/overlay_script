"""Retrospective tools (spec §6.1 mandatory retros + §17).

spawn_retro_worktree, gen_wave2_prd_draft, detect_wave_boundary.

9 mandatory retros across Phase 4 → Phase 5:
- Wave retros × 6 (0a, 0b, 1a, 1b, 1c, 1d)
- Epic deep retros × 2 (Epic 1, Epic 7)
- Phase 5 final × 1 (co-design with human)
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool


@tool(
    "detect_wave_boundary",
    "Check if current wave is complete (all stories merged).",
    {"wave": str},
)
async def detect_wave_boundary(args: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": "TODO: detect boundary"}]}


@tool(
    "spawn_retro_worktree",
    "Spawn ephemeral retrospective worktree (fresh `claude -p /bmad-retrospective`).",
    {"wave": str, "level": str},  # level: wave | epic | phase
)
async def spawn_retro_worktree(args: dict[str, Any]) -> dict[str, Any]:
    """Role 4 from spec §7. Hard gate per spec §6.1 — нельзя пропустить."""
    return {"content": [{"type": "text", "text": "TODO: spawn retro"}]}


@tool(
    "gen_wave2_prd_draft",
    "Generate Wave 2 (Thor) PRD draft from Phase 4 retros. Co-design with human (spec §6.1 #9).",
    {},
)
async def gen_wave2_prd_draft(args: dict[str, Any]) -> dict[str, Any]:
    """Phase 5 final action. Output -> _bmad-output/planning-artifacts/wave-2-thor-prd-draft.md."""
    return {"content": [{"type": "text", "text": "TODO: gen PRD draft"}]}
