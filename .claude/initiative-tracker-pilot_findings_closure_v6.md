# Initiative Tracker — Pilot Findings Closure v6 (replay-режим + commit/review/metrics gates)

## Metadata
- **Spec:** spec/spec_pilot_findings_closure_v6.md
- **Integration branch:** integration/pilot_findings_closure_v6
- **Created:** 2026-05-19
- **Bootstrap completed:** 2026-05-19 by /auto-loop-spec-long
- **Scope frozen:** 2026-05-19
- **Runtime:** loop_wrapper
- **Delay seconds:** 120
- **Auto merge:** false

## Scope Freeze

### In scope
- NEW-19 (P1): replay-from-worktree режим — CLI `replay --worktree <path> --story <id> --integration <branch>`, прогон хвоста pipeline (stage5→build-check→merge-gate→reconcile→merge) без spawn_worker. EventType `REPLAY_MODE_STARTED`.
- NEW-14 (P1): NEW-12 регрессия — `PRE_COMMIT_ALLOW_NO_CONFIG=1` в env всех `git commit` вызовов stage5 recovery path; общий helper `_git_commit_env()`.
- NEW-15 (P1): code_review `verdict=error` → retry + escalate-story (паттерн NEW-13); fallback_verdict из `code_review_runner_log_fallback` доезжает до итогового merge-gate verdict.
- NEW-16 (P1): `succeeded` метрика требует `story_merged` (реальный merge в integration); story без коммита/merge = `failed`/`no_op`.
- NEW-17 (P2): worker exit с uncommitted changes без stage5 → loud audit-warning, EventType `WORKER_EXIT_UNCOMMITTED`.
- NEW-18 (P2): `bmad_format.unknown_status` raw print → structured warning `bmad_format_unknown_status story_id=... raw_status=... layout=...`; расширить набор распознаваемых статусов.
- methodology-virgil.md §5 — пометить NEW-14/15/16/17/18/19 DONE.
- Tests target ≥2100 PASS (2078 → ~+22), mypy/ruff clean на changed files.

### Out of scope (explicit)
- P3 backlog-writer subscriber (мета-фича) — отдельная инициатива.
- Полный 3/3-story production pilot replay — после merge v6 (финальная валидация = один replay 1.4).
- 3 pre-existing mypy errors (sandbox.py / phase4_subscribers.py / main_merge_token.py) — не из v6.

### Deferred to follow-up initiative
- Production pilot Antares 1a полный прогон (1.3/1.4/1.5) — после merge v6.

## Sessions

### Pending
(none)

### Current
(none — initiative complete)

### Completed

- **id:** S3
  **title:** NEW-16 succeeded-метрика + NEW-17 silent worker exit + NEW-18 status parser
  **surface:** backend-python
  **spec_section:** §4 (NEW-16), §5 (NEW-17), §6 (NEW-18)
  **completed:** 2026-05-19
  **commit:** 2970769
  **result:** NEW-16 — новый модуль `runtime/pilot_outcomes.py` (`partition_pilot_outcomes` + `PilotOutcomes`) пересчитывает worker-completed список по реальным merge-событиям: EventType #40 `INTEGRATION_MERGE_COMPLETED` (эмитит `merge_to_integration_subscriber` после ff-merge) → `merged`/`succeeded`; `INTEGRATION_MERGE_SKIPPED` reason=`no_commits` → `no_op`; иначе или нет merge-события → `failed`. `_run_real_pilot_body` зовёт partition после финального drain; `real_pilot_done` репортит честный `succeeded` + `worker_succeeded`/`no_op`/`failed`. NEW-17 — EventType #41 `WORKER_EXIT_UNCOMMITTED`; на zero-commit silent-failure пути `_tail_and_emit_completion` проверяет dirty worktree (`_worktree_has_uncommitted_changes` git porcelain) + stage5-маркер из stdout через `worker_silent_failure.decide_uncommitted_exit`; loud event при «файлы написаны, не закоммичены, stage5 не было». NEW-18 — `_canonical_status` получил `layout`-параметр, эмитит structured warning `bmad_format_unknown_status` (`story_id`/`raw_status`/`layout`), матчинг case-insensitive (`Done`→`done`), `KNOWN_STATUSES` +`drafted`/`approved`. +10 tests PASS (NEW-16 ×5, NEW-17 ×3, NEW-18 ×2). Обновлены 3 существующих теста (event inventory ×2 → 41, test_b1 case-insensitive, w4 merge subscriber ×2 ожидают INTEGRATION_MERGE_COMPLETED). Suite 2092→2102, mypy/ruff clean на changed files. methodology §5 NEW-16/17/18 → DONE.

