# Spec — Competitor Quickwins (token economy + safety net)

**Версия:** 0.1.0
**Дата:** 2026-05-20
**Автор:** AABIT
**Статус:** ready for /auto-loop-spec-short
**Источник:** анализ bmad-code-org/bmad-automator vs Virgil (см. чат 2026-05-20)
**Размер:** SHORT (1-3 сессии)
**Приоритет:** P0 — закрывает feedback_llm_worker_overthinks_skills + NEW-11/NEW-12 регрессии

---

## 1. Executive Summary

Перенять три быстрых паттерна у конкурента, которые дают **немедленную экономию токенов и защиту от регрессий** без архитектурного рефакторинга:

1. **Log pre-filter** — перед LLM-парсингом грепаем `SUCCESS|FAIL|ERROR|CRITICAL|WARN|RETRY|ESCALATE` + `tail -120`. Экономия токенов 5-10× на парсинге worker-output.
2. **Slim SKILL.md** — для outer-orchestrator skills body ≤10 строк + steps-* / REFERENCE.md JIT loadable. Уменьшает «worker over-thinks длинные SKILL.md» (известный pain — см. feedback memory).
3. **CLI contract check** — на старте каждого execute step проверяем `--help` ключевых CLI команд. Fail-fast при interface drift (вместо тихих регрессий уровня NEW-11/NEW-12).

---

## 2. Goals / Non-Goals

### Goals
- G1: ввести функцию `log_pre_filter(log_path, max_lines=120)` и обязательно прогонять через неё все логи worker'ов перед передачей в LLM-парсер.
- G2: пересмотреть top-5 SKILL.md по размеру в outer-orchestrator слое (агенты), сократить body до ≤10 строк, переместить детали в `REFERENCE.md` / `steps-*.md`.
- G3: добавить `cli_contract_check()` в начало `worker_spawn` и `review_runner`: проверить, что нужные флаги CLI-команд (claude/codex/git/ruff/pre-commit) ещё существуют. При drift — halt с понятным сообщением.

### Non-Goals
- НЕ переделываем `agent/run.py` целиком (это spec_step_file_runtime_architecture).
- НЕ внедряем per-step verifier по контракту (это spec_verifier_contracts).
- НЕ трогаем review pipeline по сути (это spec_dual_source_verdict + spec_adversarial_review_bundled).

---

## 3. Изменения по файлам

| Файл | Что меняется |
|---|---|
| `src/bmad_orchestrator/runtime/log_filter.py` (новый) | функция `pre_filter(log_path, focus_patterns, max_lines=120)`, тесты в `tests/runtime/test_log_filter.py` |
| `src/bmad_orchestrator/runtime/worker_events.py` | вызвать `pre_filter` перед extract/parse |
| `src/bmad_orchestrator/runtime/security_review.py` | то же для review log |
| `src/bmad_orchestrator/runtime/cli_contract.py` (новый) | функция `check_contract(commands: dict[str, list[str]])` |
| `src/bmad_orchestrator/runtime/worker_spawn.py` | вызвать `check_contract` в начале `spawn_worker_subprocess` |
| `src/bmad_orchestrator/agent/skills/<orchestrator-outer>/SKILL.md` | trim до ≤10 строк, детали в `REFERENCE.md` |
| `tests/runtime/test_log_filter.py` (новый) | unit тесты |
| `tests/runtime/test_cli_contract.py` (новый) | unit тесты + integration (mock CLI с подменённым `--help`) |

## 4. Acceptance Criteria

- AC1: `pre_filter` принимает любой текстовый лог worker'а и возвращает ≤120 строк, отдавая приоритет matched-паттернам.
- AC2: При прогоне Antares 1.5 replay в test mode — LLM-парсер получает ≤120 строк (метрика `events.jsonl` field `parser_input_lines`).
- AC3: Минимум 3 outer-orchestrator skills имеют SKILL.md ≤10 строк body (frontmatter не считается).
- AC4: `cli_contract_check` ловит хотя бы один известный случай (regress test: `ruff check --output-format=text` ушёл → check fail).
- AC5: tests/ растут минимум на 15 новых тестов; existing tests не падают (≥2272 PASS).
- AC6: На smoke pilot — total tokens на 1 story снижаются ≥30% по сравнению с baseline (метрика `cost_tracker`).

## 5. Test Plan

| Тест | Тип |
|---|---|
| `test_pre_filter_grep_first_then_tail` | unit |
| `test_pre_filter_no_matches_returns_tail` | unit |
| `test_pre_filter_max_lines_respected` | unit |
| `test_cli_contract_passes_when_flags_present` | unit |
| `test_cli_contract_fails_on_missing_flag` | unit (mock subprocess) |
| `test_worker_spawn_calls_contract_check` | integration |
| `test_security_review_uses_pre_filter` | integration |
| smoke pilot replay на Antares 1.5 (1 story) | manual gate |

## 6. Rollout

- Feature flag: `BMAD_PRE_FILTER=1` (default on after AC pass), `BMAD_CLI_CONTRACT=1` (default on).
- Откат: env flags off → старое поведение.

## 7. Risks

| Риск | Митигация |
|---|---|
| `pre_filter` режет важный stack trace | `focus_patterns` включает `Traceback\|Exception\|panic`, плюс всегда tail -120 |
| `cli_contract_check` ломает прод при минорном CLI update | проверки через `subprocess --help`, не парсим версию, только наличие флага |
| SKILL.md slim ломает существующие skills | trimming только в outer-orchestrator уровне, inner stages не трогаем |

## 8. Effort

3 сессии. Phase 1 = log_filter (1с). Phase 2 = cli_contract (1с). Phase 3 = SKILL.md trim + smoke pilot (1с).

## 9. Dependencies

- Никаких внешних. Может стартовать сразу.
- Блокирует: spec_dual_source_verdict (там используем log_pre_filter).
