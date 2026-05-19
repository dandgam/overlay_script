# spec_pilot_findings_closure_v6 — replay-режим + commit/review/metrics gates

> **Owner:** user + Virgil orchestrator
> **Created:** 2026-05-19
> **Goal:** Закрыть NEW-14..18 (pilot run #5 findings) + добавить NEW-19 (replay-from-worktree) — режим валидации фиксов БЕЗ перезапуска дорогого worker-dev. После закрытия — один replay на одной истории, ожидаем `story_merged` для 1.4.
> **Source:** `spec/methodology-virgil.md §5` — backlog «pilot run #5 findings». Pilot run #5 (Antares 1a, 2026-05-19): `succeeded=1 failed=1`, фактически 0 stories merged, NEW-12 регрессировал.

---

## 0. Context

### Inventory at start

- Branch: `main` @ `dbeb936` (после v5 merge) + methodology §5 sync.
- Tests: **2078 PASS**, mypy/ruff clean (3 pre-existing mypy errors out of scope).
- EventType: ~37 (`SECURITY_REVIEW_ERROR` последний) — bootstrap уточнит по `runtime/event_loop.py`.
- Antares worktrees `wt-1.4` (dev-коммиты `9cd3fd8`+`6175c50`) / `wt-1.5` (uncommitted) —
  оставлены для диагностики, можно использовать как fixture.

### Что валидировано pilot run #5 (НЕ трогаем)

- **NEW-11** — `build_check_skip_no_ruff_config` работает: ruff не халтит config-less worktree.
- **NEW-7/8/9** — pipeline замкнут (validated на run #4, story 1.3 в `integration/1a`).

### Корневая проблема процесса

v5 закрыл NEW-12, тесты прошли — но тест проверял **не тот code path** (env в воркер, баг в
recovery path). Баг проехал в прод, ловился дорогим pilot-прогоном (~30 мин worker-dev на
историю). **Правила v6:**

1. Каждый фикс → unit-тест, воспроизводящий **именно тот code path**, где баг.
2. Pilot replay — один раз в конце, на одной истории (1.4). Не после каждого фикса.
3. NEW-19 (replay-from-worktree) делается **первым** — дальше валидация идёт за секунды.

---

## 1. NEW-19 (P1) — replay-from-worktree режим

**Зачем.** Validation фиксов merge-gate/stage5/metrics не требует worker-dev (самая дорогая
фаза). Нужен режим: взять worktree с готовым dev-коммитом и прогнать **только** хвост
pipeline (stage5 commit → build-check → merge-gate → reconcile → merge).

**Что делаем.**

- Новый CLI-флаг `bmad-orchestrator replay --worktree <path> --story <id> --integration <branch>`
  (или `run --replay-worktree`). Скипает spawn worker'а, берёт существующий worktree как есть.
- Внутри — переиспользовать post-dev часть pipeline: detect dev-коммит(ы) в worktree →
  stage5 → build-check → merge-gate → verdict → reconcile → merge. Без `spawn_worker`.
- Если worktree dirty (uncommitted, как `wt-1.5`) — флаг `--auto-commit-dev` синтезирует
  dev-коммит из рабочих изменений (для воспроизведения post-dev пути).
- Лог `replay_mode_active worktree=... story=...` + EventType `REPLAY_MODE_STARTED`.

**Tests.** `tests/test_new19_replay_worktree.py`:
- 3 unit: CLI парсинг флага, skip-spawn путь, detect dev-коммитов в worktree.
- 2 integration: mock worktree с готовым коммитом → replay прогоняет stage5→merge-gate без
  spawn; dirty worktree + `--auto-commit-dev` → синтез-коммит.
- **Target:** +5 tests

**Acceptance.** `replay --worktree wt-1.4 --story 1.4` прогоняет хвост pipeline за секунды,
ноль вызовов worker-dev.

---

## 2. NEW-14 (P1) — NEW-12 регрессия: pre-commit env в stage5 recovery path

**Симптом.** `stage5_recovery_failed: No .pre-commit-config.yaml file` на 1.4 — снова, после
v5. v5-фикс прокинул `PRE_COMMIT_ALLOW_NO_CONFIG=1` в env *воркера* (`worker_spawn.py`), но
stage5 recovery вызывает `git commit` отдельным subprocess без проброшенного env.

**Что делаем.**

- Найти все `git commit` вызовы в stage5 recovery path (runner Stage 5 / recovery helper).
- Инжектить `PRE_COMMIT_ALLOW_NO_CONFIG=1` в env **каждого** такого subprocess — либо через
  общий helper `_git_commit_env()`, либо глобально в subprocess env runner'а.
- Не дублировать логику: вынести env-prep в одну функцию, использовать и в worker_spawn, и
  в recovery.

**Tests.** `tests/test_new14_precommit_recovery_env.py`:
- 2 unit: recovery `git commit` subprocess получает `PRE_COMMIT_ALLOW_NO_CONFIG=1`;
  helper `_git_commit_env()` возвращает флаг.
- 1 integration: mock worktree без `.pre-commit-config.yaml` → stage5 recovery commit
  **успешен**, не `stage5_recovery_failed`.
- **Target:** +3 tests. Тест ОБЯЗАН падать на текущем `main` (воспроизводит регрессию).

**Acceptance.** stage5 recovery коммитит в config-less worktree без ошибки.

---

## 3. NEW-15 (P1) — code_review verdict=error блокирует merge gate

**Симптом.** `code_review_dispatched review_jsonl= verdict=error` на 1.4: пустой review_jsonl
→ verdict=error → оба merge-gate stage (spec/quality) fail. Аналог NEW-13, но для
**code_review** — v5 покрыл только security_review. Под-баг: `code_review_runner_log_fallback
fallback_verdict=approve` отработал, но `merge_gate_quality_stage_done` всё равно `verdict=error`.

**Что делаем.**

- Применить паттерн NEW-13 к code_review: `verdict=error` (technical failure) → retry
  (`error_retry_max`, default 1) → при исчерпании одиночная HUMAN_QUERY escalate-story, НЕ
  circuit-breaker increment. Переиспользовать механику из `security_review_subscriber`.
- Под-баг (b): когда `code_review_runner_log_fallback` даёт `fallback_verdict`, этот verdict
  должен **применяться** к итоговому merge-gate verdict, а не отбрасываться. Найти где
  merge_gate stage берёт verdict и почему fallback не доезжает.
- Диагностировать пустой `review_jsonl=` — почему runner не записал review-лог (путь? race?).

**Tests.** `tests/test_new15_code_review_error.py`:
- 3 unit: code_review `verdict=error` → retry; исчерпание retry → escalate-story не abort;
  `fallback_verdict` применяется к merge-gate verdict.
- 2 integration: mock code_review error → retry path; fallback approve → merge-gate
  получает approve.
- **Target:** +5 tests

**Acceptance.** code_review `verdict=error` не валит merge-gate безусловно; fallback-verdict
доезжает до итогового решения.

---

## 4. NEW-16 (P1) — ложный `succeeded` для story без merge

**Симптом.** Story 1.5 написала код, не закоммитила, не дошла до merge gate, нет
`story_merged` — но засчитана `succeeded=1`. Метрика `succeeded` врёт.

**Что делаем.**

- Найти где считается `succeeded` в `_run_real_pilot_body` / pilot summary.
- `succeeded` обязан требовать `story_merged` (story реально в integration) — либо, как
  минимум, непустой dev-коммит на feature-ветке + approve-verdict. Story без коммита и без
  merge = `failed` (или новый статус `no_op`), не `succeeded`.
- Согласовать с NEW-9 (verdict source-of-truth) — не сломать валидный путь run #4.

**Tests.** `tests/test_new16_succeeded_requires_merge.py`:
- 3 unit: story без `story_merged` → не `succeeded`; story с merge → `succeeded`;
  story с коммитом но без merge → `failed`/`no_op` по политике.
- 1 integration: pilot summary на mock-сценарии 1 merged + 1 no-commit → `succeeded=1
  failed=1` (не `succeeded=2`).
- **Target:** +4 tests

**Acceptance.** `succeeded` отражает только реально смерженные истории.

---

## 5. NEW-17 (P2) — 1.5 worker завершился молча без коммита

**Симптом.** Для 1.5 нет `pilot_mark_done`/`stage5`/`merge_gate` — только
`cost_tracking_unavailable`. Worker написал файлы и вышел, не закоммитив, без single
`stage5_recovery_failed`. Silent failure.

**Что делаем.**

- Диагностировать через `wt-1.5` + events.jsonl: worker упал? timeout? вышел с пустым
  результатом? Почему нет stage5 вообще.
- Вероятно тот же pre-commit блок (NEW-14), но silent — нужен loud audit-warning когда
  worker завершился с uncommitted changes и без stage5: EventType
  `WORKER_EXIT_UNCOMMITTED` + emit в completion-detector.
- Переиспользовать `runtime/worker_silent_failure.py` (NEW-2 Layer B) — расширить детектор.

**Tests.** `tests/test_new17_worker_uncommitted_exit.py`:
- 2 unit: детектор находит uncommitted-changes + no-stage5 → emit `WORKER_EXIT_UNCOMMITTED`.
- 1 integration: mock worker exit с dirty worktree → событие в шине.
- **Target:** +3 tests

**Acceptance.** Worker, завершившийся без коммита, даёт громкое событие, не тишину.

---

## 6. NEW-18 (P2) — `bmad_format.unknown_status` ×4

**Симптом.** Парсер статусов историй печатает голый `bmad_format.unknown_status` в stdout
(не структурный лог) 4 раза за прогон — не распознаёт Status-поле части историй.

**Что делаем.**

- Найти в `bmad_format.py` где печатается `unknown_status` (вероятно raw `print`).
- Заменить на structured warning: `bmad_format_unknown_status story_id=... raw_status=...
  layout=...` — видно какие статусы не парсятся и в каком layout.
- Проверить покрывает ли парсер все BMad-варианты Status (`done`/`Done`/`ready-for-dev`/
  `in-progress`/`drafted` и т.п.); добавить недостающие.

**Tests.** `tests/test_new18_unknown_status.py`:
- 2 unit: structured warning вместо raw print; парсер распознаёт расширенный набор статусов.
- **Target:** +2 tests

**Acceptance.** Нераспознанный статус → structured warning с `story_id` + `raw_status`.

---

## 7. Session breakdown (для /auto-loop-spec-long bootstrap)

Предполагаемая раскладка (bootstrap уточнит):

| Session | Items | Прим. |
|---|---|---|
| S1 | NEW-19 (replay-режим) | разблокирует дешёвую валидацию |
| S2 | NEW-14 + NEW-15 | commit/review gates, оба P1 |
| S3 | NEW-16 + NEW-17 + NEW-18 | metrics + silent failure + parser |

**Integration branch:** `integration/pilot_findings_closure_v6` (auto_merge=false, ручной
merge как v5).

## 8. Acceptance (инициатива в целом)

- NEW-14..18 закрыты, каждый — с тестом, воспроизводящим конкретный code path.
- NEW-19 — `replay`-режим работает, валидация без worker-dev.
- Все тесты PASS (2078 → ~2100+), mypy/ruff clean на changed.
- **Финальная валидация:** один `replay --worktree wt-1.4 --story 1.4` → `story_merged` в
  `integration/1a`. Ноль worker-dev перезапусков.

---

**Last updated:** 2026-05-19 (v1.0 — READY for `/auto-loop-spec-long` bootstrap; 6 items,
3 сессии, ~+22 tests)