- **id:** S2
  **title:** NEW-14 pre-commit env в recovery path + NEW-15 code_review error handling
  **surface:** backend-python
  **spec_section:** §2 (NEW-14), §3 (NEW-15)
  **completed:** 2026-05-19
  **commit:** 7c554e3
  **result:** NEW-14 — общий helper `_git_commit_env()` + константа `GIT_COMMIT_ENV_INJECTED` в новом модуле `runtime/git_env.py`; `env=_git_commit_env()` проброшен в `git commit` subprocess обоих recovery-путей (`stage5_completeness.py` Patch S + `commit_recovery.py` Patch R); `worker_spawn.WORKER_ENV_INJECTED` переиспользует константу. NEW-15 — `code_review_subscriber` рефакторен: helper `_run_merge_gate_two_stage` + retry-loop на `verdict=error` (`CODE_REVIEW_ERROR_RETRY_MAX`, default 1), EventType #39 `CODE_REVIEW_ERROR` per attempt, при исчерпании — одиночная HUMAN_QUERY `verdict=code_review_error` (не CODE_REVIEW_VERDICT(error) → не кормит circuit breaker); `SupervisorEngine._is_security_review_error` распознаёт code_review-маркеры; под-баг (b) — runner-log fallback verdict доезжает до итогового verdict при quality stage=error. +9 tests PASS (NEW-14 ×4, NEW-15 ×5; интеграционные NEW-14 падают на main — воспроизводят регрессию). Suite 2083→2092, mypy/ruff clean на changed files. methodology §5 NEW-14/15 → DONE.

- **id:** S1
  **title:** NEW-19 replay-from-worktree режим
  **surface:** backend-python
  **spec_section:** §1
  **completed:** 2026-05-19
  **commit:** 0772517
  **result:** CLI `replay --worktree/--story/--integration [--auto-commit-dev]`; модуль `runtime/replay.py` (git-хелперы + prepare_replay_worktree) + `agent.run.run_replay` (хвост pipeline через общий `_wire_pipeline_subscribers`, переиспользуется `_run_real_pilot_body`). EventType #38 `REPLAY_MODE_STARTED`, лог `replay_mode_active`. +5 tests PASS (3 unit: CLI parse / skip-spawn / detect-commits; 2 integration: ready worktree merge / dirty+auto-commit-dev synth). Suite 2078→2083, mypy/ruff clean на changed files.

## Safety Gates Triggered
(none)

## Blockers / Pauses

- **date:** 2026-05-19 UTC
  **session:** S3
  **type:** manual_merge_pending
  **detail:** initiative complete on integration/pilot_findings_closure_v6 (S1+S2+S3, commits 0772517 / 7c554e3 / 2970769). Auto merge=false — user must merge manually:
    git checkout main && git merge --no-ff integration/pilot_findings_closure_v6 -m "merge pilot_findings_closure_v6 S1..S3"
  **resolution:** PENDING (user action)

## Decisions Log

