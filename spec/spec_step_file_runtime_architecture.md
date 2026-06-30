# Spec — Step-file Runtime Architecture (рефакторинг agent/run.py)

**Версия:** 0.1.0
**Дата:** 2026-05-20
**Автор:** AABIT
**Статус:** ready for /auto-loop-spec-long (LAST — после всех verifier и review specs)
**Источник:** bmad-automator `skills/bmad-story-automator/steps-c|steps-v|steps-e/` + `workflow.md`
**Размер:** LONG (10-15 сессий)
**Приоритет:** P2 — strategic maintainability, не блокирует MVP

---

## 1. Executive Summary

`src/bmad_orchestrator/agent/run.py` — **5226 строк** в одном файле. Это самая большая единица кода в репозитории. Все шаги выполнения (create / dev / review / commit / merge / retro / wrapup) живут в одном файле как inline методы, переплетённые с LLM-prompt construction, error handling, state updates.

Симптомы:
- Изменение «правила запрета chain'а шагов» — это правка LLM-промпта внутри огромного метода. LLM иногда «забывает». Феномен зафиксирован в feedback memory.
- Регрессии: добавление одного шага = риск сломать соседние (NEW-7 был такого рода).
- Чтение/code-review — невозможно за разумное время.

Конкурент решает это через **step-file architecture**: workflow.md как router → грузит `steps-c/01-init.md`, `02-preflight.md`, `03-execute.md`, ... строго по одному, и **запрещено** держать в контексте более одного step-file. Каждый step-file самодостаточный markdown с инструкциями + ссылками на data/scripts.

Рефакторинг даёт: maintainability + чёткие defence-against-LLM-shortcuts + понятные точки изменения за минуту.

---

## 2. Goals / Non-Goals

### Goals
- G1: разбить `agent/run.py` на step-files под `agent/steps/` (markdown инструкции) + thin Python runner.
- G2: ввести `agent/runner.py` ≤500 строк, который читает текущий step-file, делает один шаг, обновляет state, грузит следующий.
- G3: каждый step-file ≤200 строк markdown.
- G4: запрет load multiple step files: enforce в `runner.py` через assert + audit event.
- G5: миграция без breaking changes — старый agent/run.py заменяется поэтапно (feature flag).

### Non-Goals
- НЕ меняем поведение системы — только структура кода.
- НЕ переписываем skills (skills отдельны).
- НЕ меняем event-loop / phase4_subscribers.

---

## 3. Архитектура

```
agent/
  runner.py                ≤500 строк — thin orchestrator (load step → execute → save state → next)
  steps/
    workflow.md             router (mode → first step)
    steps-c/                create flow
      step-01-init.md
      step-02-preflight.md
      step-03-execute.md
      step-04-wrapup.md
    steps-v/                validate (опц., если уже сделан spec_operator_first_class_modes)
    steps-e/                edit
    steps-r/                resume
  data/
    execution-patterns.md
    forbidden-patterns.md
    monitoring-pattern.md
    code-review-loop.md
    retry-fallback.md
```

```python
# agent/runner.py (skeleton)
class StepFileRunner:
    MAX_LOADED_STEPS = 1  # enforce

    def __init__(self, state, project_root):
        self.state = state
        self.project_root = project_root
        self._loaded_step: Path | None = None

    def run(self, entry: str = "workflow.md"):
        step_path = self._resolve(entry)
        while step_path:
            self._enforce_single_load(step_path)
            instructions = self._read_step(step_path)
            result = self._execute_step(instructions)
            self._save_state(result)
            step_path = self._next_step(instructions, result)
        self._wrapup()

    def _enforce_single_load(self, path: Path):
        if self._loaded_step is not None and self._loaded_step != path:
            self.state.event("step_swap", from_=str(self._loaded_step), to=str(path))
        self._loaded_step = path
```

## 4. Изменения по файлам

| Файл | Что |
|---|---|
| `src/bmad_orchestrator/agent/run.py` | LEGACY: обернуть в `legacy_run()`, оставить для feature-flag |
| `src/bmad_orchestrator/agent/runner.py` (новый) | thin orchestrator |
| `src/bmad_orchestrator/agent/steps/workflow.md` (новый) | router |
| `src/bmad_orchestrator/agent/steps/steps-c/*.md` (новые) | 4-6 файлов create flow |
| `src/bmad_orchestrator/agent/steps/steps-r/*.md` (новые) | resume |
| `src/bmad_orchestrator/agent/data/*.md` (новые) | shared rules |
| `src/bmad_orchestrator/cli/main.py` | switch via flag `BMAD_STEP_FILE_RUNNER=1` |
| `tests/agent/test_runner.py` (новый) | 12+ |
| `tests/agent/test_step_files.py` (новый) | 8+ (markdown structure invariants) |

## 5. Acceptance Criteria

- AC1: `agent/runner.py` ≤500 строк, `agent/run.py` legacy остаётся за flag.
- AC2: Каждый step-file ≤200 строк, имеет frontmatter с `name / nextStep / dataFileIndex`.
- AC3: Runner запрещает загружать >1 step-file одновременно (test).
- AC4: Полная parity с legacy на golden pilot (Antares 1.5): same events, same final state.
- AC5: Latency overhead per step ≤50ms (чтение markdown файла).
- AC6: Tests grow ≥20.

## 6. Test Plan

| Тест | Сценарий |
|---|---|
| `test_runner_loads_workflow_md_first` | entry |
| `test_runner_routes_create_mode_to_step01` | router |
| `test_runner_routes_resume_mode_to_resume_steps` | router |
| `test_runner_single_step_loaded_at_a_time` | invariant |
| `test_step_files_have_valid_frontmatter` | structure |
| `test_step_files_max_lines_200` | structure |
| `test_runner_parity_with_legacy_on_mock_pilot` | regression |
| `test_runner_swap_step_emits_audit_event` | telemetry |

## 7. Rollout

- Feature flag: `BMAD_STEP_FILE_RUNNER=1` (default off, 6 месяцев).
- Параллельный прогон: каждый pilot гонит оба runner'а в shadow mode, сравнение state.
- Cleanup legacy `agent/run.py` после 10 успешных prod runs на новом runner'е.

## 8. Risks

| Риск | Митигация |
|---|---|
| Параллельная поддержка двух runner'ов = двойная нагрузка | feature flag + shadow mode сравнение; cleanup жёстко зашит |
| LLM игнорит «single step» инструкцию | runner.py enforce'ит — не доверяемся промпту |
| Step-files расходятся с кодом runner'а | контрактные тесты на frontmatter + assertion `step_file.next ∈ existing_files` |

## 9. Effort

12-15 сессий, разбито:
- (1-2) runner skeleton + workflow.md routing
- (3-6) разбор agent/run.py на 4-6 step-files
- (7-8) data/*.md shared rules
- (9-10) tests
- (11-12) shadow mode + parity validation
- (13-15) cleanup + rollout

## 10. Dependencies

- Зависит от: ВСЕ предыдущие spec'и (этот трогает каркас).
- Может стартовать раньше только в части skeleton (runner + workflow.md) — без миграции step-files.
- Блокирует: ничего, но улучшает все будущие фичи.
