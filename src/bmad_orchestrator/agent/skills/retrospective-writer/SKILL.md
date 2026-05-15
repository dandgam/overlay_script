---
name: retrospective-writer
description: Generate wave / epic / phase retrospective in ephemeral worktree. Hard gate — mandatory per spec §6.1 (9 retros across Phase 4 → Phase 5).
---

# retrospective-writer skill

## Когда активируется (9 hard gates per spec §6.1)

| Trigger | Level |
|---------|-------|
| `wave_boundary_reached(wave=0a)` | wave |
| `wave_boundary_reached(wave=0b)` | wave |
| `wave_boundary_reached(wave=1a)` | wave |
| `wave_boundary_reached(wave=1b)` | wave |
| `wave_boundary_reached(wave=1c)` | wave |
| `wave_boundary_reached(wave=1d)` | wave |
| `epic_boundary_reached(epic=1)` | epic-deep |
| `epic_boundary_reached(epic=7)` | epic-deep |
| `phase4_complete` | phase5 |

## Pipeline

1. `spawn_retro_worktree(wave, level)` — ephemeral worktree
2. Внутри: `claude -p /bmad-retrospective <wave> <level>`
3. Output artifacts:
   - Wave: `_bmad-output/_runs/<wave>/retrospective.md`, `policy-deltas.yaml`, `lessons.md`
   - Epic-deep: `_bmad-output/retrospectives/epic-<N>-deep.md`
   - Phase5: `_bmad-output/retrospectives/phase4-final.md` + `_bmad-output/planning-artifacts/wave-2-thor-prd-draft.md`
4. Trigger `reflexion-learner` skill для policy.yaml deltas
5. Trigger `memory-curator` для compression
6. Phase 5 ONLY: эскалация к человеку «PRD draft готов, нужно strategic review»

## Hard gate semantics

- Skip = automatic escalation
- Артефакты обязательны, не виртуальные
- Следующая wave не стартует пока retro не сделан

## Tools

- `spawn_retro_worktree`, `write_memory`, `gen_wave2_prd_draft` (Phase 5 only), `escalate_to_human`
