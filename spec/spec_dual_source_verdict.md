# Spec — Dual-source Verdict + Git Reality Cross-check

**Версия:** 0.1.0
**Дата:** 2026-05-20
**Автор:** AABIT
**Статус:** ready for /auto-loop-spec-short
**Источник:** анализ bmad-code-org/bmad-automator (`review_completion` verifier, `sourceOrder` контракт)
**Размер:** SHORT-MEDIUM (2-4 сессии)
**Приоритет:** P0 — архитектурно закрывает повторение NEW-9 / NEW-21 / NEW-26

---

## 1. Executive Summary

Сейчас Virgil считает story done только если review-runner записал valid jsonl с `verdict=approve`. Если runner упал, завис в интерактиве, или процесс exit-code non-zero — verdict теряется и появляется false-negative. Уже пришлось патчить NEW-9, NEW-21, NEW-26 — это **разные симптомы одной архитектурной проблемы**: один источник истины.

У конкурента (bmad-automator) `review_completion` verifier проверяет done по **двум источникам**:

1. **`sprint-status.yaml`** (или нашего эквивалента — `_bmad-output/sprint-status.yaml` / `.bmad/state`)
2. **Story-file Status frontmatter** (наш `_bmad-output/implementation-artifacts/{prefix}-*.md`, поле `Status:`)

С приоритетом `sourceOrder: [sprint-status.yaml, story-file]`. Плюс **git-reality cross-check**: `story.FileList` сравнивается с `git diff --name-only HEAD~N` — ловит «worker заявил, что сделал, но коммит пустой».

Перенести эти две вещи в Virgil = одним движением закрыть три открытых класса багов и предотвратить весь будущий хвост NEW-XX той же природы.

---

## 2. Goals / Non-Goals

### Goals
- G1: ввести `dual_source_verdict_resolver` в `runtime/verdict_fallback.py` (либо новый `runtime/verdict_resolver.py`).
- G2: настраиваемый `sourceOrder` через `_bmad/_config/orchestrator-policy.yaml` (опционально, default: jsonl → story-file → sprint-status).
- G3: ввести `git_reality_check(story)` — сравнить заявленные файлы из dev_agent_record с `git diff --name-only`. Расхождения → mark story as suspicious, не блок.
- G4: интегрировать оба check в `phase4_subscribers.py` review handling.

### Non-Goals
- НЕ переписываем review-runner (это spec_adversarial_review_bundled).
- НЕ меняем формат jsonl.
- НЕ удаляем circuit breaker (NEW-13 fix остаётся, но станет реже срабатывать).

---

## 3. Архитектура решения

```
                  ┌─────────────────────────────┐
review-runner ──▶ │ jsonl verdict (primary)     │
                  └─────────────────────────────┘
                              │
                              ▼ (если valid)
                       ACCEPT done
                              │
                              ▼ (если invalid/missing/error)
                  ┌─────────────────────────────┐
                  │ story-file Status: done?    │
                  └─────────────────────────────┘
                              │
                              ▼ (yes)
                       ACCEPT done (with note: jsonl_missing)
                              │
                              ▼ (no)
                  ┌─────────────────────────────┐
                  │ sprint-status.yaml: done?   │
                  └─────────────────────────────┘
                              │
                              ▼ (yes)
                       ACCEPT done (with note: jsonl+storyfile_missing)
                              │
                              ▼ (no)
                       MARK as incomplete → retry/escalate

git_reality_check (parallel, не блокирует):
   declared_files = parse_dev_agent_record(story_file)
   actual_files = git diff --name-only HEAD~N HEAD
   if declared != actual:
      events.jsonl ← {type: "story_file_mismatch", declared, actual}
      story → flag SUSPICIOUS (review_loop +1 cycle)
```

## 4. Изменения по файлам

| Файл | Что |
|---|---|
| `src/bmad_orchestrator/runtime/verdict_resolver.py` (новый) | `resolve_verdict(story, jsonl_path, story_file_path, sprint_status_path) -> Verdict` |
| `src/bmad_orchestrator/runtime/git_reality.py` (новый) | `check_file_list_match(story_id, declared, repo_path, base_ref) -> MatchResult` |
| `src/bmad_orchestrator/runtime/phase4_subscribers.py` | заменить прямое чтение jsonl на `resolve_verdict()`; вызывать `git_reality.check_file_list_match` после verdict |
| `src/bmad_orchestrator/runtime/verdict_fallback.py` | депрекейт старого fallback, оставить shim для миграции |
| `src/bmad_orchestrator/state/db.py` | новые EventType: `verdict_source_fallback`, `story_file_mismatch` |
| `src/bmad_orchestrator/runtime/worker_events.py` | поддержать новые event types |
| `tests/runtime/test_verdict_resolver.py` (новый) | 8+ unit тестов всех веток дерева |
| `tests/runtime/test_git_reality.py` (новый) | 4+ unit тестов |
| `tests/integration/test_dual_source_review.py` (новый) | end-to-end mock pilot |

## 5. Acceptance Criteria

- AC1: При corrupted/missing jsonl + valid story-file Status: verdict resolved как done, event `verdict_source_fallback{from: jsonl, to: story_file}` записан.
- AC2: При всех трёх источниках saying "in-progress": verdict = incomplete, retry triggered.
- AC3: При file_list mismatch: event `story_file_mismatch` записан, story flagged, review_loop увеличен на 1 цикл.
- AC4: Regress: NEW-9, NEW-21, NEW-26 кейсы воспроизводятся через unit тест → теперь проходят как done.
- AC5: Tests grow by ≥18; 0 regressions on existing suite (≥2272 PASS).
- AC6: Antares replay 1.5 → жёлтый/красный verdict выпадает на legitimate-incomplete, а не на runner-crashed.

## 6. Test Plan

| Тест | Сценарий |
|---|---|
| `test_resolver_jsonl_approve_wins` | happy path |
| `test_resolver_jsonl_missing_falls_to_storyfile` | NEW-21 reproduction |
| `test_resolver_jsonl_error_falls_to_storyfile` | NEW-9 reproduction |
| `test_resolver_runner_hang_storyfile_done` | NEW-26 reproduction |
| `test_resolver_all_sources_incomplete` | legitimate retry |
| `test_resolver_source_order_configurable` | policy override |
| `test_git_reality_match_clean` | happy path |
| `test_git_reality_declared_more_than_actual` | hallucinated file |
| `test_git_reality_actual_more_than_declared` | undocumented change |
| `test_git_reality_handles_renames` | git rename edge case |

## 7. Rollout

- Feature flag: `BMAD_DUAL_SOURCE_VERDICT=1` (default off → 50% pilots → on).
- Сравнение: первые 10 prod runs логируют verdict из обоих систем (old vs new) для anomaly detection.

## 8. Risks

| Риск | Митигация |
|---|---|
| story-file mark done но реально не done (LLM hallucinated) | git_reality_check + sprint-status как третий уровень |
| sprint-status.yaml не существует в target project | source-order skip missing files; работает с любым подмножеством |
| git_reality false positives на staged-but-not-committed | проверка только после worker commit step |

## 9. Effort

3-4 сессии: (1) resolver + tests, (2) git_reality + tests, (3) integration + phase4 subscriber wiring, (4) Antares replay + tuning.

## 10. Dependencies

- Зависит от: spec_competitor_quickwins (использует `log_pre_filter`).
- Блокирует: spec_verifier_contracts (там верификатор review_completion будет вызывать наш resolver).
