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
(none)

### Current

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
    - methodology-virgil.md §5 — NEW-9/NEW-10/NEW-5-recheck помечены DONE
    - +7 + +5 = +12 tests
  **safety_gates: []
  **checkpoint:** false
  **estimated_retries_allowed:** 3
  **started:** 2026-05-19
  **workflow:** workflows/backend-python.md
  **retry_count:** 0
  **worker_branches:** []

### Completed

- **id:** S1
  **title:** NEW-9 verdict source-of-truth для worker success
  **surface:** backend-python
  **completed:** 2026-05-19
  **commit:** 80dd56d
  **result:** decide_worker_status + read_runner_verdict реализованы, wired в
    _tail_and_emit_completion. worker_completed payload содержит verdict /
    new_commits_count / status_decided_by. +11 tests (2038→2049 PASS),
    mypy/ruff clean. Все acceptance criteria выполнены, включая regression
    fixture pilot run #3 1.5 (approve + 2 commits + inner exit 2 → success).

## Safety Gates Triggered
(none)

## Blockers / Pauses
(none)

## Decisions Log

[2026-05-19] S1: decide_worker_status размещён в runtime/worker_silent_failure.py
(рядом с parse_inner_exit_code — обе pure-функции про worker terminal status),
а не в worker_spawn.py как буквально указано в spec §1. Spec допускает «или где
формируется worker_completed»; фактический wiring-site = agent/run.py
_tail_and_emit_completion. read_runner_verdict вынесен в verdict_fallback.py.

## Journal

[2026-05-19 UTC] bootstrap: tracker created via /auto-loop-spec-long, 2 sessions planned, S1 promoted to Current. Slug=pilot_findings_closure_v4, runtime=loop_wrapper, delay=120s, auto_merge=false.

[2026-05-19 10:00 UTC] S1 done — NEW-9 verdict source-of-truth. commit 80dd56d. +11 tests (2049 PASS), mypy/ruff clean. S2 promoted to Current. runtime=loop_wrapper — wrapper handles next iteration.

## Final Report
(empty)
