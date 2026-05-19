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

- **id:** S2
  **title:** NEW-13 security_review error handling
  **surface:** backend-python
  **acceptance:** `verdict=error` не инкрементит circuit breaker; pipeline не abort'ится из-за сбоя одного review-шага; story с устойчивым error → одиночная HUMAN_QUERY эскалация. +7 tests.
  **spec_section:** "## 3. NEW-13 (P2) — security_review error → circuit breaker abort"
  **depends_on:** S1
  **destructive_actions:** []

### Current

- **id:** S1
  **title:** NEW-11 ruff build_check + NEW-12 pre-commit config
  **surface:** backend-python
  **acceptance:** ruff в worktree без конфига target-проекта → не приводит к немедленному halt; worktree без `.pre-commit-config.yaml` → `git commit` воркера проходит. +10 tests.
  **spec_section:** "## 1. NEW-11 (P2) — ruff build_check_halt + ## 2. NEW-12 (P2) — pre-commit config missing"
  **depends_on:** none
  **destructive_actions:** []
  **retry_count:** 0

### Completed
(none)

## Safety Gates Triggered
(none)

## Blockers / Pauses
(none)

## Decisions Log
(none)

## Journal

[2026-05-19 UTC] bootstrap: tracker created via /auto-loop-spec-long, 2 sessions planned, S1 promoted to Current. Slug=pilot_findings_closure_v5, runtime=loop_wrapper, delay=120s, auto_merge=false.

## Final Report
(empty — pending S2 close)
