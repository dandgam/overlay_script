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
(none — initiative complete)

### Completed

- **id:** S2
  **title:** NEW-10 worker events propagation + NEW-5 recheck
  **surface:** backend-python
  **completed:** 2026-05-19
  **commit:** 1327b43
  **result:** Новый модуль runtime/worker_events.py — merge_worktree_events
    (append + dedup по ts), worktree_events_path / main_events_path,
    detect_stage_marker. Wired в _tail_and_emit_completion: merge worktree
    events после worker_completed + worker_stage_progress лог-строки.
    NEW-5 recheck: filter_dirty_outside_claude игнорирует `.claude/` пути в
    dirty-worktree gate, _clean_dirty_worktree → `git clean -fd -e .claude`.
    methodology-virgil §5 — NEW-9/NEW-10/NEW-5-recheck помечены DONE.
    +12 tests (2049→2061 PASS), mypy/ruff clean. Все acceptance criteria
    выполнены.

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

[2026-05-19 11:00 UTC] manual_merge_pending — initiative complete on integration/pilot_findings_closure_v4. User must merge manually:
  git checkout main && git merge --no-ff integration/pilot_findings_closure_v4 -m "merge pilot_findings_closure_v4 S1..S2"
resolution: PENDING (user action)

## Decisions Log

[2026-05-19] S1: decide_worker_status размещён в runtime/worker_silent_failure.py
(рядом с parse_inner_exit_code — обе pure-функции про worker terminal status),
а не в worker_spawn.py как буквально указано в spec §1. Spec допускает «или где
формируется worker_completed»; фактический wiring-site = agent/run.py
_tail_and_emit_completion. read_runner_verdict вынесен в verdict_fallback.py.

[2026-05-19] S2: NEW-10 реализован как отдельный модуль runtime/worker_events.py
(не вкомпилен в worker_spawn.py) — merge/path/stage функции переиспользуемы и
изолированно тестируемы. NEW-5 recheck: spec предлагал либо `git clean -e .claude`
либо фильтрацию dirty вне `.claude/` — сделано ОБА (фильтр на gate-входе +
`-e .claude` на clean) для defence-in-depth: gate не халтит на embedded skills,
а если реальная грязь триггерит clean — skills всё равно переживают.

## Journal

[2026-05-19 UTC] bootstrap: tracker created via /auto-loop-spec-long, 2 sessions planned, S1 promoted to Current. Slug=pilot_findings_closure_v4, runtime=loop_wrapper, delay=120s, auto_merge=false.

[2026-05-19 10:00 UTC] S1 done — NEW-9 verdict source-of-truth. commit 80dd56d. +11 tests (2049 PASS), mypy/ruff clean. S2 promoted to Current. runtime=loop_wrapper — wrapper handles next iteration.

[2026-05-19 11:00 UTC] S2 done — NEW-10 events propagation + NEW-5 recheck. commit 1327b43. +12 tests (2061 PASS), mypy/ruff clean. Pending empty → initiative complete. Auto merge=false → manual_merge_pending, Final Report written. runtime=loop_wrapper — wrapper exits on populated Final Report.

## Final Report

Initiative: Pilot Findings Closure v4 (verdict source-of-truth + observability)
Spec: spec/spec_pilot_findings_closure_v4.md
Started: 2026-05-19
Completed: 2026-05-19
Sessions: 2 planned, 2 executed, 0 buffered
Safety gate trips: 0
Human pauses: 0
Integration branch: integration/pilot_findings_closure_v4
Final commit: 1327b43 (+ tracker commit follows)
Commits on integration (vs main):
  80dd56d feat(worker): NEW-9 verdict как source-of-truth для worker success
  f7f5e79 tracker(pilot_findings_closure_v4): S1 NEW-9 completed, S2 promoted
  ffc33b2 docs(spec): methodology-virgil §5 — разметка Тип (Баг/Улучшение)
  1327b43 feat(worker): NEW-10 worker events propagation + NEW-5 embedded skills not dirty
Diff stats (code, vs pre-S1): 9 files changed, 1059 insertions(+), 56 deletions(-)
Tests: 2038 → 2061 PASS (+23: +11 NEW-9, +7 NEW-10, +5 NEW-5), mypy/ruff clean.
Acceptance (epic §5):
  - Tests ≥2055 PASS — ✅ 2061
  - NEW-9 verdict source-of-truth — ✅ (S1)
  - NEW-10 events propagation — ✅ (S2)
  - NEW-5 embedded skills not dirty — ✅ (S2)
  - methodology-virgil §5 DONE marks — ✅
Local checkpoints written: none (Auto merge=false, no checkpoint sessions)
Recommendation: MERGE TO MAIN — all acceptance criteria met, full suite green.
  Then run NEW-7 validation: pilot replay (Antares 1a) должен создать
  integration/wave-1a ветку (NEW-9 разблокировал success path).
Merge hint: git checkout main && git merge --no-ff integration/pilot_findings_closure_v4 -m "merge pilot_findings_closure_v4 S1..S2"
