# Initiative Tracker — Pilot Findings Closure + Phase 3 Step B

## Metadata
- **Spec:** spec/spec_pilot_findings_closure.md
- **Integration branch:** integration/pilot_findings_closure
- **Created:** 2026-05-19
- **Bootstrap completed:** 2026-05-19 by /auto-loop-spec-long
- **Scope frozen:** 2026-05-19
- **Runtime:** loop_wrapper
- **Delay seconds:** 300
- **Auto merge:** false

## Scope Freeze

### In scope
- 5 P1 items from pilot findings + research (mark-done normalize, verdict event wiring, autofix routing+split, AbortController, MCP readiness)
- 3 P2 items (budget auto-detect, halt pre-flight, subprocess timeout adaptive)
- 1 P3 item (real_pilot_done counter split)
- Phase 3 closure: Step B real BMad eval cases (≥10 + baseline)
- methodology-virgil.md §5 updates (mark all 10 items DONE, close Phase 3 gate)
- 4-5 new event types (`STORY_AUTO_SPLIT`, `WORKER_CANCELLED`, `MCP_NOT_READY`, `BUDGET_AUTO_DISABLED`, `WORKER_HALT_PRESPAWN`)
- Tests target ≥1950 PASS (delta +90 from baseline 1860), mypy/ruff clean

