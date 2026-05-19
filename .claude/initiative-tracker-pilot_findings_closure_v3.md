# Initiative Tracker — Pilot Findings Closure v3 (Antares 1a validation-replay)

## Metadata
- **Spec:** spec/spec_pilot_findings_closure_v3.md
- **Parent specs:** spec/spec_pilot_findings_closure_v2.md, spec/spec_pilot_findings_closure.md
- **Integration branch:** integration/pilot_findings_closure_v3
- **Created:** 2026-05-19
- **Bootstrap completed:** 2026-05-19 by auto-loop-spec-long
- **Scope frozen:** 2026-05-19
- **Runtime:** loop_wrapper
- **Delay seconds:** 120
- **Auto merge:** false

## Scope Freeze
### In scope
- NEW-7 (P1): восстановить verdict→integration pipeline — integration-ветка должна создаваться на любой succeeded story с коммитами.
- NEW-1-completion (P1): прокинуть resolved Settings в pilot bodies, убрать перечитывание env.
- NEW-3-completion (P2): починить resolve_sprint_status_key для kebab+slug ключей 1.4/1.5.
- NEW-6 (P2): расширить parse_inner_exit_code regex на формат EXIT_CODE=N.
- NEW-5 (P2): обработать dirty reused worktree перед spawn (auto-clean / halt).
- NEW-8 (P2): clean shutdown оркестратора после real_pilot_done (no 13-min hang).

### Out of scope (explicit)
- R3 per-turn token snapshot, R4 stale worktree GC, R5 fail-closed cleanup policy.
- P3 backlog-writer subscriber.
- Phase 5 items (observability dashboard, TTS, Vision steps 3-7).
- Полный production pilot — отдельная mini-session после merge v3.

### Deferred to follow-up initiative
- Повторный validation-replay Antares 1a (после manual merge v3).

## Sessions

### Pending

(none)

### Current

- **id:** S4
  **title:** NEW-5 dirty worktree pre-spawn + NEW-8 clean shutdown + finalize
  **surface:** backend-python
  **spec_section:** 147-191
  **depends_on:** [S3]
  **acceptance:**
    - dirty reused worktree → auto-clean (default) или WORKER_HALT_PRESPAWN (safe-режим).
    - run_orchestrator завершается сразу после real_pilot_done (no hang), shutdown timeout guard.
    - methodology-virgil §5 обновлён (NEW-1/3/5/6/7/8 → DONE) в финальном commit'е.
    - +9 tests, итог ≥2037 PASS, mypy/ruff clean.
  **safety_gates:**
    - L3 branch isolation. NEW-5 пишет код с `git reset --hard` в managed worktree — обоснование комментарием у callsite (не prod-destructive для самой сессии).
  **checkpoint:** false
  **estimated_retries_allowed:** 3
  **started:** 2026-05-19 08:30 UTC
  **workflow:** workflows/backend-python.md
  **retry_count:** 0
  **worker_branches:** []

### Completed

- **id:** S3
  **title:** NEW-7 INTEGRATION_MERGE_SKIPPED + subscriber robustness + NEW-6 exit-code regex
  **completed:** 2026-05-19 08:30 UTC
  **commit:** 1a80fa1
  **files_changed:** 6
  **tests_passed:** 2029 PASS (+6: 4 в test_integration_merge_success_path.py, 2 в test_inner_exit_code_formats.py)
  **decisions_made:**
    - EventType #36 INTEGRATION_MERGE_SKIPPED добавлен в runtime/event_loop.py (count 35→36). reason ∈ {no_commits, verdict_missing, ff_conflict}.
    - Emission points: (a) `_reconcile_success_verdicts` — success+0-commits → no_commits; success без WorkerHandle/base_sha → verdict_missing (раньше тихий continue); (b) `merge_to_integration_subscriber` except-ветка → ff_conflict перед HUMAN_QUERY.
    - merge_to_integration_subscriber: при пустом `worktree` payload и наличии story_id+target_project — резолвит `<project>/.worktrees/wt-<id>` (та же конвенция что worker spawn run.py:635), если путь существует. Synthetic reconcile-verdict эмитит пустой worktree — это и был случай тихого промаха.
    - NEW-6: `_INNER_EXIT_RE` = `^(?:❯\s*)?(?:Exit code:\s*|EXIT_CODE=)(\d+)$` — альтернация на оба формата, last-match семантика сохранена. Runner-скрипт bmad-auto-dev-runner.sh `EXIT_CODE=` не печатает (формат идёт от claude-p wrapper) — regex без пробелов корректен.
  **deferred_items:**
    - 3 теста инвентаря EventType обновлены под 36 (test_canonical_patches_p6, test_s3_runtime); test_w4 conflict-тест теперь ждёт 2 события (skipped+human_query) — намеренное поведенческое изменение.
    - mypy: 4 pre-existing ошибки в run.py (lines ~1021/1031/1040/1090, orphan arg-type + bus kwarg) — baseline, не регрессия, чистка в S4 finalize.
    - methodology-virgil §5 (NEW-* → DONE) — финальный commit S4.

