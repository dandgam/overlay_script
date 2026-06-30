# Spec — Per-step Verifier Contracts + Complexity Scoring

**Версия:** 0.1.0
**Дата:** 2026-05-20
**Автор:** AABIT
**Статус:** ready for /auto-loop-spec-long
**Источник:** bmad-automator `success_verifiers.py`, `data/orchestration-policy.json`, `data/complexity-rules.json`
**Размер:** LONG (6-10 сессий)
**Приоритет:** P1 — гибкость + reproducibility архитектурного уровня

---

## 1. Executive Summary

Сейчас гейты переходов между шагами (create / dev / review / commit / merge) **хардкодены в `phase4_subscribers.py`**. Чтобы изменить «что значит дев-стори прошла» — нужно править Python код, перекатывать тесты, релизить. Это медленно и приводит к копипаст-фиксам уровня NEW-XX.

Конкурент решает это через **policy-as-contract**: `data/orchestration-policy.json` декларативно задаёт для каждого step:

- `assets.required` — обязательные файлы skill
- `prompt.templateFile` + `templateHash` — fixed prompt
- `parse.schemaFile` + `schemaHash` — JSON Schema выходного формата
- `success.verifier` — имя verifier-функции (из реестра)
- `success.contractFile` (опционально) — дополнительный JSON с параметрами (например, `blockingSeverity`, `doneValues`, `sourceOrder`)

Verifiers зарегистрированы в Python (`VERIFIERS = {"create_story_artifact": ..., "review_completion": ..., "epic_complete": ..., "session_exit": ...}`) — но **выбор и параметры приходят из policy JSON**. Hash файлов проверяется на load → защита от drift.

Дополнительно: **complexity scoring** через `parse-story --rules complexity-rules.json` ДО agent selection. Запрещено угадывать сложность. Это влияет на выбор модели (Sonnet vs Opus), max_review_cycles, и нашего auto-split.

---

## 2. Goals / Non-Goals

### Goals
- G1: ввести `runtime/verifier_registry.py` с реестром именованных verifier'ов.
- G2: ввести `data/orchestration-policy.json` (или yaml) с per-step контрактом для всех шагов Virgil.
- G3: верификация шага вызывается через `verifier_registry.run(name, project_root, story, contract)`.
- G4: hash-pinning prompt templates и parse schemas (часть policy snapshot из spec_policy_snapshot_marker).
- G5: ввести `runtime/complexity_scorer.py` с rules-based scoring (LOC threshold, file count, test count, external deps mention).
- G6: complexity влияет на: model choice (worker), max_review_cycles, auto-split threshold.

### Non-Goals
- НЕ заменяем event-based phase4 subscribers полностью — verifier вызывается ИЗ subscriber, но не заменяет события.
- НЕ форсим один формат policy на BMad-проекты (Virgil reads its own policy + bmm/config.yaml если есть).
- НЕ переписываем skill code.

---

## 3. Архитектура

### Policy structure

```json
{
  "version": 1,
  "snapshot": {"relativeDir": "_bmad-output/virgil/policy-snapshots"},
  "runtime": {
    "parser": {"provider": "claude", "model": "claude-sonnet-4-6", "timeoutSeconds": 60},
    "judge":  {"provider": "claude", "model": "claude-opus-4-7", "timeoutSeconds": 120}
  },
  "workflow": {
    "sequence": ["create", "dev", "auto", "review", "commit"],
    "repeat":  {"review": {"maxCycles": 5}},
    "crash":   {"maxRetries": 2}
  },
  "steps": {
    "create": {
      "assets":  {"skillName": "bmad-create-story", "required": ["skill", "workflow"]},
      "prompt":  {"templateFile": "templates/create-prompt.md", "templateHash": "auto"},
      "parse":   {"schemaFile":   "schemas/create-output.json", "schemaHash":   "auto"},
      "success": {"verifier": "create_story_artifact",
                  "config": {"glob": "_bmad-output/implementation-artifacts/{story_prefix}-*.md", "expectedMatches": 1}}
    },
    "dev":     { /* same shape */ "success": {"verifier": "session_exit"} },
    "auto":    { "success": {"verifier": "session_exit"} },
    "review":  { "success": {"verifier": "review_completion",
                              "contractFile": "data/review-contract.json"} },
    "commit":  { "success": {"verifier": "git_commit_present"} }
  },
  "complexity": {
    "rulesFile": "data/complexity-rules.json"
  }
}
```

### Verifier registry

```python
# runtime/verifier_registry.py
VERIFIERS = {
    "create_story_artifact": create_story_artifact,    # glob + expectedMatches
    "session_exit":          session_exit,              # exit code 0 only
    "review_completion":     review_completion,         # dual-source (см. spec_dual_source_verdict)
    "epic_complete":         epic_complete,             # all stories in epic done
    "git_commit_present":    git_commit_present,        # HEAD changed after worker
}

def run(name, *, project_root, story_key, output_file="", contract=None) -> VerifyResult:
    fn = VERIFIERS.get(name)
    if not fn:
        raise PolicyError(f"unknown verifier: {name}")
    return fn(project_root=project_root, story_key=story_key, output_file=output_file, contract=contract or {})
```