### Out of scope (explicit)
- R3/R4/R5 research findings (defer)
- P3 backlog-writer subscriber meta-feature
- Observability dashboard (Phase 5 item #12)
- TTS notifications
- Vision steps 3-7 embedding work
- Production pilot itself (#10) — this initiative unblocks it but does not run it

### Deferred to follow-up initiative
- R3 per-turn token snapshot
- R4 stale worktree GC
- R5 fail-closed cleanup policy
- Backlog-writer subscriber meta-feature

## Sessions

### Pending

- **id:** S4
  **title:** #4 AbortController per worker
  **surface:** backend-python
  **spec_section:** spec/spec_pilot_findings_closure.md §1 #4
  **depends_on:** []
  **acceptance:**
    - Per-worker cancellation token; supervisor `cancel_worker` action wired
    - WORKER_CANCELLED event emitted with reason + cancelled_by
    - Stuck worker (sleep 9999) killed in <5s wall clock
    - +8 tests
  **safety_gates: []
  **checkpoint:** true
  **estimated_retries_allowed:** 3

- **id:** S5
  **title:** #5 MCP server readiness polling
  **surface:** backend-python
  **spec_section:** spec/spec_pilot_findings_closure.md §1 #5
  **depends_on:** []
  **acceptance:**
    - poll_mcp_ready util (30s max, 500ms interval)
    - MCP_NOT_READY event; worker not spawned if required MCP not authenticated
    - Per-story override via frontmatter requires_mcp:[]
    - +8 tests
  **safety_gates: []
  **checkpoint:** false
  **estimated_retries_allowed:** 3

- **id:** S6
  **title:** #6 budget auto-detect (subscription mode) + #7 halt pre-flight check
  **surface:** backend-python
  **spec_section:** spec/spec_pilot_findings_closure.md §2 #6 + #7
  **depends_on:** []
  **acceptance:**
    - subscription_mode detect auto-sets Settings._budget_disabled, emits BUDGET_AUTO_DISABLED
    - Pre-spawn halt-reason.txt check skips spawn with WORKER_HALT_PRESPAWN event
    - --resume flag clears halt before spawn
    - +5 + +5 = +10 tests
  **safety_gates: []
  **checkpoint:** false
  **estimated_retries_allowed:** 3

- **id:** S7
  **title:** #10 Step B real eval cases — harness + first 5 cases
  **surface:** backend-python
  **spec_section:** spec/spec_pilot_findings_closure.md §4 #10 (part 1)
  **depends_on:** []
  **acceptance:**
    - evals/cases/real/ contains 5 cases (3 easy + 2 medium) with YAML schema
    - CLI `bmad-orchestrator eval run --mode real --project-root <path> --cases-dir evals/cases/real/` works
    - +6 tests
  **safety_gates: []
  **checkpoint:** true
  **estimated_retries_allowed:** 3

- **id:** S8
  **title:** #10 part 2 (5 hard cases + baseline) + methodology Phase 3 close-out
  **surface:** backend-python
  **spec_section:** spec/spec_pilot_findings_closure.md §4 #10 (part 2) + §6
  **depends_on:** [S7]
  **acceptance:**
    - ≥10 real cases total in evals/cases/real/
    - baseline file evals/baselines/phase3-step-b-baseline.json with pass_rate/median_latency/median_cost
    - methodology-virgil.md §5 marks all 10 spec items DONE
    - methodology-virgil.md Phase 3 status flipped from 🟡 IN PROGRESS → ✅ DONE
    - Backlog «pilot findings» + R1/R2 sections cleared in methodology
  **safety_gates: []
  **checkpoint:** false
  **estimated_retries_allowed:** 3

### Current

- **id:** S3
  **title:** #3 autofix routing policy + #8 subprocess timeout adaptive
  **surface:** backend-python
  **spec_section:** spec/spec_pilot_findings_closure.md §1 #3 + §2 #8
  **depends_on:** [S2]
  **acceptance:**
    - Routing policy YAML loaded; security-critical tag → opus; iter ≥2 → opus
    - Auto-split triggered on WORKER_HALT_FILE with loc_cap_exceeded
    - Default subprocess timeout 3600s; ENV `BMAD_RUNNER_CLAUDE_TIMEOUT_SEC` honoured; adaptive by AC count
    - +9 + +7 = +16 tests
  **safety_gates: []
  **checkpoint:** false
  **estimated_retries_allowed:** 3
  **started:** 2026-05-19 (auto-promoted after S2)
  **workflow:** workflows/backend-python.md
  **retry_count:** 0
  **worker_branches:** []

### Completed

- **id:** S2
  **title:** #2 verdict event runner ↔ orchestrator wiring
  **completed:** 2026-05-19 UTC
  **commit:** 4ac3e565a5e2
  **files_changed:** 6 (2 new + 4 modified; src + tests)
  **tests_passed:** 1892 (was 1883; +9 new spec tests; target +9 ✓)
  **decisions_made:**
    - Chose Variant B (orchestrator-side fallback reader) over Variant A (modify 825-line runner.sh). Reasoning: lower risk, doesn't depend on runner.sh stdout JSON propagation through claude_event parser, easier to test with mock fixtures.
    - New module `runtime/verdict_fallback.py` (single-file, ~80 LOC) — no upstream skill changes.
    - Fallback fires ONLY when spec stage verdict=="error" (no parseable event). Quality stage uses unchanged path — if it returns error after spec approve via fallback, final verdict goes through standard worst-wins logic.
    - Most-recent mtime wins for multi-log resolution — autofix re-review (`<id>-retry-1.log`) supersedes initial review (`<id>.log`). Mirrors runner.sh's `tail -n 5 | grep -Eo | tail -n 1` semantics.
    - Added `source` field to emitted CODE_REVIEW_VERDICT payload (`runner_log_fallback` or `merge_gate_spec`) for observability — fields are passed through bus.emit(**kwargs).
  **deferred_items:**
    - Symmetric fallback for quality stage (low priority — quality-stage 'error' after spec-approve is rare in pilots; current worst-wins+HUMAN_QUERY path is acceptable).

- **id:** S1
  **title:** #1 mark-done ID normalization + #9 spawned/succeeded counter split
  **completed:** 2026-05-18 22:13 UTC
  **commit:** 7edc9bb1c25b6efeb1e434c961e088be406c1331
  **files_changed:** 4
  **tests_passed:** 1883 (was 1860 baseline, +23 incl. +12 new spec tests; target +11)
  **decisions_made:**
    - Added `resolve_sprint_status_key` as a sibling to existing `normalize_story_id` in runtime/bmad_format.py (different semantics — sprint-key lookup vs. canonical dotted) instead of overloading the existing function.
    - Changed `_tail_and_emit_completion` return type from `None` to `str` outcome tag (`completed|failed|halted|silent_failure`) — non-breaking since all existing callers ignore the return.
    - Kept legacy `stories=` key in the `real_pilot_done` log line for backwards-compat alongside new `spawned/succeeded/failed` keys.
    - Mock pilot mark-done loop left unchanged (out of spec scope).
  **deferred_items:**
    - (none)

## Safety Gates Triggered
(none)

## Blockers / Pauses
(none)

## Decisions Log

[2026-05-18 22:13 UTC] S1 — resolver lives in `runtime/bmad_format.py` (not new `agent/story_id.py`) because the file already owns the kebab/dotted regex toolkit; adding `agent/story_id.py` would split related helpers across two modules.

[2026-05-19 UTC] S2 — chose Variant B (orchestrator reads runner log) over Variant A (modify runner.sh stdout). Variant A would require coordinating JSON line format with claude_event parser; Variant B reuses runner's existing on-disk artifact and ships as a single 80-LOC module.

## Journal

[2026-05-19 UTC] bootstrap: tracker created via /auto-loop-spec-long, 8 sessions planned, S1 promoted to Current. Slug=pilot_findings_closure, runtime=loop_wrapper, delay=300s, auto_merge=false.

[2026-05-18 22:13 UTC] S1 done, runtime=loop_wrapper — wrapper handles next iteration. commit=7edc9bb, tests 1883 PASS, ruff+mypy clean. S2 promoted to Current.

[2026-05-19 UTC] S2 done, runtime=loop_wrapper — wrapper handles next iteration. commit=4ac3e56, tests 1892 PASS (+9), ruff clean. Variant B (runner-log fallback) shipped. S3 promoted to Current.

## Final Report
(empty)
