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

- **id:** S3
  **title:** NEW-16 succeeded-метрика + NEW-17 silent worker exit + NEW-18 status parser
  **surface:** backend-python
  **spec_section:** §4 (NEW-16), §5 (NEW-17), §6 (NEW-18)
  **acceptance:** `succeeded` отражает только реально смерженные истории; worker без коммита даёт громкое событие `WORKER_EXIT_UNCOMMITTED`; нераспознанный статус → structured warning с story_id+raw_status. +9 tests (NEW-16 ×4 + NEW-17 ×3 + NEW-18 ×2). Финальная валидация: `replay --worktree wt-1.4 --story 1.4` → `story_merged` в integration/1a.
  **depends_on:** S2
  **destructive_actions:** []
  **retry_count:** 0

### Completed

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
(none)

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

## Journal

[2026-05-19 UTC] bootstrap: tracker created via /auto-loop-spec-long, 3 sessions planned, S1 promoted to Current. Slug=pilot_findings_closure_v6, runtime=loop_wrapper, delay=120s, auto_merge=false. Backup branch backup/pilot_findings_closure_v6-pre-2026-05-19, integration branch integration/pilot_findings_closure_v6.

[2026-05-19 UTC] S1 done, runtime=loop_wrapper — wrapper handles next iteration. NEW-19 replay-from-worktree closed: commit 0772517 on integration/pilot_findings_closure_v6. New module runtime/replay.py + agent.run.run_replay + CLI `replay` command + EventType #38 REPLAY_MODE_STARTED. +5 tests, suite 2078→2083, mypy/ruff clean. S2 promoted to Current.

[2026-05-19 UTC] S2 done, runtime=loop_wrapper — wrapper handles next iteration. NEW-14 + NEW-15 closed: commit 7c554e3 on integration/pilot_findings_closure_v6. NEW-14 — runtime/git_env.py (_git_commit_env helper + GIT_COMMIT_ENV_INJECTED), env проброшен в stage5_completeness + commit_recovery git commit. NEW-15 — code_review verdict=error retry/escalate-story, EventType #39 CODE_REVIEW_ERROR, circuit breaker не кормится, fallback verdict сквозит через quality error. +9 tests, suite 2083→2092, mypy/ruff clean. Обновлены 3 существующих теста (event inventory + w4 spawn-failure). S3 promoted to Current.

## Final Report

(empty — pending S3 close)
