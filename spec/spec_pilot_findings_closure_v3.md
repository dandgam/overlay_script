# spec_pilot_findings_closure_v3 — Закрытие validation-replay findings (Antares 1a replay)

> **Owner:** user + Virgil orchestrator
> **Created:** 2026-05-19
> **Goal:** Закрыть 6 находок из validation-replay пилота Antares 1a (post-`f76816f`). Главный приоритет — **NEW-7**: восстановить verdict→integration pipeline, без которого production pilot бессмыслен (stories делаются, но никуда не интегрируются).
> **Source:** `spec/methodology-virgil.md` §5 — секция «Backlog — validation replay findings (Antares 1a replay 2026-05-19)».
> **Predecessor:** `spec_pilot_findings_closure_v2.md` (NEW-1..NEW-4, merged `f76816f`).
> **Runtime:** `/auto-loop-spec-long` — каждый wake = свежий headless `claude -p` процесс с пустым контекстом.
> **Scope decision:** все 6 находок (2× P1 + 4× P2). Без R3-R5 deferred research findings и без backlog cleanup — отдельные инициативы.

---

## 0. Context

### Inventory at start

- Branch: `main` @ `812457f`
- Tests: **~2009 PASS** (точное число — `python -m pytest --collect-only -q | tail -1` из venv).
- EventType count: **35** (`runtime/event_loop.py`).
- Pilot 1a replay verdict: `spawned=3 succeeded=2 failed=1`. NEW-2 ✅ validated (0 branch-delete halt). Story 1.5 — чистый успех, commit `85bb8a4` на `feature/1.5` — но `integration/wave-1a` **не создана**.
- Phase 4 #10 production pilot: ⬜ blocked by **NEW-7** (главный блокер).

### Что вошло в epic

| # | Tier | Item | Estimate | Source |
|---|---|---|---|---|
| 1 | **P1** | integration-ветка не создаётся даже на succeeded story (verdict→integration pipeline разорван) | ~1.5 сессии | NEW-7 |
| 2 | P1 | pilot body перечитывает env → worktrees в odyssey (NEW-1 fix неполный) | ~0.4 сессии | NEW-1-completion |
| 3 | P2 | `resolve_sprint_status_key` всё ещё не матчит 1.4/1.5 (NEW-3 fix неполный) | ~0.4 сессии | NEW-3-completion |
| 4 | P2 | `parse_inner_exit_code` regex не ловит формат `EXIT_CODE=N` | ~0.3 сессии | NEW-6 |
| 5 | P2 | dirty reused worktree → runner Stage 0 halt `working tree not clean` | ~0.5 сессии | NEW-5 |
| 6 | P2 | orchestrator висит ~13 мин после `real_pilot_done` (не выходит сам) | ~0.5 сессии | NEW-8 |

**Total:** ~3.5-4 сессии. Параллелить нельзя (NEW-7 + NEW-1/3/6 трогают `agent/run.py` и `runtime/`; merge-order конфликтов слишком много).

### Hard rules

