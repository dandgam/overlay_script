---
name: elicitation-router
description: Map incoming worker question via policy YAML → auto-answer / escalate / batch-defer. Activates on worker_elicitation event.
---

# elicitation-router skill

## Когда активируется

- Event `worker_elicitation` (worker задал уточняющий вопрос)
- Source: JSONL event `{"type": "elicitation", "question": ..., "topics": [...]}`

## Algorithm

1. Match вопрос к rules в `policy.yaml` (first-match-wins, см. `examples/elicitation-policy.example.yaml`)
2. По matched rule action:
   - `auto_resolve` → respond_to_elicitation(pid, default_answer) + log
   - `conditional` → проверить условие → auto или escalate
   - `escalate` → escalate_to_human(reason, context)
3. Если no match → fallback per `defaults.unknown_topic_action` (default: escalate)
4. Если за story уже >5 auto-resolves → flag как suspicious, escalate

## Hard rules (всегда escalate, override policy)

- security / crypto / PII handling
- destructive ops
- DB migration strategy
- cost-impacting decisions (>$10 token consequence)

## `feedback_decide_dont_halt` rule

Halt только при: destructive ops, security/data risk, кардинальный pivot, spend >$50.
Все formality issues (naming, error pattern, test placement) → auto_resolve.

## Tools

- `respond_to_elicitation`, `escalate_to_human`, `read_memory(path="policy-deltas.yaml")`
