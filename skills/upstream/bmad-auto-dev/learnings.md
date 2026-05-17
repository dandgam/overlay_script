# bmad-auto-dev — Learnings

Self-learning notes accumulated across runs. Append-only.

## Format

```
[YYYY-MM-DD] <story-id or batch> <category>: <one-line learning>
  detail: <2-3 lines of context>
  applies_to: <future story types this affects>
```

## Categories

- `gauntlet-mode-correction` — auto-detect chose wrong mode (e.g., should have been deep, was quick)
- `dependency-edge-case` — analyzer missed a dep or false-positive
- `branch-lifecycle` — merge / cleanup issue
- `headless-context` — claude -p missed context that interactive would catch
- `cost` — token / time outlier
- `halt-cause` — why a story halted

(empty — initialised in S1 bootstrap of bmad-auto-dev skill)