- Branch isolation: вся работа в `integration/pilot-findings-closure-v3`. **Никогда** напрямую в `main`.
- Auto-merge **disabled** — финальный merge в main делает user руками.
- `--no-verify`, `--amend` после hook reject, `--force` — запрещены.
- Tests delta cap: ≥ +28 tests (см. per-item) → target ≥2037 PASS.
- mypy/ruff clean каждый commit.
- 1 item = 1 commit (внутри одной session допустимо 2 commit'а если найден refactor).
- Tracker: `.bmad-runs/pilot_findings_closure_v3/` с per-session updates + final report.

---

## 1. P1 — критический путь

### #1 (NEW-7) integration-ветка не создаётся на succeeded story — ГЛАВНЫЙ БЛОКЕР

**Зачем.** В replay-пилоте Story 1.5 — чистый успех (commit `85bb8a4` на `feature/1.5`), но `merge_to_integration_subscriber` не сработал, `integration/wave-1a` не создана. `merge_to_integration_subscriber` (`agent/run.py:3501`) подписан на `CODE_REVIEW_VERDICT` и мержит только при `verdict == "approve"`. Disconnect: succeeded story **не эмитит** `CODE_REVIEW_VERDICT` event → subscriber никогда не вызывается. S2 verdict-fallback (`runtime/verdict_fallback.py`, commit `4ac3e56`) должен был покрыть это, но не сработал в реальном сценарии (Stage 7 проходит, Stage-6 review log есть, но fallback reader не находит/не парсит его, либо событие эмитится после того как pilot loop уже завершился).

**Что добавляем (диагностика → fix, в таком порядке).**

**Шаг 1 — диагностика (обязателен перед кодом):**
- Прочитать `runtime/verdict_fallback.py` целиком + найти где он подписан/вызывается в `agent/run.py`.
- Прочитать `_run_real_pilot_body` (`agent/run.py:901`) — проследить: когда worker завершается `status=success`, какие события эмитятся и в каком порядке относительно завершения pilot loop.
- Зафиксировать root cause в tracker одним из:
  - **(a)** `CODE_REVIEW_VERDICT` не эмитится вообще для success-path (verdict-fallback не триггерится);
  - **(b)** эмитится, но **после** того как pilot loop завершился и bus уже не обрабатывает (race / shutdown order);
  - **(c)** эмитится, subscriber вызывается, но падает (missing `worktree`/`story_id` payload, ff-merge conflict).

**Шаг 2 — fix по root cause:**
- **Общий инвариант:** любая story с `worker_completed status=success` И ≥1 commit на `feature/<id>` поверх `base_sha` → ОБЯЗАНА получить `CODE_REVIEW_VERDICT` event до завершения pilot loop. Реализовать как явный **post-worker reconcile step** в `_run_real_pilot_body`: после finalize каждого worker'а — если success + commits есть + verdict event ещё не видели → emit synthetic `CODE_REVIEW_VERDICT verdict=approve source=success_path_reconcile`.
- **Drain перед shutdown:** pilot loop НЕ должен завершаться пока bus не обработал все pending события. Добавить explicit `await bus.drain()` (или эквивалент — проверить API event loop) перед `real_pilot_done`.
- **Subscriber robustness:** в `merge_to_integration_subscriber` — если `worktree` пустой, но `story_id` есть → резолвить worktree path из project registry (`<project>/.worktrees/wt-<id>`), не падать молча.

**Шаг 3 — observability:**
- Новый EventType **#36 `INTEGRATION_MERGE_SKIPPED`** — эмитить когда story success, но merge не произошёл, с `reason` (no_commits / verdict_missing / ff_conflict). Это превращает «тихую потерю работы» в видимый сигнал.

**Tests.** `tests/test_integration_merge_success_path.py`:
- 4 unit: success+commits → reconcile emit; success+no-commits → `INTEGRATION_MERGE_SKIPPED reason=no_commits`; verdict уже эмитился → no double-emit; subscriber резолвит worktree из registry когда payload пустой.
- 3 integration: full real-pilot mock, 1 success story → `integration/wave-1a` создана + ff-merge commit присутствует; drain ordering (verdict обработан до `real_pilot_done`); 2 stories (1 success / 1 halt) → только success merged.
- **Target:** +7 tests.

**Acceptance.**
- Real-pilot mock с одной succeeded story → `integration/<wave>` ветка существует, содержит ff-merged commit от `feature/<id>`.
- `events.jsonl` содержит `CODE_REVIEW_VERDICT` ДО `real_pilot_done` (порядок проверяется в integration-тесте).
- Story success без коммитов → `INTEGRATION_MERGE_SKIPPED reason=no_commits` (не тихо).

---

### #2 (NEW-1-completion) pilot body перечитывает env

**Зачем.** v2 (commits `64f07a9` + `0348b75`) починил CLI-резолвинг — `run_orchestrator` принимает pre-resolved `Settings` (`agent/run.py:254` — `settings = settings or load_settings()`). Но `_run_mock_pilot` (`agent/run.py:541`) и `_run_real_pilot_body` (`agent/run.py:933`) делают `settings = load_settings()` **заново, безусловно** → перечитывают `ORCHESTRATOR_TARGET_PROJECT` из `.env` → worktrees создаются в `/home/server/odyssey`. `--project` flag снова теряется.

**Что добавляем.**
- Прокинуть resolved `Settings` параметром в `_run_mock_pilot` и `_run_real_pilot_body` (и в `_run_real_pilot` на `:703`, если он тоже вызывает `load_settings()`).
- Убрать безусловные `load_settings()` в трёх местах (`:541`, `:703`, `:933`) — заменить на принятый параметр.
- Сигнатура: `settings: Settings` (required, не Optional — caller всегда имеет resolved settings из `run_orchestrator`).
- Audit: `grep -n "load_settings()" agent/run.py` → подтвердить что в pilot-call-chain не осталось перечитываний (callsites вне pilot-chain — `:334`, `:2691`, `:3358` — оставить, проверить контекст каждого).

**Tests.** `tests/test_pilot_body_uses_resolved_settings.py`:
- 3 unit: `_run_real_pilot_body` с переданными settings target=Antares + `ORCHESTRATOR_TARGET_PROJECT=odyssey` в env → используется Antares; `_run_mock_pilot` тот же случай; settings обязателен (TypeError если не передан).
- 1 integration: `run_orchestrator(settings=<antares>)` end-to-end mock → worktree path под `/home/server/Antares`, не odyssey.
- **Target:** +4 tests.

**Acceptance.**
- `ORCHESTRATOR_TARGET_PROJECT=/home/server/odyssey virgil run --project antares --wave 1a --real --dry-run` → все worktree-пути под `/home/server/Antares`, **ни одного** в odyssey.
- `grep "load_settings()" agent/run.py` в pilot-call-chain → 0 совпадений.

---

## 2. P2 — quality of life

### #3 (NEW-3-completion) resolver всё ещё не матчит 1.4/1.5

**Зачем.** v2 commit `729650f` дал `resolve_sprint_status_key` (`runtime/bmad_format.py:105`) детерминированный tie-break, но в replay `pilot_mark_done_unresolved` снова firing для 1.4/1.5 → sprint-status не обновляется → resume повторяет done-stories.

**Что добавляем.**
- **Диагностика первым делом:** прочитать `resolve_sprint_status_key` целиком + воспроизвести на реальных ключах из Antares sprint-status (`1-4-*`, `1-5-*`). Зафиксировать в tracker почему `1.4`/`1.5` не матчат — варианты: epic-block scoping (ключ ищется не в том epic-блоке), формат raw id (`1.4` vs `1-4` vs `Story 1.4`), suffix содержит цифры ломающие split-regex.
- **Fix по факту диагностики.** Не угадывать — расширить алгоритм ровно под найденный gap. Если regex `^(\d+-\d+)-(.+)$` — проверить что суффикс с цифрами (`1-4-startup-v2-checks`) парсится корректно.
- Сохранить детерминированный tie-break v2 (lexicographic + `sprint_status_key_ambiguous` warning).

**Tests.** `tests/test_sprint_status_key_replay_regression.py`:
- 3 unit: точные ключи из Antares 1a (`1-4-*`, `1-5-*`) → match; suffix с цифрами → match; epic-block scoping (ключ в другом эпике не матчится по ошибке).
- 1 integration: pilot mock с Antares-shaped sprint-status → mark-done срабатывает для 1.4 и 1.5.
- **Target:** +4 tests.

**Acceptance.**
- `pilot_mark_done_unresolved` НЕ firing на Antares 1a sprint-status для всех 1.3/1.4/1.5.
- Воспроизводящий тест с реальными ключами из replay → green.

---

### #4 (NEW-6) `parse_inner_exit_code` regex не ловит `EXIT_CODE=N`

**Зачем.** Runner выводит inner exit в ДВУХ форматах: `Exit code: N` и `EXIT_CODE=N`. `_INNER_EXIT_RE` (`runtime/worker_silent_failure.py:45`) = `^(?:❯\s*)?Exit code:\s*(\d+)$` — ловит только первый. В replay worker 1.4 вывел `EXIT_CODE=2`, regex промахнулся → `parse_inner_exit_code` вернул `None` → `worker_completed status=success` (неверно, NEW-4 fix обойдён).

**Что добавляем.**
- Расширить `_INNER_EXIT_RE` на оба формата — один regex с альтернацией: `^(?:❯\s*)?(?:Exit code:\s*|EXIT_CODE=)(\d+)$`.
- `parse_inner_exit_code` docstring (`:109`) — обновить описание форматов.
- Проверить runner-скрипт (`bmad-auto-dev-runner.sh`) — где он печатает `EXIT_CODE=` — убедиться что формат именно `EXIT_CODE=N` (без пробелов), иначе подправить regex.
- Если оба формата встречаются в одном stdout → вернуть **последний** (текущая семантика «last match», сохранить).

**Tests.** `tests/test_inner_exit_code_formats.py`:
- 4 unit: `Exit code: 2` → 2; `EXIT_CODE=2` → 2; `❯ Exit code: 1` → 1; оба формата в одном tail → последний; ни одного → None.
- **Target:** +4 tests (5 assertions, 4 кейса + None).

**Acceptance.**
- `parse_inner_exit_code(["EXIT_CODE=2"])` → `2`.
- Replay-сценарий 1.4: `EXIT_CODE=2` + outer 0 → `worker_completed status=failure inner_exit_code=2`.

---

### #5 (NEW-5) dirty reused worktree → Stage 0 halt

**Зачем.** Reused worktree с uncommitted изменениями (residue от прошлого aborted run'а) → runner Stage 0 halt `working tree not clean`. Story 1.3 упала на этом в replay. Warning `worktree_dirty_pre_spawn` уже эмитится — но **нет action**, worker всё равно спавнится и падает.

**Что добавляем.**
- `spawn_worker` (или pre-spawn hook в `worker_spawn.py`): при detected dirty worktree → перед spawn'ом выполнить cleanup в зависимости от политики:
  - **Default (`auto_clean_dirty_worktree=True`):** `git -C <wt> reset --hard <base_sha> && git -C <wt> clean -fd` — вернуть worktree к base. Это **destructive** — допустимо ТОЛЬКО для residue в managed `.worktrees/` под orchestrator-контролем (не user-репо). Лог `worktree_auto_cleaned story=<id> discarded_files=N`.
  - **Safe-режим (`auto_clean_dirty_worktree=False`):** не трогать, emit `WORKER_HALT_PRESPAWN reason=dirty_worktree` (как NEW-2 P2 #7 pre-flight halt) — worker не спавнится, story → halt с понятным reason.
- `Settings.auto_clean_dirty_worktree: bool = True` (новое поле, env `BMAD_AUTO_CLEAN_DIRTY_WORKTREE`).
- ⚠️ Cross-impact: `reset --hard` входит в hard-rule destructive ops. Здесь допустим т.к. (1) только managed `.worktrees/`, (2) дискардит лишь orchestrator-residue, (3) gated флагом. Зафиксировать обоснование комментарием у callsite.

**Tests.** `tests/test_dirty_worktree_prespawn.py`:
- 4 unit: dirty + auto_clean=True → reset+clean выполнены, worker спавнится; dirty + auto_clean=False → `WORKER_HALT_PRESPAWN reason=dirty_worktree`, no spawn; clean worktree → no-op в обоих режимах; лог формат.
- 1 integration: pilot mock с dirty reused worktree + auto_clean=True → story проходит Stage 0.
- **Target:** +5 tests.

**Acceptance.**
- Dirty reused worktree + default settings → auto-cleaned, runner Stage 0 проходит.
- `auto_clean_dirty_worktree=False` → story halt с `reason=dirty_worktree`, worktree нетронут (user может разобрать residue руками).

---

### #6 (NEW-8) orchestrator висит ~13 мин после `real_pilot_done`

**Зачем.** После `real_pilot_done` процесс не выходит сам ~13 мин. Вероятно orphan-cleanup hang или незавершённый `await` на subscriber/background task.

**Что добавляем.**
- **Диагностика:** прочитать shutdown-путь `run_orchestrator` после `real_pilot_done`. Найти что блокирует — кандидаты: незакрытый event-loop background task, `asyncio` task без cancel, worktree GC ожидающий subprocess, незакрытый file watcher на `events.jsonl`.
- **Fix:** явный shutdown-step — после `real_pilot_done`:
  - cancel всех pending background tasks (`asyncio.all_tasks()` минус current → `task.cancel()` + `gather(..., return_exceptions=True)`);
  - закрыть event bus / file watchers;
  - hard-timeout guard: `asyncio.wait_for(<shutdown>, timeout=30)` → если не уложились, log `orchestrator_shutdown_timeout` + forced exit.
- Новый лог `orchestrator_shutdown_complete elapsed_sec=N` для будущей регрессии.

**Tests.** `tests/test_orchestrator_clean_shutdown.py`:
- 3 unit: shutdown cancel'ит pending tasks; shutdown укладывается в timeout; зависший task → forced exit + log.
- 1 integration: full real-pilot mock → процесс/`run_orchestrator` корутина завершается в пределах ~разумного времени теста (<10s mock), no hang.
- **Target:** +4 tests.

**Acceptance.**
- Real-pilot mock → `run_orchestrator` возвращается сразу после `real_pilot_done` (no 13-min hang).
- `events.jsonl` / лог содержит `orchestrator_shutdown_complete`.

---

## 3. Session plan

| Session | Items | Estimated time | Tests delta | Commit count |
|---|---|---|---|---|
| S1 | #2 NEW-1-completion + #3 NEW-3-completion | ~1 сессия | +8 | 2 |
| S2 | #1 NEW-7 шаг 1-2 (диагностика + reconcile + drain) | ~1 сессия | +5 | 1-2 |
| S3 | #1 NEW-7 шаг 3 (`INTEGRATION_MERGE_SKIPPED` + subscriber robustness + tests finalize) + #4 NEW-6 | ~1 сессия | +6 | 2 |
| S4 | #5 NEW-5 + #6 NEW-8 + finalize | ~1 сессия | +9 | 2 |

**Total estimate:** 3.5-4 сессии. Tests delta: ≥ +28 → target ≥2037 PASS.

**Finalize check (end of S4):**
- `python -m pytest -q` → ≥2037 PASS, mypy/ruff clean.
- `git log integration/pilot-findings-closure-v3 --oneline` → 7-8 commits.
- EventType count → **36** (+1 `INTEGRATION_MERGE_SKIPPED`).
- Manual smoke: real-pilot mock с одной succeeded story → `integration/<wave>` ветка создана с ff-merged commit (NEW-7 acceptance — критический).
- Tracker `.bmad-runs/pilot_findings_closure_v3/` с per-session updates + final report.
- Обновить `spec/methodology-virgil.md` §5 — пометить NEW-1-completion/NEW-3-completion/NEW-5/NEW-6/NEW-7/NEW-8 как ✅ DONE (в commit'е финальной session).

**Post-merge (manual, вне auto-loop):**
- `git checkout main && git merge --no-ff integration/pilot-findings-closure-v3`.
- Повторный validation-replay Antares 1a — проверить `spawned=3 succeeded=3` И `integration/wave-1a` создана. Только после этого Phase 4 #10 production pilot разблокирован.

---

## 4. Out of scope (для следующей инициативы)

- **R3 per-turn token snapshot** (research backlog, P2).
- **R4 stale worktree GC** / **R5 fail-closed cleanup policy** (P2/P3) — пересекается с NEW-5, но GC — отдельный periodic mechanism.
- **P3 backlog-writer subscriber** (auto-capture архитектурных находок из events.jsonl).
- **Phase 5 items** — observability dashboard, TTS notifications, Vision steps 3-7.
- Полный production pilot на target проекте — после merge v3 + успешного replay.

---

## 5. References

- Methodology: `spec/methodology-virgil.md` §5 «Backlog — validation replay findings (Antares 1a replay 2026-05-19)»
- Predecessor: `spec/spec_pilot_findings_closure_v2.md` (NEW-1..NEW-4, merged `f76816f`)
- Memory:
  - `project_pilot_antares_1a_replay_2026-05-19` (replay findings, источник эпика)
  - `project_backlog_new1_incomplete` (NEW-1-completion)
- Code anchors:
  - `src/bmad_orchestrator/agent/run.py:3501` (`merge_to_integration_subscriber`)
  - `src/bmad_orchestrator/agent/run.py:3470` (`_ff_merge_to_integration`)
  - `src/bmad_orchestrator/agent/run.py:901` (`_run_real_pilot_body`), `:524` (`_run_mock_pilot`), `:254` (`run_orchestrator` settings entry)
  - `src/bmad_orchestrator/runtime/verdict_fallback.py` (S2 fallback, не сработал)
  - `src/bmad_orchestrator/runtime/worker_silent_failure.py:45` (`_INNER_EXIT_RE`), `:109` (`parse_inner_exit_code`)
  - `src/bmad_orchestrator/runtime/bmad_format.py:105` (`resolve_sprint_status_key`)
  - `src/bmad_orchestrator/runtime/worker_spawn.py` (`spawn_worker`, pre-spawn hooks)
  - `src/bmad_orchestrator/runtime/event_loop.py` (EventType enum, 35 → 36)
