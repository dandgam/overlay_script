# Initiative Tracker — Pilot Findings Closure v5 (build/commit gates + security_review error)

## Metadata
- **Spec:** spec/spec_pilot_findings_closure_v5.md
- **Integration branch:** integration/pilot_findings_closure_v5
- **Created:** 2026-05-19
- **Bootstrap completed:** 2026-05-19 by /auto-loop-spec-long
- **Scope frozen:** 2026-05-19
- **Runtime:** loop_wrapper
- **Delay seconds:** 120
- **Auto merge:** false

## Scope Freeze

### In scope
- NEW-11 (P2): ruff build_check_halt в worktree — диагностика + graceful path при отсутствии конфига target-проекта.
- NEW-12 (P2): pre-commit config missing — `PRE_COMMIT_ALLOW_NO_CONFIG=1` в env воркера + detector.
- NEW-13 (P2): security_review `verdict=error` не должен инкрементить circuit breaker → abort pipeline.
- methodology-virgil.md §5 — пометить NEW-11/NEW-12/NEW-13 DONE.
- Tests target ≥2078 PASS, mypy/ruff clean.

### Out of scope (explicit)
- Production pilot replay (Antares 1a) — отдельно ПОСЛЕ merge v5.
- P3 backlog-writer subscriber (мета-фича).
- NEW-7 повторная валидация — уже validated в pilot run #4.

### Deferred to follow-up initiative
- Pilot replay для валидации 3/3 stories в integration/1a.

## Sessions

### Pending
(none)

### Current

- **id:** S2
  **title:** NEW-13 security_review error handling
  **surface:** backend-python
  **spec_section:** "## 3. NEW-13 (P2) — security_review error → circuit breaker abort"
  **depends_on:** S1
  **acceptance:** `verdict=error` не инкрементит circuit breaker; pipeline не abort'ится из-за сбоя одного review-шага; story с устойчивым error → одиночная HUMAN_QUERY эскалация. +7 tests.
  **destructive_actions:** []
  **started:** 2026-05-19 19:30 UTC
  **workflow:** workflows/backend-python.md
  **retry_count:** 0
  **worker_branches:** []

### Completed

- **id:** S1
  **title:** NEW-11 ruff build_check + NEW-12 pre-commit config
  **completed:** 2026-05-19 19:30 UTC
  **commit:** cab0a75
  **files_changed:** 8
  **tests_passed:** suite 2071 PASS (2061→2071, +10); ruff clean
  **decisions_made:**
    - NEW-11: выбран graceful-skip-on-no-config (флаг `skip_if_no_ruff_config`) вместо scoping ruff на changed files — проще, satisfies acceptance, ниже риск.
    - Cross-impact: build-check больше не халтит config-less worktree → downstream code-review спавнится. test_w1_real_pilot counting fakes обновлены — считают только dev-спавны (skill_invocation отсутствует у dev, есть у review/merge-gate).
  **deferred_items:**
    - 3 pre-existing mypy errors (sandbox.py:528, phase4_subscribers.py:142, main_merge_token.py:40) — присутствуют на чистом дереве до v5, НЕ из S1. Out of S1 scope.

## Safety Gates Triggered
(none)

## Blockers / Pauses
(none)

## Decisions Log

- **date:** 2026-05-19 19:30 UTC
  **session:** S1
  **decision:** 3 pre-existing mypy errors не из v5 — не чинятся в этой инициативе.
  **rationale:** На чистом дереве (git stash) те же 3 ошибки; mypy version drift / tech debt предшествует v5.
  **impact:** Epic acceptance "mypy clean" не достижим без отдельного фикса; S2 не должна вносить новых mypy ошибок, существующие 3 остаются.

## Journal

[2026-05-19 UTC] bootstrap: tracker created via /auto-loop-spec-long, 2 sessions planned, S1 promoted to Current. Slug=pilot_findings_closure_v5, runtime=loop_wrapper, delay=120s, auto_merge=false.
[2026-05-19 19:30 UTC] S1 execution: workflow backend-python — NEW-11 (skip_if_no_ruff_config флаг в build_check.py + build-check.yaml) + NEW-12 (WORKER_ENV_INJECTED PRE_COMMIT_ALLOW_NO_CONFIG в worker_spawn.py + precommit detector). +10 tests (test_new11 5, test_new12 5). Cross-impact: test_w1_real_pilot 3 теста обновлены. Suite 2071 PASS, ruff clean. Commit cab0a75.
[2026-05-19 19:30 UTC] S1 completed, S2 promoted to Current. runtime=loop_wrapper — wrapper handles next iteration.

## Final Report
(empty — pending S2 close)
