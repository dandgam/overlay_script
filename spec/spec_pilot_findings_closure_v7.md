# spec_pilot_findings_closure_v7 — review-runner root-fix + security_review fallback symmetry

> **Owner:** user + Virgil orchestrator
> **Created:** 2026-05-19
> **Goal:** Закрыть NEW-20/21/22 (pilot run #6 findings, Antares 1.5). Корень — **NEW-21**: review runner систематически возвращает `verdict=error` (пустой review_jsonl) во всех прогонах #4/#5/#6 → NEW-13/15/20 чинят симптом. После NEW-21 ревью даёт реальные verdict'ы, fallback'и становятся страховкой, а не основным путём.
> **Source:** `spec/methodology-virgil.md §5` — backlog «pilot run #6 findings»; memory `project_pilot_antares_1.5_run6_2026-05-19`.
> **Scale:** M — 3 items, ~5 файлов, ~2 сессии, +~14 tests.
> **Тип:** bugfix (2×P1 🐛 + 1×P2 🐛).

---

## 0. Context

### Inventory at start

- Branch: `main` @ `55825cb` (после v6 merge) + methodology §5 sync.
- Tests: **2102 PASS**, mypy/ruff clean (3 pre-existing mypy errors out of scope).
- EventType: ~41 (`WORKER_EXIT_UNCOMMITTED` #41 последний) — bootstrap уточнит по `runtime/event_loop.py`.
- Antares worktrees `wt-1.4` / `wt-1.5` — доступны как fixture для replay-валидации (NEW-19 режим работает).
- Логи прогона #6: `.claude/virgil-pilot-1.5-2218.log`, `.claude/virgil-replay-1.4-2208.log`, `.claude/virgil-replay-1.5-2216.log` — источник симптомов.

### Что валидировано pilot run #6 (НЕ трогаем)

- **NEW-14/15/16/19** — validated вживую (см. methodology §5). stage5 recovery commit, code_review error fallback, честная `succeeded`-метрика, replay-режим.
- Pipeline замкнут (NEW-7) — verdict→reconcile→merge работает на реальном verdict.

### Корневая проблема (почему v7)

`review_jsonl=` пустой во ВСЕХ событиях code_review/security_review всех прогонов (#4/#5/replay/1.5).
Серия фиксов NEW-13 (security_review error→escalate) / NEW-15 (code_review error→fallback) /
NEW-20 (этот spec) — все обрабатывают **симптом** «verdict=error». Никто не закрыл вопрос
**почему reviewer не отрабатывает**. Пока корень открыт — каждый merge идёт по fallback-пути,
а не по реальному ревью → качество merge-gate не гарантировано → Phase 4 Deploy заблокирован.

### Правила v7 (наследуют v6)

1. Каждый фикс → unit-тест, воспроизводящий **именно тот code path**, где баг.
2. NEW-21 — **диагностика first**: Session 1 начинается с spike, фикс прескриптивно не задан
   до подтверждения гипотезы (см. §1).
3. Финальная валидация — один replay на одной истории (1.5 или 1.4), без worker-dev.

---

## 1. NEW-21 (P1, КОРЕНЬ) — review runner возвращает verdict=error (пустой review_jsonl)

### Симптом

`code_review_dispatched review_jsonl= verdict=error` и `security_review_dispatched verdict=error`
во всех прогонах. `tail_jsonl_events(handle.jsonl_path)` не находит ни одного verdict-события →
verdict остаётся初始 `"error"`.

### Что уже известно из code research

1. **Observability-баг (подтверждён).** `_MergeGateStageResult` (`agent/run.py:~3697`) **не
   содержит** поля `jsonl_path` / `quality_handle`. Quality-стейдж (`_run_merge_gate_quality_stage`,
   `~3646`) получает `WorkerHandle` с `.jsonl_path`, но возвращает только `(verdict, summary,
   metrics)` — handle отбрасывается. В `_code_review_subscriber` (`~3934`) `handle_jsonl_str`
   захардкожен пустой строкой → лог `review_jsonl=` пустой всегда (`~4077`).
   ⚠️ Это **только логирование** — verdict-чтение на `~3658` использует `handle.jsonl_path`
   напрямую, корректный путь. Пустой лог ≠ причина verdict=error.

2. **Открытый вопрос (требует spike).** Почему `tail_jsonl_events(handle.jsonl_path)` не даёт
   verdict-событие. Гипотезы:
   - **H1 — path mismatch.** Reviewer-pivot ставит `BMAD_CURRENT_WAVE=<wave>__review_<story_id>`
     (`run.py:~3444`); `worker_jsonl_path()` (`agent/tools/_common.py:318`) деривит путь из
     этого env. Reviewer-subprocess пишет в worktree-internal `events.jsonl`, а orchestrator
     читает `runs_dir/<wave>__review_.../<wt>.events.jsonl` — пути расходятся (ср. NEW-10
     `merge_worktree_events` для dev-воркера; для review-воркера merge может не вызываться).
   - **H2 — reviewer не эмитит verdict-событие.** `claude -p /bmad-code-review` (Opus) выдаёт
     prose-ревью, но не пишет structured JSONL verdict-line. Тогда JSONL воркера есть, но в нём
     нет события с verdict-полем.
   - **H3 — права/CWD.** Reviewer CWD=worktree, файл JSONL в `runs_dir` (вне worktree); при
     sandbox-изоляции запись наружу заблокирована.

### Session 1 — диагностический spike (обязательно ПЕРВЫМ)

1. Прогнать `code_review` на готовом worktree (`wt-1.4`, dev-коммит уже есть) через replay
   или изолированно. Проверить **на диске**: существует ли `handle.jsonl_path`, пустой ли он,
   что внутри (`worker_jsonl_path()` для review-pivot env).
2. Сверить путь, который пишет reviewer-subprocess, с путём, который читает
   `_run_merge_gate_quality_stage` на `~3658`. Подтвердить/опровергнуть H1.
3. Если файл есть и непустой, но verdict-события нет — подтвердить H2: что именно reviewer
   пишет в JSONL, есть ли там `verdict`-ключ.
4. Зафиксировать root cause в spec-приложении / commit message ДО написания фикса.

### Fix (scope зависит от spike — наиболее вероятный путь H1/H2)

- **Если H1 (path mismatch):** применить паттерн NEW-10 `merge_worktree_events` к
  review-воркеру — мержить worktree-internal `events.jsonl` в orchestrator-видимый путь
  ПЕРЕД `tail_jsonl_events`; либо унифицировать `worker_jsonl_path()` чтобы reviewer писал
  туда, откуда orchestrator читает.
- **Если H2 (reviewer не пишет verdict):** review-skill (`/bmad-code-review`,
  `/bmad-security-review`) должен эмитить structured verdict-событие в JSONL — добавить шаг
  в SKILL.md ревью-скила ИЛИ orchestrator-side парсить prose-вывод reviewer'а в verdict
  (расширить `verdict_fallback.parse_runner_review_log` на reviewer stdout).
- **Если H3 (права):** writeable-путь JSONL внутри worktree-границы sandbox.
- **Observability-фикс (делаем независимо от гипотезы):** добавить `jsonl_path: str` в
  `_MergeGateStageResult`, захватить в `_run_merge_gate_quality_stage` return, populate
  `handle_jsonl_str` на `~3934` — чтобы `review_jsonl=` в логах был непустой и будущая
  диагностика была видимой.

**Tests.** `tests/test_new21_review_jsonl.py`:
- 2 unit: `_MergeGateStageResult` несёт `jsonl_path`; `handle_jsonl_str` populated непустой.
- 2 unit: фикс корневой причины (зависит от гипотезы — напр. review-events merge, или
  verdict-парсинг prose-вывода) — тест воспроизводит пустой-JSONL сценарий и проверяет
  что verdict извлекается.
- 1 integration: mock review-воркер пишет JSONL → orchestrator читает verdict (не `error`).
- **Target:** +5 tests. Корневой тест ОБЯЗАН падать на `main` (воспроизводит пустой review_jsonl).

**Acceptance.** На replay `wt-1.4`/`wt-1.5` событие `code_review_dispatched` имеет непустой
`review_jsonl=` и `verdict ∈ {approve, request_changes, reject}` — НЕ `error` при технически
исправном reviewer.

---

## 2. NEW-20 (P1) — security_review verdict=error без fallback (асимметрия с code_review)

### Симптом

`security_review_dispatched verdict=error trigger=keyword` → `supervisor_escalated HUMAN_QUERY`
×2 → `failed=1`, story не смержена. code_review при `verdict=error` получает fallback→approve
(NEW-15), security_review — только escalate-story (NEW-13), без fallback.

### Что известно из code research

- `security_review_subscriber` (`runtime/security_review.py:321–499`): retry до
  `error_retry_max` → эмит `SECURITY_REVIEW_ERROR` (#37) per attempt → при исчерпании
  мутирует `CODE_REVIEW_VERDICT` payload в `verdict='reject'` + `HUMAN_QUERY`
  (`verdict=security_review_error`). **Нет** чтения runner Stage 6 log.
- `code_review_subscriber` (`agent/run.py:3843–3899`): retry → `CODE_REVIEW_ERROR` (#39) →
  **fallback** через `parse_runner_review_log()` (`verdict_fallback.py`) читает
  `<worktree>/_bmad/auto-dev-state/reviews/<story_id>*.log` → восстанавливает
  `PASS|NEEDS-FIX|BLOCKED` → `approve|request_changes|reject`. Не валит merge безусловно.
- `SupervisorEngine._is_security_review_error` (`supervisor/engine.py:201–220`) уже
  распознаёт оба типа — circuit breaker не инкрементится. Этот слой трогать не нужно.

### Что делаем — симметрия с NEW-15

- Добавить security_review **fallback-путь**: при `verdict=error` после исчерпания retry —
  прочитать вывод security-review runner'а (его Stage-6 / final-finding log, аналог
  `_bmad/auto-dev-state/reviews/`) и восстановить holistic verdict
  `approve|merge_with_fixes|block`. Только при отсутствии fallback-сигнала — escalate-story
  (одиночная HUMAN_QUERY, как сейчас), НЕ безусловный `reject`.
- Если у security-review runner нет аналога Stage-6 log (research: «runs its own parallel
  `/bmad-security-review --auto` skill») — fallback читает JSONL/stdout reviewer'а тем же
  механизмом, что NEW-21 даст для code_review. **Зависимость: NEW-21 делается первым** —
  его verdict-extraction переиспользуется здесь.
- Вынести общий fallback-helper (`parse_review_runner_output`) — переиспользуют и code_review,
  и security_review. Не дублировать `verdict_fallback`-логику.

**Tests.** `tests/test_new20_security_review_fallback.py`:
- 3 unit: security_review `verdict=error` + есть runner-log → fallback-verdict применён;
  `verdict=error` + нет сигнала → escalate-story (не abort, не безусловный reject);
  общий helper `parse_review_runner_output` возвращает verdict из обоих форматов.
- 2 integration: mock security_review error + runner-log approve → merge не блокируется;
  mock без сигнала → одиночная HUMAN_QUERY.
- **Target:** +5 tests.

**Acceptance.** security_review `verdict=error` ведёт себя симметрично code_review:
технический сбой раннера → fallback, не безусловная блокировка merge.

---

## 3. NEW-22 (P2) — `bmad_format_unknown_status` голый stdout

### Симптом

Лог печатает `bmad_format_unknown_status` без `story_id`/`raw_status` (строки 8/9/15/16 лога #6).

### Что известно из code research — гипотеза методички ОПРОВЕРГНУТА

- **Единственный callsite:** `runtime/bmad_format.py:246–254`, функция `_canonical_status`.
  Версия «4 вызова из top-level scan, не из `_canonical_status`» (methodology §5) — **неверна**.
- Код уже structured: `logger.warning("bmad_format_unknown_status", extra={"story_id":...,
  "raw_status":..., "token":..., "layout":...})`.
- **Реальная причина:** message-строка = голый ключ `"bmad_format_unknown_status"`; поля в
  `extra={}` не попадают в rendered output, если форматтер логгера их не разворачивает.
  «4 раза» — это 4 истории с нераспознанным статусом за прогон, один и тот же callsite.

### Что делаем

- Привести `_canonical_status` warning к стилю остальных structured-логов проекта: поля —
  **в самой message-строке** (f-string/`%`-format), а не только в `extra`. Свериться с тем,
  как логируются другие события (напр. `build_check_halt`, `stage5_recovery_commit`) —
  взять тот же паттерн (вероятно `f"bmad_format_unknown_status story_id={...} raw_status={...}
  layout={...}"`).
- Удалить ложную гипотезу из methodology §5 при закрытии (см. §6 acceptance).
- Бонус (дёшево): свериться что `KNOWN_STATUSES` (`bmad_format.py:65–78`,
  `{done, in-progress, ready-for-dev, backlog, review, deferred, optional, drafted, approved}`)
  покрывает статусы Antares-историй, из-за которых триггерился warning — добавить недостающие,
  если diagnose покажет легитимный статус.

**Tests.** `tests/test_new22_unknown_status_log.py`:
- 2 unit: warning-сообщение содержит `story_id` + `raw_status` + `layout` **в тексте**
  сообщения (не только в `extra`); легитимный статус из лога #6 распознаётся (если такой есть).
- **Target:** +2 tests.

**Acceptance.** Нераспознанный статус → одна строка лога с `story_id`/`raw_status`/`layout`
видимыми без зависимости от формата логгера.

---

## 4. Session breakdown (для /auto-loop-spec-short bootstrap)

`/auto-loop-spec-short` — SHORT preset (1-3 сессии, schedule_wakeup runtime, контекст
накапливается). Предполагаемая раскладка (bootstrap уточнит):

| Session | Items | Прим. |
|---|---|---|
| S1 | NEW-21 (spike + fix) | корневой; диагностика first; разблокирует NEW-20 |
| S2 | NEW-20 + NEW-22 | NEW-20 переиспользует verdict-extraction из NEW-21; NEW-22 мелкий |

**Integration branch:** `integration/pilot_findings_closure_v7` (auto_merge=false, ручной
merge как v5/v6).

---

## 5. Acceptance (инициатива в целом)

- NEW-20/21/22 закрыты, каждый — с тестом, воспроизводящим конкретный code path.
- **NEW-21 root cause задокументирован** (commit message + spec-приложение) — не «обработали
  error», а «reviewer теперь даёт реальный verdict».
- Все тесты PASS (2102 → ~2116+), mypy/ruff clean на changed.
- **Финальная валидация:** один `replay --worktree wt-1.5 --story 1.5` (или 1.4) →
  `code_review_dispatched` с непустым `review_jsonl=` и реальным verdict; при approve →
  `story_merged` в `integration/1a`. Ноль worker-dev перезапусков.
- После закрытия — Phase 4 Deploy gate разблокирован для 5-field deploy-elicitation
  (888-persona-ops).

---

## 6. Post-close методология

При закрытии обновить `spec/methodology-virgil.md §5`:
- NEW-20/21/22 → ✅ DONE с commit-хэшами.
- Исправить ложную гипотезу NEW-22 («4 вызова из top-level scan» → «один callsite,
  extra-поля не рендерились»).
- Если NEW-21 root делает NEW-13/15/20 страховкой — отметить это явно.

---

**Last updated:** 2026-05-19 (v1.0 — READY for `/auto-loop-spec-short` bootstrap; 3 items
(2×P1 + 1×P2), 2 сессии, ~+14 tests; NEW-21 — диагностика-first spike)
