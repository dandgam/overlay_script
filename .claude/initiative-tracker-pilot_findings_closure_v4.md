# Initiative Tracker — Pilot Findings Closure v4 (verdict source-of-truth)

## Metadata
- **Spec:** spec/spec_pilot_findings_closure_v4.md
- **Integration branch:** integration/pilot_findings_closure_v4
- **Created:** 2026-05-19
- **Bootstrap completed:** 2026-05-19 by /auto-loop-spec-long
- **Scope frozen:** 2026-05-19
- **Runtime:** loop_wrapper
- **Delay seconds:** 120
- **Auto merge:** false

## Scope Freeze

### In scope
- NEW-9 (P1): verdict=approve + commits → success, runner exit code вторичен. `decide_worker_status` + `read_runner_verdict`.
- NEW-10 (P2): worker events → главный events.jsonl + промежуточные orchestrator-лог строки.
- NEW-5 recheck (P2): embedded skills (`.claude/`) не считаются dirty worktree.
- methodology-virgil.md §5 — пометить NEW-9/NEW-10/NEW-5-recheck DONE.
- Tests target ≥2055 PASS, mypy/ruff clean.

### Out of scope (explicit)
- NEW-7 финальная валидация — отдельный pilot replay ПОСЛЕ merge v4.
- Production pilot сам по себе.
- P3 backlog-writer subscriber (мета-фича).
- Auto-split поведение (BMAD_AUTO_SPLIT) — тестируется в replay после v4.

### Deferred to follow-up initiative
- NEW-7 end-to-end валидация (pilot replay)
- backlog-writer subscriber

## Sessions

### Pending

- **id:** S2
  **title:** NEW-10 worker events propagation + NEW-5 recheck
  **surface:** backend-python
  **spec_section:** spec/spec_pilot_findings_closure_v4.md §2 + §3
  **depends_on:** [S1]
  **acceptance:**
    - Главный runs/default/wt-<id>.events.jsonl получает свежие worker события
    - Orchestrator-лог не молчит >5 мин при активных воркерах
    - Dirty-detector игнорирует `.claude/` пути (embedded skills не «грязь»)
    - Реальная грязь вне `.claude/` по-прежнему ловится
    - +7 + +5 = +12 tests
  **safety_gates: []
  **checkpoint:** false
  **estimated_retries_allowed:** 3

### Current

- **id:** S1
  **title:** NEW-9 verdict source-of-truth для worker success
  **surface:** backend-python
  **spec_section:** spec/spec_pilot_findings_closure_v4.md §1
  **depends_on:** []
  **acceptance:**
    - `decide_worker_status(verdict, commits, inner_exit, outer_exit)` — verdict=approve+commits→success независимо от inner_exit
    - `read_runner_verdict(worktree, story_id)` парсит stage6 review log
    - `_tail_and_emit_completion` wired → worker_completed payload содержит verdict/new_commits_count/status_decided_by
    - verdict=request_changes → failure даже с commits
    - verdict=None → fallback на exit-code логику (backwards compat)
    - regression: pilot run #3 1.5 fixture (verdict approve, 2 commits, exit non-zero) → success
    - +11 tests
  **safety_gates: []
  **checkpoint:** false
  **estimated_retries_allowed:** 3
  **started:** 2026-05-19 (bootstrap)
  **workflow:** workflows/backend-python.md
  **retry_count:** 0
  **worker_branches:** []

### Completed
(none)

## Safety Gates Triggered
(none)

## Blockers / Pauses
(none)

## Decisions Log
(none)

## Journal

[2026-05-19 UTC] bootstrap: tracker created via /auto-loop-spec-long, 2 sessions planned, S1 promoted to Current. Slug=pilot_findings_closure_v4, runtime=loop_wrapper, delay=120s, auto_merge=false.

## Final Report
(empty)
