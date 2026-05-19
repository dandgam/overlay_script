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
- Layer C if not absorbed by S3.
- `pilot_findings_closure_v2_validation` — replay Antares Epic 1 (1.3/1.4/1.5) on fresh worktrees post-merge to confirm fixes.

## Sessions

### Pending
(none — S3 promoted to Current)

### Current
- **id:** S3
  **title:** P2 NEW-4 — inner exit code parsing + optional Layer C + finalize
  **surface:** backend-python
  **spec_section:** 156-210
  **depends_on:** [S2]
  **acceptance:**
    - `_tail_and_emit_completion` (lives in `agent/run.py`, NOT `runtime/worker_spawn.py` — spec anchor stale) scans last ~50 stdout lines for `^Exit code: (\d+)$` (or `❯ Exit code: …`); when inner != 0 and outer == 0 → emit `worker_completed status=failure inner_exit_code=N outer_exit_code=0`.
    - No regression on success path (inner=0 → status=success).
    - +4 tests minimum (3 unit + 1 integration).
    - Optional Layer C (pre-spawn refresh / `--force-new-worktree`): only attempted if S1+S2 came in under budget; otherwise deferred.
    - methodology-virgil.md §5 updated: NEW-1..NEW-4 flipped to ✅ DONE with commit refs.
    - Final tests count ≥1960 PASS; mypy/ruff clean.
    - Final Report populated with commit list, diff stats, manual-merge hint.
  **safety_gates:**
    - L3 branch check.
    - L1 deny-list.
  **checkpoint:** false
  **estimated_retries_allowed:** 3
  **started:** 2026-05-19 05:59 UTC
  **workflow:** workflows/backend-python.md
  **retry_count:** 0
  **worker_branches:** []

### Completed
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
(none yet)

## Blockers / Pauses
(none yet)

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

## Journal
[2026-05-19 UTC] bootstrap: tracker + integration branch `integration/pilot_findings_closure_v2` + backup `backup/pilot_findings_closure_v2-pre-2026-05-19` created via /auto-loop-spec-long, delay=120s, Auto merge=false, 3 sessions planned (S1 NEW-1+NEW-3 / S2 NEW-2 A+B / S3 NEW-4 + optional Layer C + finalize).
[2026-05-19 05:44 UTC] S1 execution: NEW-1 — `--project` strict registry resolution, `ProjectNotFoundError`, `run_orchestrator` settings param; commit 64f07a9. NEW-3 — `resolve_sprint_status_key` deterministic tie-break + warning; commit 729650f. Regression fix test_w1_real_pilot; commit 0348b75. Full suite 1990 PASS, ruff clean.
[2026-05-19 05:44 UTC] S1 completed, S2 promoted to Current. runtime=loop_wrapper — wrapper handles next iteration.
[2026-05-19 05:59 UTC] S2 execution: NEW-2 Layer A — `stage7_cleanup_feature_branch()` graceful Stage 7 in bmad-auto-dev-runner.sh (worktree-hold skip + synthetic verdict + BMAD_RUNNER_SKIP_STAGE7 override). Layer B — new `runtime/worker_silent_failure.py` pure detector + wire-in to `_tail_and_emit_completion`, EventType #35 `RUNNER_CLEANUP_FAILED_REUSED_WORKTREE`. +12 tests (5 runner + 5 detector + 2 e2e), 2002 PASS, ruff clean, mypy 0 new. Commit 0e4e6d0.
[2026-05-19 05:59 UTC] S2 completed, S3 promoted to Current. runtime=loop_wrapper — wrapper handles next iteration.

## Final Report (populated on last session completion)
(empty)