### Complexity scoring

```json
// data/complexity-rules.json
{
  "version": 1,
  "weights": {
    "loc_estimate":   {"thresholds": [50, 200, 500], "points": [1, 3, 6, 10]},
    "file_count":     {"thresholds": [3, 8, 20],     "points": [1, 2, 4, 8]},
    "acceptance_criteria_count": {"thresholds": [3, 6, 10], "points": [1, 2, 4, 6]},
    "external_deps_mentioned":   {"per_mention": 2, "max": 8},
    "security_keywords_mentioned": {"per_mention": 3, "max": 12}
  },
  "levels": [
    {"name": "S", "max_score": 6,  "model": "sonnet", "max_review_cycles": 3, "auto_split": false},
    {"name": "M", "max_score": 15, "model": "sonnet", "max_review_cycles": 5, "auto_split": false},
    {"name": "L", "max_score": 30, "model": "sonnet", "max_review_cycles": 7, "auto_split": true},
    {"name": "XL","max_score": 999,"model": "opus",   "max_review_cycles": 10,"auto_split": true}
  ]
}
```

## 4. Изменения по файлам

| Файл | Что |
|---|---|
| `src/bmad_orchestrator/runtime/verifier_registry.py` (новый) | реестр + run() |
| `src/bmad_orchestrator/runtime/verifiers/*.py` (новый пакет) | каждый verifier отдельным файлом |
| `src/bmad_orchestrator/runtime/policy_loader.py` (новый) | загрузка + валидация policy JSON, hash-проверка |
| `src/bmad_orchestrator/data/orchestration-policy.json` (новый) | default policy |
| `src/bmad_orchestrator/data/complexity-rules.json` (новый) | default rules |
| `src/bmad_orchestrator/data/review-contract.json` (новый) | review contract |
| `src/bmad_orchestrator/runtime/complexity_scorer.py` (новый) | rules-based |
| `src/bmad_orchestrator/runtime/phase4_subscribers.py` | вызвать verifier_registry вместо inline проверок |
| `src/bmad_orchestrator/runtime/worker_spawn.py` | model selection через complexity |
| `src/bmad_orchestrator/runtime/auto_split.py` | trigger по complexity level |
| `tests/runtime/test_verifier_registry.py` (новый) | 8+ |
| `tests/runtime/test_policy_loader.py` (новый) | 6+ |
| `tests/runtime/test_complexity_scorer.py` (новый) | 10+ |

## 5. Acceptance Criteria

- AC1: policy.json валидируется на загрузке (unknown keys → PolicyError).
- AC2: Hash prompt template / parse schema проверяется → mismatch halt.
- AC3: Каждый шаг (create/dev/review/commit) вызывается через verifier_registry.run() — никаких inline проверок в phase4_subscribers.
- AC4: Complexity scorer выдаёт level S/M/L/XL для каждой story; результат пишется в `state/db.py` table `stories.complexity_level`.
- AC5: Worker model выбирается по complexity level (Sonnet для S/M/L, Opus для XL).
- AC6: Auto-split триггерится только для L+XL.
- AC7: Tests grow ≥24; 0 regressions.

## 6. Test Plan

Покрытие: policy load/validate (6), verifier registry (8), каждый verifier по отдельности (5+ на каждый = 25+), complexity scorer (10).

| Категория | Кол-во тестов |
|---|---|
| Policy structural validation | 6 |
| Verifier registry dispatch | 4 |
| create_story_artifact | 5 (happy, multi-match, no match, custom glob, custom expected) |
| session_exit | 3 |
| review_completion (см. spec_dual_source_verdict) | покрытие там |
| epic_complete | 4 |
| git_commit_present | 4 |
| Complexity scorer | 10 |
| Worker model selection by complexity | 3 |
| Auto-split triggered only for L+XL | 3 |

## 7. Rollout

- Feature flag: `BMAD_POLICY_DRIVEN_VERIFIERS=1` (default off → 50% pilots → on).
- Migration: existing phase4_subscribers оставляем как fallback пока flag off; включаем flag после смоук пилот.

## 8. Risks

| Риск | Митигация |
|---|---|
| Verifier registry становится too rigid | Каждый verifier принимает `contract` dict, гибкие параметры. Plus `--legacy-verifiers` flag |
| policy.json drift между versions Virgil | Hash + version в policy schema |
| Complexity scoring неточный → wrong model | rules-based + override через story frontmatter `complexity_override:` |
| Auto-split на L+XL ломает sequential ordering | auto-split sub-stories остаются sequential (это известный constraint из памяти) |

## 9. Effort

8-10 сессий. (1) policy_loader + validation, (2) verifier_registry + first verifier, (3-4) все verifiers + tests, (5) complexity_scorer + rules, (6) wiring в phase4_subscribers, (7) worker_spawn model select, (8) auto_split trigger, (9-10) integration + Antares pilot.

## 10. Dependencies

- Зависит от: spec_dual_source_verdict (review_completion verifier использует resolver оттуда).
- Зависит от: spec_policy_snapshot_marker (snapshot включает policy.json hash).
- Блокирует: spec_adversarial_review_bundled (review verifier ходит к bundled review skill).
