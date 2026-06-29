# Spec — Operator First-class Modes (Validate / Edit / Resume)

**Версия:** 0.1.0
**Дата:** 2026-05-20
**Автор:** AABIT
**Статус:** ready for /auto-loop-spec-long
**Источник:** bmad-automator `steps-v/` (Validate), `steps-e/` (Edit), `steps-c/step-01b-continue.md` (Resume)
**Размер:** MEDIUM-LONG (4-6 сессий)
**Приоритет:** P1 — operator UX, debug speed

---

## 1. Executive Summary

Сейчас debugging и recovery оркестратора — это **ручной SQL по `state/db.py`** или попытка прочитать events.jsonl глазами. Resume есть, но только программный (`replay.py`), без меню «view / modify / start-over / abort». Validate отсутствует.

У конкурента три отдельных first-class CLI mode'а с интерактивным menu:

1. **Validate** — структурная проверка state-doc (frontmatter ok, status enums valid, session refs vs live processes, per-story progress consistency, stalled combinations).
2. **Edit** — модификация state без ручного SQL: статус, story range, overrides, custom instructions, AI command.
3. **Resume** — step-aware восстановление с меню `Resume / View / Modify / StartOver / Abort`.

Перенести как полноценные CLI команды Virgil. Это сокращает время debugging пилота с 30+ мин до 1-2 мин.

---

## 2. Goals / Non-Goals

### Goals
- G1: CLI команда `virgil validate <run-id|--latest>` — выводит structured report (severity buckets: ok / warning / error).
- G2: CLI команда `virgil edit <run-id>` — TUI menu для модификации state без SQL.
- G3: CLI команда `virgil resume [<run-id>|--latest-incomplete]` — расширенное меню.
- G4: TUI на `typer + rich` (стек уже в проекте).
- G5: Validate отчёт записывается в `_bmad-output/virgil/validate-reports/<run-id>-<ts>.md`.

### Non-Goals
- НЕ переписываем `state/db.py` схему.
- НЕ заменяем events.jsonl на markdown state-doc (у нас SQLite, у них markdown — оставляем как есть).
- НЕ делаем web UI (только CLI/TUI).

---

## 3. Архитектура

### Validate

```
virgil validate <run-id>
  ├─ Structure checks
  │    ├─ run row exists, status is valid enum
  │    ├─ all stories have phase set
  │    └─ no orphan events
  ├─ Session checks
  │    ├─ worktree paths still exist
  │    ├─ no dangling worker processes (pid alive but no state row)
  │    └─ branches exist for active stories
  ├─ Progress checks
  │    ├─ stories in 'in_progress' have recent heartbeat (<10min)
  │    ├─ failed stories have failure_reason set
  │    └─ no impossible state combinations (e.g., status=merged but no commit_sha)
  └─ Report severity:
       OK  / WARNING / ERROR
       → write markdown report
       → exit code 0/1/2
```

### Edit

```
virgil edit <run-id>
  ├─ load state from DB
  ├─ TUI menu:
  │    [S] status         (paused / running / aborted)
  │    [R] story range    (current/total)
  │    [O] overrides      (max_parallel, skip_security_review, ...)
  │    [C] custom instructions
  │    [J] judge config   (model, timeout)
  │    [Q] save & quit
  ├─ atomic update via single transaction
  └─ resume? [Yes/No]
```

### Resume (расширенный)

```
virgil resume [--latest-incomplete]
  ├─ find run
  ├─ verify policy snapshot hash (from spec_policy_snapshot_marker)
  ├─ verify marker (cleanup if stale)
  ├─ show summary: epic, current story, last action, last event
  ├─ menu:
  │    [R] Resume from saved step
  │    [V] View action log (tail 50)
  │    [M] Modify (-> edit mode)
  │    [O] Start Over (backup state, restart preflight)
  │    [A] Abort (mark run as aborted, exit)
  └─ on R: re-create marker, route to saved step
```

## 4. Изменения по файлам

| Файл | Что |
|---|---|
| `src/bmad_orchestrator/cli/commands/validate.py` (новый) | команда + validators |
| `src/bmad_orchestrator/cli/commands/edit.py` (новый) | TUI menu |
| `src/bmad_orchestrator/cli/commands/resume.py` (новый) | резюм с меню |
| `src/bmad_orchestrator/state/validators.py` (новый) | structural/session/progress checks |
| `src/bmad_orchestrator/cli/main.py` | регистрация новых команд |
| `src/bmad_orchestrator/cli/tui.py` | расширить с edit menu (если ещё нет) |
| `tests/cli/test_validate_command.py` (новый) | 6+ тестов |
| `tests/cli/test_edit_command.py` (новый) | 4+ тестов |
| `tests/cli/test_resume_command.py` (новый) | 5+ тестов |

## 5. Acceptance Criteria

- AC1: `virgil validate <run-id>` выдаёт report c severity и пишет MD-файл; exit 0/1/2.
- AC2: Validate ловит минимум 5 типов проблем (stale heartbeat, missing worktree, orphan event, status mismatch, impossible combo).
- AC3: `virgil edit` позволяет менять статус/диапазон/overrides без прямого SQL; изменения атомарны.
- AC4: `virgil resume --latest-incomplete` находит самый свежий не-COMPLETE run и предлагает меню.
- AC5: Resume интегрирован с policy snapshot verify и marker cleanup.
- AC6: Tests grow ≥15.

## 6. Test Plan

| Тест | Сценарий |
|---|---|
| `test_validate_clean_run_returns_ok` | happy path |
| `test_validate_detects_stale_heartbeat` | warning |
| `test_validate_detects_missing_worktree` | error |
| `test_validate_detects_orphan_event` | warning |
| `test_validate_writes_markdown_report` | side effect |
| `test_validate_exit_code_matches_severity` | CLI contract |
| `test_edit_updates_status_atomically` | DB integrity |
| `test_edit_invalid_status_rejected` | validation |
| `test_resume_finds_latest_incomplete` | logic |
| `test_resume_with_stale_marker_cleans_up` | integration |
| `test_resume_aborts_run_on_menu_choice` | TUI |
| `test_resume_routes_to_saved_step` | logic |

## 7. Rollout

- Без feature flag — новые команды, ничего не ломают.
- Документация: README.md + spec/methodology-virgil.md обновить.

## 8. Risks

| Риск | Митигация |
|---|---|
| TUI ломается на не-tty окружении | детектим `sys.stdout.isatty()`, fallback на non-interactive prompts |
| Edit повреждает БД | все изменения в одной транзакции с rollback |
| Validate генерирует false-positive warnings | severity бакеты, плюс `--strict` flag для CI |

## 9. Effort

5-6 сессий: (1-2) validate + validators, (3) edit + TUI, (4) resume rebuild, (5) integration tests, (6) docs + smoke.

## 10. Dependencies

- Зависит от: spec_policy_snapshot_marker.
- Блокирует: ничего (но улучшает все будущие пилоты).
