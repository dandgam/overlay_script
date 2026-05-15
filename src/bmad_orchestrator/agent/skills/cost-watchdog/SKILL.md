---
name: cost-watchdog
description: Track token spend per worker/batch/wave, halt at hard cap, escalate at 80% threshold. Activates continuously on budget_threshold_hit events.
---

# cost-watchdog skill

## Когда активируется

- Event `budget_threshold_hit` (генерится `BudgetGuard` интерсептором, не агент)
- В chat: «сколько потрачено?», «какая дорогая story?»

## Two-tier semantics (spec §8)

| Scope | Alarm threshold | Halt threshold |
|-------|----------------|-----------------|
| Story | $30 | $50 |
| Batch | $200 | $300 |

## Actions

| Event | Action |
|-------|--------|
| alarm | escalate_to_human with snapshot, continue |
| halt | escalate_to_human + spawn no new workers + finish current + halt session |
| budget OK при chat-вопросе | report snapshot |

## Optimizations to suggest

При alarm агент **предлагает** оптимизации:
- Переключить routine на Haiku — ~30% экономии без потери качества
- Reduce parallelism temporarily — снижает TPM hit риск
- Defer expensive security-stories пока не освободится бюджет

## Tools

- `get_budget`, `escalate_to_human`, `set_model` (если user agrees on cheaper)
