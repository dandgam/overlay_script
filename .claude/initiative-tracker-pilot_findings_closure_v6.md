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

- **id:** S2
  **title:** NEW-14 pre-commit env в recovery path + NEW-15 code_review error handling
  **surface:** backend-python
  **spec_section:** §2 (NEW-14), §3 (NEW-15)
  **acceptance:** stage5 recovery коммитит в config-less worktree без ошибки; code_review verdict=error не валит merge-gate безусловно, fallback-verdict доезжает до итогового решения. +8 tests (NEW-14 ×3 + NEW-15 ×5), каждый тест воспроизводит конкретный code path (правило v6 §0).
  **depends_on:** S1
  **destructive_actions:** []

- **id:** S3
  **title:** NEW-16 succeeded-метрика + NEW-17 silent worker exit + NEW-18 status parser
  **surface:** backend-python
  **spec_section:** §4 (NEW-16), §5 (NEW-17), §6 (NEW-18)
  **acceptance:** `succeeded` отражает только реально смерженные истории; worker без коммита даёт громкое событие `WORKER_EXIT_UNCOMMITTED`; нераспознанный статус → structured warning с story_id+raw_status. +9 tests (NEW-16 ×4 + NEW-17 ×3 + NEW-18 ×2). Финальная валидация: `replay --worktree wt-1.4 --story 1.4` → `story_merged` в integration/1a.
  **depends_on:** S2
  **destructive_actions:** []

### Current

- **id:** S1
  **title:** NEW-19 replay-from-worktree режим
  **surface:** backend-python
  **spec_section:** §1
  **acceptance:** `replay --worktree wt-1.4 --story 1.4` прогоняет хвост pipeline (stage5→build-check→merge-gate→reconcile→merge) за секунды, ноль вызовов worker-dev. EventType `REPLAY_MODE_STARTED`, лог `replay_mode_active`. +5 tests (3 unit CLI/skip-spawn/detect-commits + 2 integration mock-worktree/dirty+auto-commit-dev).
  **depends_on:** []
  **destructive_actions:** []
  **retry_count:** 0

### Completed
(none)

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

## Journal

[2026-05-19 UTC] bootstrap: tracker created via /auto-loop-spec-long, 3 sessions planned, S1 promoted to Current. Slug=pilot_findings_closure_v6, runtime=loop_wrapper, delay=120s, auto_merge=false. Backup branch backup/pilot_findings_closure_v6-pre-2026-05-19, integration branch integration/pilot_findings_closure_v6.

## Final Report

(empty — pending S3 close)