- **date:** 2026-05-19 (bootstrap)
  **session:** —
  **decision:** Surface всех 3 сессий = backend-python; delay=120s (как v5, серия pilot_findings_closure).
  **rationale:** Весь объём — Python в `src/bmad_orchestrator/` (runtime/, cli/, eval/, agent/, bmad_format.py) + pytest. Единственная narrow-поверхность на сессию → не mixed. Delay 120s для консистентности с v5 (сессии 25-45 мин, не token-heavy).
  **impact:** Каждая сессия делегирует workflow backend-python. Delay переопределяется ручной правкой `.claude/scripts/auto-loop-pilot_findings_closure_v6.sh` или re-bootstrap.

- **date:** 2026-05-19 (S1)
  **session:** S1
  **decision:** Subscriber-wiring блок (configure_code_review_gate + 13 bus.on) вынесен из `_run_real_pilot_body` в module-level `_wire_pipeline_subscribers(bus, settings, wave)`.
  **rationale:** spec §1 «не дублировать логику» — replay должен переиспользовать тот же gate-chain, не копировать. Блок самодостаточен (зависит только от bus/settings/wave).
  **impact:** `_run_real_pilot_body` и `run_replay` регистрируют идентичные subscriber'ы. `run_replay` принимает `wire_subscribers=False` для тестов со stub-цепочкой.

- **date:** 2026-05-19 (S2)
  **session:** S2
  **decision:** `code_review_subscriber` рефакторен — двух-стадийный merge-gate вынесен в helper `_run_merge_gate_two_stage`, обёрнут в retry-loop; на персистентной ошибке эмитится HUMAN_QUERY, а НЕ CODE_REVIEW_VERDICT(error).
  **rationale:** spec §3 — `verdict=error` это technical failure, не story-дефект; CODE_REVIEW_VERDICT(error) кормил бы circuit breaker как story-эскалацию. Существующий тест `test_w4_code_review_spawn_failure_emits_error_verdict` обновлён под новое поведение (cross-impact).
  **impact:** S3 (NEW-16 succeeded-метрика) — story с `verdict=code_review_error` теперь не получает CODE_REVIEW_VERDICT; метрика `succeeded`/`no_op` должна корректно учитывать отсутствие verdict (story не merged).

- **date:** 2026-05-19 (S3)
  **session:** S3
  **decision:** NEW-16 — `succeeded` пересчитывается из событий, а не из worker-exit-кода. Введён положительный сигнал `INTEGRATION_MERGE_COMPLETED` (раньше успешный merge был только log.info `story_merged`). `partition_pilot_outcomes` — чистая функция в отдельном модуле `runtime/pilot_outcomes.py`. NEW-17 — детектор `decide_uncommitted_exit` встроен в существующую zero-commit silent-failure ветку `_tail_and_emit_completion` (не в конец генератора — `tail_jsonl_events` никогда не завершается без terminal event, т.к. `_wait_and_finalize` всегда дописывает `worker_completed`).
  **rationale:** spec §4 «согласовать с NEW-9» — worker-exit `status=success` доказывает только наличие dev-коммита на feature-ветке, не merge. Только событие реального ff-merge — честный `succeeded`. NEW-18 case-insensitive матчинг сломал бы старый тест `test_parse_unknown_status` (`DONE` считался unknown) — тест обновлён (cross-impact).
  **impact:** Финальная инициатива. `real_pilot_done` теперь репортит `succeeded`/`worker_succeeded`/`no_op`/`failed` раздельно — downstream-парсеры логов могут грепать новые ключи. `mark_sprint_status_done` по-прежнему флипает worker-completed (NEW-3-completion) — НЕ изменено (out of scope, отдельный concern от метрики).

## Journal

[2026-05-19 UTC] bootstrap: tracker created via /auto-loop-spec-long, 3 sessions planned, S1 promoted to Current. Slug=pilot_findings_closure_v6, runtime=loop_wrapper, delay=120s, auto_merge=false. Backup branch backup/pilot_findings_closure_v6-pre-2026-05-19, integration branch integration/pilot_findings_closure_v6.

