---
name: reflexion-learner
description: Async post-wave Reflexion (Actor / Evaluator / Self-Reflection) — extracts patterns from elicitation logs + failures, proposes policy.yaml deltas. Activates on wave_boundary_reached after retrospective-writer.
---

# reflexion-learner skill

## Pattern: Reflexion (research §18.5)

```
Actor (LLM)         : "Вот что произошло в wave 1a: 12/12 stories, 3 escalations, 1 merge conflict"
        │
        ▼
Evaluator (LLM)     : "Из 3 escalations: 2 были auto-resolvable если бы policy.yaml имел rule X"
        │
        ▼
Self-Reflection (LLM): "Предлагаю rule X. Pattern для future: pre-validate Y перед spawn"
        │
        ▼
Write to Memory Tool: lessons/wave-1a-reflections.md + policy-deltas.yaml
        │
        ▼
Next wave loads → starts smarter
```

## Когда активируется

- После того как `retrospective-writer` завершила wave retro
- На phase boundary — extra deep run

## Input

- `_bmad-output/runs/<wave>/retrospective.md` (от retro skill)
- Все per-story JSONL events за wave
- Existing `policy.yaml` для diff'а

## Output

- `<wave>/policy-deltas.yaml` — proposed additions
- `<wave>/architectural-insights.md` — patterns
- `<orchestrator>/memory/cross-wave-learnings.md` — append summarized

## Constraints

- Не autoapply policy deltas — human review через chat: «вот предложения, апрувить?»
- Limit длина deltas (anti-bloat per research §18.3 anti-pattern #2)
- Существующие policy rules не модифицируем без явного human consent

## Tools

- `read_memory`, `write_memory`, `escalate_to_human` (для policy review)
