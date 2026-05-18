# Evals — Phase 3 Test & Release gate

Benchmark suite для Virgil. Без eval suite **Phase 3 ADLC не имеет gate'а**.

## Структура

```
evals/
├── cases/
│   ├── easy/        2 stories (single-step, happy path)
│   ├── medium/      2 stories (multi-step, no edge cases)
│   └── hard/        1 story (edge case / expected to escalate)
├── baseline/        Saved runs для regression detection
├── cases.yaml       Manifest: case ids → BMad story.md + expected outcomes
└── README.md
```

## Status

Step A (synthetic harness) — текущая итерация. 5 cases для отладки runner'а.
Step B (real Odyssey stories) — после Step A merge, добавит 10+ real cases.

## Запуск

```
bmad-orchestrator eval run                # все cases, mock mode (default)
bmad-orchestrator eval run --real         # с реальным claude binary (стоит токены)
bmad-orchestrator eval run --case TC-001  # один case
```

Report → stdout (table) + `evals/results-<timestamp>.json` (machine-readable).

## Метрики (см. `spec/methodology-virgil.md` §1 таблица)

| Метрика | Target |
|---|---|
| pass_rate | ≥ 85% |
| escalation_rate | ≤ 20% |
| review_iteration_p95 | ≤ 2 |
| cost_per_story_median | baseline → regression |
| cache_hit_rate | ≥ 50% (только real mode) |

Pass criteria для case: `final_verdict == expected.final_verdict` AND `review_iteration ≤ expected.max_iterations` AND `cost_usd ≤ expected.max_cost_usd`.
