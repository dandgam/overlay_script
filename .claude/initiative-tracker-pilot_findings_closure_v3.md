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

- **id:** S3
  **title:** NEW-7 INTEGRATION_MERGE_SKIPPED + subscriber robustness + NEW-6 exit-code regex
  **surface:** backend-python
  **spec_section:** 50-83, 127-146
  **depends_on:** [S2]
  **acceptance:**
    - EventType #36 INTEGRATION_MERGE_SKIPPED добавлен (count 35→36).
    - merge_to_integration_subscriber резолвит worktree из registry при пустом payload.
    - _INNER_EXIT_RE ловит и `Exit code: N`, и `EXIT_CODE=N`.
    - +6 tests, mypy/ruff clean.
  **safety_gates:**
    - L3 branch isolation.
  **checkpoint:** false
  **estimated_retries_allowed:** 3

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

### Current

- **id:** S2
  **title:** NEW-7 диагностика + reconcile + bus drain
  **surface:** backend-python
  **spec_section:** 50-83
  **depends_on:** [S1]
  **acceptance:**
    - Root cause verdict→integration disconnect зафиксирован в tracker (вариант a/b/c).
    - post-worker reconcile step эмитит synthetic CODE_REVIEW_VERDICT для success+commits story.
    - explicit bus drain перед real_pilot_done.
    - +5 tests, mypy/ruff clean.
  **safety_gates:**
    - L3 branch isolation — работа только на integration/pilot_findings_closure_v3.
  **checkpoint:** false
  **estimated_retries_allowed:** 3
  **started:** (set on first execution wake)
  **workflow:** workflows/backend-python.md
  **retry_count:** 0
  **worker_branches:** []

### Completed

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

## Journal

[2026-05-19 bootstrap] S0 bootstrap: tracker + backup/integration branches created, 4 sessions planned, runtime=loop_wrapper delay=120s, auto-merge=false
[2026-05-19 08:03 UTC] S1 execution: NEW-1 — settings прокинут в 3 pilot-функции (required kw-only), убраны load_settings() из pilot-chain; ~25 test-callsite'ов обновлены. NEW-3 — диагностика: mark-done loop не понимал BMad-flat layout; добавлен mark_sprint_status_done. +9 tests, 2018 PASS, ruff clean. commit 80a15d2.
[2026-05-19 08:03 UTC] S1 done, runtime=loop_wrapper — wrapper handles next iteration. S2 promoted to Current.

## Final Report (populated on last session completion)

(empty — pending S4 close)