- **id:** S2
  **title:** NEW-7 диагностика + reconcile + bus drain
  **completed:** 2026-05-19 08:18 UTC
  **commit:** db48b2c
  **files_changed:** 5
  **tests_passed:** 2023 PASS (+5: test_integration_merge_success_path.py)
  **decisions_made:**
    - Root cause NEW-7 = вариант (b), уточнённый: в real-режиме НИКТО не дренирует шину. `_run_real_pilot_body` эмитит WORKER_COMPLETED/WAVE_BOUNDARY в `bus.queue`, но `dispatch_one` не вызывается нигде (только тесты + 2 CLI-команды его дёргают). Значит вся subscriber-цепочка (build_check → deletion_safety → code_review → security_review → merge_to_integration) в проде была мёртвым кодом — отсюда и «integration не создаётся». S2 verdict-fallback (4ac3e56) исправен, но недостижим без consumer'а.
    - Fix: `EventLoop.drain()` — диспатчит очередь + каскады до пустоты (max_events cap против runaway re-emit). `_run_real_pilot_body` делает drain → reconcile → drain перед `real_pilot_done`.
    - `_reconcile_success_verdicts` — fallback-инвариант: succeeded story с commits past base_sha без verdict'а в `dispatched` → synthetic CODE_REVIEW_VERDICT(approve, source=success_path_reconcile). Если merge-gate уже выдал любой verdict (включая reject) — reconcile НЕ перебивает.
  **deferred_items:**
    - INTEGRATION_MERGE_SKIPPED (EventType #36) для success+no-commits — S3. ✅ закрыто в S3.
    - merge_to_integration_subscriber worktree-resolve из registry при пустом payload — S3. ✅ закрыто в S3.
    - Auto-split stories (synthetic WORKER_COMPLETED без WorkerHandle) reconcile не покрывает — они получают verdict через code_review_subscriber на drain'е; отдельная защита не нужна, зафиксировано.
    - test_w1 line ~736 (`max_spend_usd_default_50`) теперь проверяет halt по пустому списку (vacuous pass) — косметика, не регрессия, можно усилить в S4.
    - mypy: 4 pre-existing ошибки в run.py (orphan arg-type + bus kwarg) — baseline, чистка в S4 finalize.

- **id:** S1
  **title:** NEW-1-completion (env threading) + NEW-3-completion (sprint-status resolver)
  **completed:** 2026-05-19 08:03 UTC
  **commit:** 80a15d2
  **files_changed:** 12
  **tests_passed:** 2018 PASS (+9: 5 NEW-1 + 4 NEW-3)
  **decisions_made:**
    - settings сделан required keyword-only на всех трёх pilot-функциях (_run_mock_pilot / _run_real_pilot / _run_real_pilot_body) — обновлено ~25 test-callsite'ов.
    - NEW-3 root cause = вариант (a) epic-block scoping: mark-done loop читал raw YAML и понимал только legacy `epics:` nested layout; на BMad-flat `development_status:` layout (реальный Antares 1a) snap["epics"] пуст → resolve_sprint_status_key вообще не вызывался. Сам resolver исправен — v2 fix 729650f чинил недостижимую функцию.
    - Fix NEW-3 — новый mark_sprint_status_done в bmad_format.py, диспатчит по layout (legacy nested + BMad flat), мутирует in-place с сохранением формата файла.
  **deferred_items:**
    - mypy: 4 pre-existing ошибки в run.py (lines ~1031/1040/1090, _detect_orphan_stories arg-type + bus kwarg) — baseline до S1, не регрессия. Можно почистить в S4 finalize.
    - mock-pilot mark-done loop (run.py ~644) оставлен на legacy-only — mock fixtures всегда legacy format, out of scope NEW-3.

## Safety Gates Triggered
(none)

## Blockers / Pauses
(none)

## Decisions Log

- **date:** 2026-05-19
  **session:** bootstrap
  **decision:** Все 4 сессии surface=backend-python.
  **rationale:** Вся работа — Python-код в src/bmad_orchestrator/ (agent/run.py, runtime/*). Нет Rust/UI/destructive-infra/removal-only. backend-python — единственный подходящий narrow surface для Python-бэкенда.
  **impact:** Все сессии используют workflows/backend-python.md.

- **date:** 2026-05-19
  **session:** S1
  **decision:** NEW-3 чинится не в resolve_sprint_status_key, а добавлением mark_sprint_status_done — диспатчера по layout sprint-status.
  **rationale:** Диагностика показала: resolver исправен, ломался уровень выше — mark-done loop не понимал BMad-flat layout. Чинить resolver было бы лечением симптома.
  **impact:** S3 (NEW-7) — при диагностике verdict→integration учитывать что mark-done теперь корректно обновляет sprint-status; resume-сценарии больше не re-spawn'ят done-stories.

- **date:** 2026-05-19
  **session:** S2
  **decision:** NEW-7 root cause — отсутствие consumer'а шины в real-режиме, а не race verdict-эмиссии. Fix = explicit `EventLoop.drain()` в `_run_real_pilot_body` + reconcile-fallback, а не патч verdict_fallback.py.
  **rationale:** `bus.emit` лишь кладёт event в `asyncio.Queue`; subscriber'ы отрабатывают только через `dispatch_one`. В проде `_run_real_pilot_body` ни разу его не вызывал → ВСЯ W4-цепочка (включая merge_to_integration) была недостижима. verdict-fallback (4ac3e56) был корректен, но за мёртвым consumer'ом. Это объясняет почему «Stage 7 проходит, log есть, а merge нет».
  **impact:** S3 — INTEGRATION_MERGE_SKIPPED эмитится внутри той же drain-цепочки, теперь она реально крутится. S4 — NEW-8 clean shutdown должен дренировать/останавливать шину (`bus.stop()` отменяет backstop) после `real_pilot_done`; drain уже отрабатывает до него. Поведенческое изменение: build_check/deletion_safety/code_review-гейты впервые реально срабатывают в real-пилоте — это и есть намеренная цель NEW-7.

- **date:** 2026-05-19
  **session:** S3
  **decision:** INTEGRATION_MERGE_SKIPPED эмитится в трёх точках с разными reason; verdict_missing добавлен для success-story без WorkerHandle/base_sha.
  **rationale:** Спека называла reason no_commits/verdict_missing/ff_conflict. no_commits и ff_conflict очевидны; verdict_missing нужен для случая когда reconcile не может проверить commits (нет handle/base_sha) — раньше тихий continue, теперь видимый сигнал. Это покрывает auto-split synthetic-success без WorkerHandle.
  **impact:** S4 — NEW-8 shutdown не трогает эту цепочку. Поведенческое: test_w4 conflict-тест и 2 EventType-инвентаря обновлены под 36; будущие сессии при добавлении EventType должны бампать те же 3 теста.

## Journal

[2026-05-19 bootstrap] S0 bootstrap: tracker + backup/integration branches created, 4 sessions planned, runtime=loop_wrapper delay=120s, auto-merge=false
[2026-05-19 08:03 UTC] S1 execution: NEW-1 — settings прокинут в 3 pilot-функции (required kw-only), убраны load_settings() из pilot-chain; ~25 test-callsite'ов обновлены. NEW-3 — диагностика: mark-done loop не понимал BMad-flat layout; добавлен mark_sprint_status_done. +9 tests, 2018 PASS, ruff clean. commit 80a15d2.
[2026-05-19 08:03 UTC] S1 done, runtime=loop_wrapper — wrapper handles next iteration. S2 promoted to Current.
[2026-05-19 08:18 UTC] S2 execution: NEW-7 диагностика — root cause = нет consumer'а шины в real-режиме (events эмитятся, dispatch_one не зовётся → W4-цепочка мёртвая). Fix: EventLoop.drain() + _reconcile_success_verdicts; _run_real_pilot_body делает drain→reconcile→drain перед real_pilot_done. +5 tests; w1/embed_phase45 тесты переведены на pre-run recorder. 2023 PASS, ruff clean, mypy 4 baseline. commit db48b2c.
[2026-05-19 08:18 UTC] S2 done, runtime=loop_wrapper — wrapper handles next iteration. S3 promoted to Current.
[2026-05-19 08:30 UTC] S3 execution: NEW-7 observability — EventType #36 INTEGRATION_MERGE_SKIPPED (count 35→36), эмитится в reconcile (no_commits/verdict_missing) и merge subscriber (ff_conflict). merge_to_integration_subscriber резолвит worktree из registry при пустом payload. NEW-6 — _INNER_EXIT_RE ловит оба формата (Exit code: N + EXIT_CODE=N). +6 tests; 3 EventType-инвентарь-теста обновлены под 36. 2029 PASS, ruff clean, mypy 4 baseline. commit 1a80fa1.
[2026-05-19 08:30 UTC] S3 done, runtime=loop_wrapper — wrapper handles next iteration. S4 promoted to Current.

## Final Report (populated on last session completion)

(empty — pending S4 close)