[2026-05-19 UTC] S1 done, runtime=loop_wrapper — wrapper handles next iteration. NEW-19 replay-from-worktree closed: commit 0772517 on integration/pilot_findings_closure_v6. New module runtime/replay.py + agent.run.run_replay + CLI `replay` command + EventType #38 REPLAY_MODE_STARTED. +5 tests, suite 2078→2083, mypy/ruff clean. S2 promoted to Current.

[2026-05-19 UTC] S2 done, runtime=loop_wrapper — wrapper handles next iteration. NEW-14 + NEW-15 closed: commit 7c554e3 on integration/pilot_findings_closure_v6. NEW-14 — runtime/git_env.py (_git_commit_env helper + GIT_COMMIT_ENV_INJECTED), env проброшен в stage5_completeness + commit_recovery git commit. NEW-15 — code_review verdict=error retry/escalate-story, EventType #39 CODE_REVIEW_ERROR, circuit breaker не кормится, fallback verdict сквозит через quality error. +9 tests, suite 2083→2092, mypy/ruff clean. Обновлены 3 существующих теста (event inventory + w4 spawn-failure). S3 promoted to Current.

[2026-05-19 UTC] S3 done, runtime=loop_wrapper — wrapper handles next iteration. NEW-16 + NEW-17 + NEW-18 closed: commit 2970769 on integration/pilot_findings_closure_v6. NEW-16 — runtime/pilot_outcomes.py partition_pilot_outcomes, EventType #40 INTEGRATION_MERGE_COMPLETED, real_pilot_done честный succeeded. NEW-17 — EventType #41 WORKER_EXIT_UNCOMMITTED, decide_uncommitted_exit на silent-failure пути. NEW-18 — structured bmad_format_unknown_status warning, case-insensitive статусы, +drafted/approved. +10 tests, suite 2092→2102, mypy/ruff clean. Обновлены 3 теста (event inventory ×2, test_b1, w4 ×2). Initiative complete — Pending empty, manual_merge_pending blocker written (Auto merge=false). NO ScheduleWakeup (loop_wrapper).

## Final Report

Initiative: Pilot Findings Closure v6 (replay-режим + commit/review/metrics gates)
Spec: spec/spec_pilot_findings_closure_v6.md
Started: 2026-05-19
Completed: 2026-05-19
Sessions: 3 planned, 3 executed, 0 buffered
Safety gate trips: 0
Human pauses: 0
Integration branch: integration/pilot_findings_closure_v6
Commits on integration (vs main):
  - 0772517 feat(replay): NEW-19 replay-from-worktree режим
  - 7c554e3 fix(worker): NEW-14 pre-commit env + NEW-15 code_review error handling
  - 2970769 fix(worker): NEW-16 succeeded-метрика + NEW-17 silent exit + NEW-18 status parser
  (+ tracker/launcher commits 2d273ed / 50fffe4 / 80cf666 / a070838)
Diff stats vs main: 27 files changed, 2561 insertions(+), 166 deletions(-)
Tests: 2078 → 2102 PASS (+24 net; NEW-19 ×5, NEW-14/15 ×9, NEW-16/17/18 ×10). mypy/ruff clean на changed files.
New EventTypes: #38 REPLAY_MODE_STARTED, #39 CODE_REVIEW_ERROR, #40 INTEGRATION_MERGE_COMPLETED, #41 WORKER_EXIT_UNCOMMITTED.
Scope: NEW-14..19 закрыты (6 находок), methodology §5 обновлена.
Recommendation: MERGE TO MAIN — все тесты зелёные, mypy/ruff clean, scope полностью закрыт. Финальная валидация (один `replay --worktree wt-1.4 --story 1.4` → story_merged) — отдельный шаг после merge, см. spec §8.
Merge hint: git checkout main && git merge --no-ff integration/pilot_findings_closure_v6 -m "merge pilot_findings_closure_v6 S1..S3"
