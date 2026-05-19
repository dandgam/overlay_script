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
- **id:** S2
  **title:** P1 NEW-2 — runner Stage 7 graceful (Layer A) + orchestrator detector (Layer B)
  **surface:** backend-python
  **spec_section:** 78-130
  **depends_on:** [S1]
  **acceptance:**
    - `agent/skills/bmad-auto-dev/scripts/bmad-auto-dev-runner.sh` Stage 7 checks `git worktree list` for branch checkout before `git branch -D`; skips cleanup gracefully on reused worktree.
    - Synthetic `verdict=approve commits=N` event emitted when feature branch has unmerged commits past base_sha and Stage 7 had to skip.
    - `runtime/worker_silent_failure.py` (or equivalent) recognises `cannot delete branch .* used by worktree` regex in stdout_lines.
    - New `RUNNER_CLEANUP_FAILED_REUSED_WORKTREE` event registered in `runtime/event_loop.py` (event count 29 → 30).
    - On detect with commits present → synthetic `CODE_REVIEW_VERDICT verdict=approve source=runner_cleanup_recovery commits=N`; with no commits → preserve halt behaviour.
    - +11 tests total (5 runner-side unit + 4 detector unit + 2 e2e mock).
    - mypy/ruff clean. Tests ≥ baseline +21 cumulative.
  **safety_gates:**
    - L3 branch check — work only on `integration/pilot_findings_closure_v2`.
    - L1 deny-list — `git push --force`, `--no-verify`, `git reset --hard` blocked.
  **checkpoint:** false
  **estimated_retries_allowed:** 3

- **id:** S3
  **title:** P2 NEW-4 — inner exit code parsing + optional Layer C + finalize
  **surface:** backend-python
  **spec_section:** 156-210
  **depends_on:** [S2]
  **acceptance:**
    - `runtime/worker_spawn.py::_tail_and_emit_completion` scans last ~50 stdout lines for `^Exit code: (\d+)$` (or `❯ Exit code: …`); when inner != 0 and outer == 0 → emit `worker_completed status=failure inner_exit_code=N outer_exit_code=0`.
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

### Current
- **id:** S1
  **title:** P1 NEW-1 — `--project` flag overrides env + P2 NEW-3 — kebab+slug composite resolver
  **surface:** backend-python
  **spec_section:** 32-76
  **depends_on:** []
  **acceptance:**
    - `_resolve_settings_for_project` (cli/main.py:393-413) raises `ProjectNotFoundError` when `--project <slug>` is passed and registry has no entry (not silent fallback).
    - `--project` flag overrides `ORCHESTRATOR_TARGET_PROJECT` env var deterministically; documented precedence: flag > yaml registry > env > default.
    - Audit pass: no module under `src/bmad_orchestrator/` (except `worker_spawn._build_worker_env`) reads `os.environ["ORCHESTRATOR_TARGET_PROJECT"]` directly post-fix.
    - `agent/story_id.py::resolve_sprint_status_key` matches `1.3` → `1-3-fastapi-app-lifespan-health` (composite key) via dotted-prefix split fallback; tie-break warning logged on multiple matches.
    - +10 tests (6 NEW-1: 3 unit + 2 integration + 1 regression on other subcommands; 4 NEW-3: 3 unit + 1 integration).
    - mypy/ruff clean. Tests ≥ baseline +10 cumulative.
    - 2 commits: one for NEW-1, one for NEW-3.
  **safety_gates:**
    - L3 branch check — work only on `integration/pilot_findings_closure_v2`.
    - L1 deny-list — `git push --force`, `--no-verify`, `git reset --hard` blocked.
  **checkpoint:** false
  **estimated_retries_allowed:** 3
  **started:** (pending first wake)
  **workflow:** workflows/backend-python.md
  **retry_count:** 0
  **worker_branches:** []

### Completed
(none yet)

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

## Journal
[2026-05-19 UTC] bootstrap: tracker + integration branch `integration/pilot_findings_closure_v2` + backup `backup/pilot_findings_closure_v2-pre-2026-05-19` created via /auto-loop-spec-long, delay=120s, Auto merge=false, 3 sessions planned (S1 NEW-1+NEW-3 / S2 NEW-2 A+B / S3 NEW-4 + optional Layer C + finalize).

## Final Report (populated on last session completion)
(empty)
