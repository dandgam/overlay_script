---
name: wave-coordinator
description: Manage Wave 1a → 1b → ... → Phase 5 boundaries, gate human checkpoint every 10 stories or wave-end, integration→main merge proposal. Activates on wave & checkpoint events.
---

# wave-coordinator skill

## Когда активируется

- 10 stories merged (counter)
- `wave_boundary_reached`
- `epic_boundary_reached`
- `phase4_complete`

## Hard gates (spec §6.1, §9)

1. **Retro must be done** перед переходом к следующей wave — check `_bmad-output/_runs/<wave>/retrospective.md` exists
2. **Integration → main merge** = human-only checkpoint, агент не делает direct
3. **Human checkpoint каждые 10 merged stories** OR на wave boundary OR на FAILED status OR на budget warning 80%

## Actions

| Event | Action |
|-------|--------|
| 10 merged | escalate «10 stories done, продолжаем?» + snapshot |
| wave_done | trigger retrospective-writer → wait → check next wave can start |
| epic_done | retrospective-writer(level=epic) + checkpoint user |
| phase4_complete | retrospective-writer(level=phase5) → wait → present PRD draft for review |
| FAILED count > halt_threshold | escalate immediately, halt session |

## Tools

- `detect_wave_boundary`
- `spawn_retro_worktree`
- `escalate_to_human`
- `update_sprint_status` (mass — wave-level promotions)
