"""Three-level memory + retrospective schedule + hard gates (spec §6, §6.1, §18.5).

Public API consumed by:
- retrospective-writer skill (write retro artifacts via tools/retro.py).
- reflexion-learner skill (read past lessons, append cross-wave patterns).
- wave-coordinator skill (can_promote_wave hard gate).
- proactive-improver skill (build_proposals after each retro).
- agent/run.py (S8) — `memory_tool_definition()` для Anthropic Memory Tool
  beta `context-management-2025-06-27` (см. agent/betas.py).

Layers (spec §6):

| Layer | Trigger | Artifact |
|-------|---------|----------|
| Tactical (per story) | story done | `memory/per-story/<id>.lesson.md` |
| Strategic (per wave) | wave_boundary_reached | `memory/per-wave/<wave>.md` |
| Architectural (per phase) | phase boundary | `memory/per-phase/<phase>.md` |
"""

from __future__ import annotations

from bmad_orchestrator.agent.memory.gates import (
    HardGateError,
    can_promote_wave,
    is_retro_done,
    missing_retros,
    retro_artifact_path,
)
from bmad_orchestrator.agent.memory.levels import (
    ArchitecturalLesson,
    StrategicLesson,
    TacticalLesson,
    record_architectural_lesson,
    record_strategic_lesson,
    record_tactical_lesson,
)
from bmad_orchestrator.agent.memory.memory_tool import (
    MEMORY_TOOL_BETA,
    MEMORY_TOOL_NAME,
    MEMORY_TOOL_TYPE,
    memory_tool_definition,
)
from bmad_orchestrator.agent.memory.proposals import (
    Proposal,
    ProposalRisk,
    ProposalType,
    build_proposals,
)
from bmad_orchestrator.agent.memory.schedule import (
    MANDATORY_RETROS,
    WAVE_SEQUENCE,
    RetroId,
    RetroLevel,
    previous_wave,
)

__all__ = [
    "MANDATORY_RETROS",
    "MEMORY_TOOL_BETA",
    "MEMORY_TOOL_NAME",
    "MEMORY_TOOL_TYPE",
    "WAVE_SEQUENCE",
    "ArchitecturalLesson",
    "HardGateError",
    "Proposal",
    "ProposalRisk",
    "ProposalType",
    "RetroId",
    "RetroLevel",
    "StrategicLesson",
    "TacticalLesson",
    "build_proposals",
    "can_promote_wave",
    "is_retro_done",
    "memory_tool_definition",
    "missing_retros",
    "previous_wave",
    "record_architectural_lesson",
    "record_strategic_lesson",
    "record_tactical_lesson",
    "retro_artifact_path",
]
