# Initiative Tracker — Pilot Findings Closure v2 (Antares 1a real)

## Metadata
- **Spec:** spec/spec_pilot_findings_closure_v2.md
- **Integration branch:** integration/pilot_findings_closure_v2
- **Backup branch:** backup/pilot_findings_closure_v2-pre-2026-05-19
- **Created:** 2026-05-19
- **Bootstrap completed:** 2026-05-19 by /auto-loop-spec-long
- **Runtime:** loop_wrapper
- **Delay seconds:** 120
- **Auto merge:** false

## Scope Freeze

### In scope
- P1 NEW-1: `--project <slug>` flag overrides `ORCHESTRATOR_TARGET_PROJECT` env var (CLI precedence rule + fail-loud on registry miss).
- P1 NEW-2 Layer A: runner-side graceful Stage 7 (skip cleanup when worktree holds branch, emit synthetic verdict if commits present, env override `BMAD_RUNNER_SKIP_STAGE7=1`).
- P1 NEW-2 Layer B: orchestrator-side detector — `runtime/worker_silent_failure.py` regex `cannot delete branch .* used by worktree` → emit `RUNNER_CLEANUP_FAILED_REUSED_WORKTREE` event + synthetic `CODE_REVIEW_VERDICT verdict=approve` when feature branch has commits past base_sha.
- P2 NEW-3: `resolve_sprint_status_key` kebab+slug composite match (dotted-prefix lookup with tie-break warning).
- P2 NEW-4: `_tail_and_emit_completion` parses inner `^Exit code: (\d+)$` from stdout_lines and overrides `worker_completed.status="failure"` when inner != 0 while outer == 0.
- New event type: `RUNNER_CLEANUP_FAILED_REUSED_WORKTREE` (#30).
- Tests target ≥ +25 (running total ≥1960 PASS), mypy/ruff clean every commit.
- methodology-virgil.md §5 updates (flip NEW-1..NEW-4 to ✅ DONE with commit refs).

### Out of scope (explicit)
- P1 NEW-2 Layer C (pre-spawn worktree refresh / `--force-new-worktree` flag). Optional, picked up in S3 only if S1+S2 finish under budget.
- R3 per-turn token snapshot (separate research backlog item).
- R4 stale worktree GC (separate, overlaps with Layer C).
- R5 fail-closed cleanup policy (separate, guard for R4).
- The validation pilot run on Antares Epic 1 itself (post-merge follow-up mini-session; this initiative unblocks it, does not run it).
- Phase 5 items (observability dashboard, TTS notifications, Vision steps 3-7).

### Deferred to follow-up initiative
- Layer C — DEFERRED (not absorbed by S3; see Decisions Log S3).
- `pilot_findings_closure_v2_validation` — replay Antares Epic 1 (1.3/1.4/1.5) on fresh worktrees post-merge to confirm fixes.

## Sessions

### Pending
(none — initiative complete)

### Current
(none — all sessions completed)

### Completed
- **id:** S3
  **title:** P2 NEW-4 — inner exit code parsing + finalize (Layer C deferred)
  **completed:** 2026-05-19 06:07 UTC
  **commit:** b5d2d9c (S3 range 5bd9ff1..b5d2d9c)
  **files_changed:** 4 (runtime/worker_silent_failure.py, agent/run.py, test_worker_completed_inner_exit.py [new], spec/methodology-virgil.md)
  **tests_passed:** 2009 PASS (+7 new: 4 unit parse + 2 behaviour + 1 integration); ruff clean; mypy 0 new errors (4 pre-existing run.py errors out-of-scope, unchanged)
  **decisions_made:**
    - NEW-4 detector implemented as pure `parse_inner_exit_code` in `runtime/worker_silent_failure.py` (consistent with S2 Layer B — same module, unit-testable, no I/O). Regex `^(?:❯\s*)?Exit code:\s*(\d+)$` applied to stripped lines; last match wins (nested wrappers may echo twice).
    - Override wired into `agent/run.py::_tail_and_emit_completion` right after `exit_code = ev.get("exit_code")`: `inner_exit_failure` flag → `status="failure"` + `inner_exit_code`/`outer_exit_code` in WORKER_COMPLETED payload + `worker_inner_exit_mismatch` warning. Override beats BOTH the silent-failure path and the commits>0 "completed" path (verified by integration test).
    - Layer C (pre-spawn worktree refresh / `--force-new-worktree`) DEFERRED to follow-up: non-trivial `spawn_worker` change, overlaps R4 stale-worktree GC, initiative scope held to 3 sessions per bootstrap Decisions Log.
  **deferred_items:**
    - Layer C (pre-spawn worktree refresh / `--force-new-worktree`) → `pilot_findings_closure_v2_validation` follow-up or a dedicated R4 initiative.
    - 4 pre-existing mypy errors in `agent/run.py` (`bus` kwarg + tuple/list) — pre-date this initiative, out of scope (same as S1/S2).

- **id:** S2
  **title:** P1 NEW-2 — runner Stage 7 graceful (Layer A) + orchestrator detector (Layer B)
  **completed:** 2026-05-19 05:59 UTC
  **commit:** 0e4e6d0 (S2 range 5f890ad..0e4e6d0)
  **files_changed:** 9 (bmad-auto-dev-runner.sh, agent/run.py, runtime/event_loop.py, runtime/worker_silent_failure.py [new], test_canonical_patches_p6.py, test_s3_runtime.py + 3 new test files)
  **tests_passed:** 2002 PASS (+12 new: 5 runner-side + 5 detector + 2 e2e); ruff clean; mypy 0 new errors (4 pre-existing run.py errors out-of-scope, unchanged)
  **decisions_made:**
    - Layer A: runner branch deletion actually lives in Stage 6.pass (`git branch -d "$feature_branch"`, was line 764) — spec's "Stage 7" naming is loose. New `stage7_cleanup_feature_branch()` helper wraps it: worktree-hold check via `git worktree list --porcelain`, structured `stage7_skipped` log, synthetic `verdict=approve` claude_event JSON to stdout when `git rev-list base..branch` > 0, `BMAD_RUNNER_SKIP_STAGE7=1` override. Always returns 0 so cleanup never crashes the runner.
    - Layer B: detector lives in `agent/run.py::_tail_and_emit_completion` (spec anchor `runtime/worker_silent_failure.py` was the *new module name*, not the wire-in site). New pure module `runtime/worker_silent_failure.py` holds `detect_reused_worktree_cleanup_failure` + `decide_cleanup_recovery` (no I/O, unit-testable); `_tail_and_emit_completion` accumulates a bounded 300-line stdout tail and runs the detector on `worker_completed`.
    - Recovery flow: detect → emit `RUNNER_CLEANUP_FAILED_REUSED_WORKTREE`; commits>0 → synthetic `CODE_REVIEW_VERDICT verdict=approve source=runner_cleanup_recovery` + `WORKER_COMPLETED status=success` → return "completed"; commits==0 → fall through to existing silent_failure/halt path.
    - EventType #35 `RUNNER_CLEANUP_FAILED_REUSED_WORKTREE` (tracker scope said #30; actual enum count was 34 → 35). Inventory tests test_canonical_patches_p6 + test_s3_runtime updated in same commit.
  **deferred_items:**
    - 4 pre-existing mypy errors in `agent/run.py` (`bus` kwarg + tuple/list) — pre-date this initiative, out of S2 scope (same as S1).

- **id:** S1
  **title:** P1 NEW-1 — `--project` flag overrides env + P2 NEW-3 — kebab+slug composite resolver
  **completed:** 2026-05-19 05:44 UTC
  **commit:** 0348b75 (S1 range 64f07a9..0348b75)
  **files_changed:** 6 (cli/main.py, agent/run.py, runtime/project_registry.py, runtime/bmad_format.py, test_w1_real_pilot.py + 2 new test files)
  **tests_passed:** 1990 PASS (+12 new: 7 NEW-1 + 5 NEW-3); ruff clean; mypy 0 new errors (4 pre-existing run.py errors out-of-scope, unchanged)
  **decisions_made:**
    - NEW-1: resolution lives in CLI `run` (strict mode), `run_orchestrator` gained optional `settings` param — CLI hands down registry-resolved Settings; avoids cli→agent circular import.
    - NEW-1: `ProjectNotFoundError` added to `project_registry.py`; `_resolve_settings_for_project` got `strict` flag (raise for mutating subcommands, graceful degrade for read-only `_build_snapshot`).
    - NEW-1 audit: no offending direct `os.environ["ORCHESTRATOR_TARGET_PROJECT"]` reads in src — only `_build_worker_env` (by-design exception) + `eval/runner.py` save/restore + `project_registry.py` hint-string.
    - NEW-3: current normalize-tier ALREADY resolved `1.3`→`1-3-fastapi-...` composite (verified). Real remaining gap was non-determinism on multi-match; fixed via lexicographic sort + `sprint_status_key_ambiguous` tie-break warning. Spec code anchor `agent/story_id.py` was stale — function lives in `runtime/bmad_format.py`.
  **deferred_items:**
    - 4 pre-existing mypy errors in `agent/run.py` (lines ~1000/1010/1019/1062-69, `bus` kwarg + tuple/list) — pre-date this initiative, out of S1 scope.

## Safety Gates Triggered
(none)

## Blockers / Pauses
- **date:** 2026-05-19 06:07 UTC
  **session:** S3
  **type:** manual_merge_pending
  **detail:** initiative complete on integration/pilot_findings_closure_v2 (S1..S3, NEW-1..NEW-4). Auto merge=false — user must merge manually:
    git checkout main && git merge --no-ff integration/pilot_findings_closure_v2 -m "merge pilot_findings_closure_v2 S1..S3"
  **resolution:** PENDING (user action)

## Decisions Log
- **date:** 2026-05-19 UTC
  **session:** bootstrap
  **decision:** Layer C (pre-spawn worktree refresh) deferred to S3 as optional, not hard-required.
  **rationale:** Layers A+B together close the immediate blocker (runner Stage 7 + orchestrator detection). Layer C is a defence-in-depth that overlaps with R4 stale-worktree GC; pushing it as optional avoids scope creep beyond ~3 sessions.
  **impact:** If S3 budget tight, Layer C lands in a follow-up; doesn't block the validation pilot.

- **date:** 2026-05-19 UTC
  **session:** bootstrap
  **decision:** surface=`backend-python` for all 3 sessions (no `mixed`).
  **rationale:** All edits land in `src/bmad_orchestrator/` (Python) plus one bash file (`bmad-auto-dev-runner.sh`) inside the bundled skill — bash piece is ≪20% of any session. Dispatch rule 10 (`mixed`) does not apply: bash + Python is not Rust+React+vanilla coexistence. The `backend-python` workflow file is CRM-tied but its scaffold (pytest + ruff + mypy + Python edit) is the universal portion the sessions need.
  **impact:** Each session reads the spec directly for project-specific commands rather than blindly following the CRM-flavoured workflow file.

- **date:** 2026-05-19 05:44 UTC
  **session:** S1
  **decision:** `run_orchestrator` gained an optional `settings: Settings | None` param instead of resolving `--project` internally.
  **rationale:** Registry resolution + `ProjectNotFoundError` live in the CLI layer; passing pre-resolved Settings down keeps `agent/run.py` free of a `cli/main.py` import (circular). `None` default preserves every existing caller/test.
  **impact:** S2/S3 — any new code paths needing the resolved target should read `settings.target_project`, not env.

- **date:** 2026-05-19 05:59 UTC
  **session:** S2
  **decision:** Layer B detector implemented as a standalone pure module `runtime/worker_silent_failure.py` (regex + decision function, no I/O); wired into `agent/run.py::_tail_and_emit_completion`.
  **rationale:** Spec named `runtime/worker_silent_failure.py` but no such file existed and the live detection point is `_tail_and_emit_completion` in `agent/run.py`. Splitting pure helpers into the new module keeps them unit-testable in isolation and matches the spec's preferred filename.
  **impact:** S3 NEW-4 (inner exit-code parsing) also targets `_tail_and_emit_completion` in `agent/run.py` — same function, NOT `runtime/worker_spawn.py` as the spec anchor claims.

- **date:** 2026-05-19 06:07 UTC
  **session:** S3
  **decision:** Layer C (pre-spawn worktree refresh / `--force-new-worktree`) DEFERRED — not implemented in S3.
  **rationale:** Layers A+B already close the production blocker (runner graceful Stage 7 + orchestrator detection/recovery). Layer C is a `spawn_worker` change of non-trivial blast radius that overlaps R4 stale-worktree GC; implementing it would push the initiative past its 3-session frozen scope. NEW-4 (the required S3 item) plus finalize fit cleanly without it.
  **impact:** Layer C moves to the `pilot_findings_closure_v2_validation` follow-up or a dedicated R4 initiative. The validation replay still works — Layers A+B recover reused-worktree runs; Layer C is a pre-emptive optimisation, not a correctness fix.

## Journal
[2026-05-19 UTC] bootstrap: tracker + integration branch `integration/pilot_findings_closure_v2` + backup `backup/pilot_findings_closure_v2-pre-2026-05-19` created via /auto-loop-spec-long, delay=120s, Auto merge=false, 3 sessions planned (S1 NEW-1+NEW-3 / S2 NEW-2 A+B / S3 NEW-4 + optional Layer C + finalize).
[2026-05-19 05:44 UTC] S1 execution: NEW-1 — `--project` strict registry resolution, `ProjectNotFoundError`, `run_orchestrator` settings param; commit 64f07a9. NEW-3 — `resolve_sprint_status_key` deterministic tie-break + warning; commit 729650f. Regression fix test_w1_real_pilot; commit 0348b75. Full suite 1990 PASS, ruff clean.
[2026-05-19 05:44 UTC] S1 completed, S2 promoted to Current. runtime=loop_wrapper — wrapper handles next iteration.
[2026-05-19 05:59 UTC] S2 execution: NEW-2 Layer A — `stage7_cleanup_feature_branch()` graceful Stage 7 in bmad-auto-dev-runner.sh (worktree-hold skip + synthetic verdict + BMAD_RUNNER_SKIP_STAGE7 override). Layer B — new `runtime/worker_silent_failure.py` pure detector + wire-in to `_tail_and_emit_completion`, EventType #35 `RUNNER_CLEANUP_FAILED_REUSED_WORKTREE`. +12 tests (5 runner + 5 detector + 2 e2e), 2002 PASS, ruff clean, mypy 0 new. Commit 0e4e6d0.
[2026-05-19 05:59 UTC] S2 completed, S3 promoted to Current. runtime=loop_wrapper — wrapper handles next iteration.
[2026-05-19 06:07 UTC] S3 execution: NEW-4 — `parse_inner_exit_code` pure parser in worker_silent_failure.py + override in `_tail_and_emit_completion` (inner!=0 && outer==0 → status=failure + inner/outer_exit_code in payload); commit 5bd9ff1, +7 tests. Layer C DEFERRED (scope guard — see Decisions Log). Finalize — methodology-virgil §5 NEW-1..NEW-4 flipped to ✅ DONE; commit b5d2d9c. Full suite 2009 PASS, ruff clean, mypy 0 new.
[2026-05-19 06:07 UTC] S3 completed — initiative done. Auto merge=false → manual_merge_pending blocker logged (resolution PENDING). runtime=loop_wrapper — wrapper exits, no further iterations.

## Final Report (populated on last session completion)

Initiative: Pilot Findings Closure v2 (Antares 1a real)
Spec: spec/spec_pilot_findings_closure_v2.md
Started: 2026-05-19 (bootstrap)
Completed: 2026-05-19 06:07 UTC
Sessions: 3 planned, 3 executed, 0 buffered
Safety gate trips: 0
Human pauses: 0
Integration branch: integration/pilot_findings_closure_v2
Final commit: b5d2d9c
Initiative commits (S1..S3):
  64f07a9 feat(cli): --project flag побеждает ORCHESTRATOR_TARGET_PROJECT env (NEW-1)
  729650f feat(dag): resolve_sprint_status_key — детерминированный tie-break (NEW-3)
  0348b75 test(cli): test_w1_cli_daemon_args регистрирует project (NEW-1 fallout)
  0e4e6d0 feat(worker): NEW-2 — graceful Stage 7 cleanup + reused-worktree detector
  5bd9ff1 feat(worker): NEW-4 — inner exit code overrides outer-0 worker_completed
  b5d2d9c docs(spec): methodology-virgil §5 — NEW-1..NEW-4 flipped to DONE
  (+ 3 tracker commits: 3073f4c bootstrap, 5f890ad S1, abfd012 S2)
Diff stats (main..integration, full branch incl. pre-bootstrap /virgil commits): 21 files changed, +1976 / -38
Tests: 1860 baseline → 2009 PASS (+31 this initiative: S1 +12, S2 +12, S3 +7; remainder from pre-bootstrap branch commits). ruff clean, mypy 0 new errors (4 pre-existing agent/run.py errors out-of-scope).
EventType count: 35 (+1 RUNNER_CLEANUP_FAILED_REUSED_WORKTREE).
Scope delivered: NEW-1 ✅ / NEW-2 Layer A+B ✅ / NEW-3 ✅ / NEW-4 ✅. Layer C DEFERRED (see Decisions Log S3).
Recommendation: NEEDS HUMAN REVIEW then MERGE TO MAIN. Branch also carries pre-bootstrap /virgil Session-3 commits (bd27770, ce2c223) — review those are intended for this merge.
Merge hint: git checkout main && git merge --no-ff integration/pilot_findings_closure_v2 -m "merge pilot_findings_closure_v2 S1..S3"
Follow-up: pilot_findings_closure_v2_validation — replay Antares Epic 1 (1.3/1.4/1.5) on fresh worktrees post-merge; pick up Layer C there or in a dedicated R4 initiative.
