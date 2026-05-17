# Initiative Tracker — Canonical BMad patches port (C/N/Q/R/S/W/X + H)

## Metadata
- **Spec:** spec/spec_canonical_patches_port.md
- **Parent specs:** spec_embed_phase45_with_selflearning.md, spec_embed_phase45_fixes.md
- **Reference:** /home/server/odyssey/spec/handoffs/handoff-bmad-phase4-gaps-2026-05-17.md
- **Reference impl:** ~/.claude/skills/bmad-auto-dev/scripts/bmad-auto-dev-runner.sh (1100 LOC bash)
- **Integration branch:** integration/canonical_patches_port
- **Base branch:** main (post-merge 352a6ab)
- **Backup branch:** backup/canonical_patches_port-pre-2026-05-18
- **Created:** 2026-05-18
- **Bootstrap completed:** 2026-05-18 by auto-loop-spec-long
- **Scope frozen:** 2026-05-18
- **Runtime:** loop_wrapper
- **Delay seconds:** 120
- **Auto merge:** false

## Scope Freeze

### In scope
- **P1** — Patch H (worker timeout 86400s → 1800s) + Patch C (smart deletion check subscriber). Reference: runner.sh lines 91-127 + customize.toml `migration_patterns`.
- **P2** — Patch N (build check guard subscriber: pytest/cargo check ДО code-review spawn).
- **P3** — Patch Q (diff size > 500 lines reject) + Patch R (commit completeness recovery) + Patch S (Stage 5 commit completeness pre-review).
- **P4** — Patch W (Patch R scope by File List allow-list).
- **P5** — Patch X (conditional bmad-security-review 4-hunter parallel for security-critical stories).
- **P6** — Integration + e2e + docs/canonical-patches-architecture.md.
- ~120 new tests, target 1154 PASS.

### Out of scope (deferred)
- Patches D/F/G/I/J/L/M/O/P (full)/Y — nice-to-haves или phase 5 specific, отложены до после первого pilot.
- Patch P (retrospective auto-invoke) — частично уже (наш `wave_boundary_reached` event эмитится); full auto-launch retro отдельной инициативой.
- Patch Y (qa-e2e per-epic) — phase 5 gap, отдельная инициатива.

### Deferred to follow-up initiative
- Phase 5 patches batch (Y, threat-model regen, correct-course).

## Sessions

### Pending

- **id:** P4
  **title:** Patch W (Patch R scope by File List allow-list) (CHECKPOINT)
  **surface:** backend-python
  **spec_section:** 110-125
  **depends_on:** [P3]
  **destructive_actions:** []
  **checkpoint:** true
  **estimated_retries_allowed:** 3
  **acceptance:**
    - New `runtime/file_list_parser.py` — parses `### File List` section из story markdown (`_bmad/stories/<id>.md`)
    - Patch R recovery + Patch Q diff size gate consume File List allow-list:
      - File List ∪ `sprint-status.yaml` ∪ `deferred-work.md` ∪ `<wave>-retrospective.md`
      - Anything outside → reject + escalate
    - Reference: Odyssey memory `~/.claude/projects/-home-server-odyssey/memory/skill_improvement_patch_W_candidate.md`
    - 15 regression tests
    - `pytest tests/ -q` — 1119 PASS

- **id:** P5
  **title:** Patch X (security-review conditional 4-hunter) (CHECKPOINT)
  **surface:** backend-python
  **spec_section:** 130-150
  **depends_on:** [P3]
  **destructive_actions:** []
  **checkpoint:** true
  **estimated_retries_allowed:** 3
  **acceptance:**
    - New subscriber `security_review_subscriber(event, bus)`:
      - On WORKER_COMPLETED (success) AND code_review verdict approve
      - Check triggers from `skills/policy/security-review.yaml`:
        - frontmatter `security_critical: true`
        - epic in `[3, 4, 5, 7, 9, 10]` (configurable)
        - keyword match in story spec OR diff: auth, jwt, rls, dpa, crypto, billing, pii, audit, hmac, argon, session
      - Spawn `claude -p /bmad-security-review` 4-hunter parallel (Injection/Auth-Bypass/Crypto/Data-Leak)
      - Parse verdict: APPROVE / MERGE-WITH-FIXES / BLOCK
      - BLOCK → halt + emit HUMAN_QUERY с findings
      - APPROVE / MERGE-WITH-FIXES → emit SECURITY_REVIEW_PASSED, merge_to_integration_subscriber fires
    - 25 regression tests
    - `pytest tests/ -q` — 1144 PASS

- **id:** P6
  **title:** Integration + e2e + docs (FINAL)
  **surface:** backend-python
  **spec_section:** 155-180
  **depends_on:** [P1, P2, P3, P4, P5]
  **destructive_actions:** []
  **checkpoint:** false
  **estimated_retries_allowed:** 3
  **acceptance:**
    - E2E test: synthetic story → spawn worker → stage5_commit_completeness → build_check → deletion_safety → code_review → security_review (если security-critical) → merge_to_integration. Asserts all gate emit + correct order.
    - `docs/canonical-patches-architecture.md`:
      - Mapping table: our subscribers ↔ Odyssey Patch IDs
      - Port methodology (bash impl + customize.toml → Python subscriber + policy.yaml)
      - Subscriber registration order rationale
      - Trigger configuration examples
    - ~10 final integration tests
    - `pytest tests/ -q` — 1154 PASS
    - ruff/mypy clean
    - Manual merge через human review (Auto merge=false)

### Current

- **id:** P3
  **title:** Patch Q (diff size) + Patch R (commit-completeness recovery) + Patch S (Stage 5 commit) (CHECKPOINT)
  **surface:** backend-python
  **spec_section:** 90-105
  **depends_on:** [P2]
  **destructive_actions:**
    - Patch S может auto-commit untracked файлы в worker'ом branch (защита от data loss)
  **checkpoint:** true
  **estimated_retries_allowed:** 3
  **retry_count:** 0
  **acceptance:**
    - Patch Q: в `code_review_subscriber` pre-merge — `git diff --shortstat HEAD~1 HEAD` → reject if `lines_added + lines_deleted > 500` AND not all paths in File List allow-list. Reference: runner.sh Patch Q.
    - Patch R: extend `merge_to_integration_subscriber` — if `git status --porcelain` shows uncommitted после auto-fix → `git add -A && git commit --signoff -m "Patch R recovery: auto-stage Stage 6.retry residue"`. Reference: runner.sh ~line 821.
    - Patch S: new subscriber `stage5_commit_completeness_subscriber(event, bus)`:
      - On WORKER_COMPLETED (before build_check) → detect uncommitted in worktree
      - Auto `git add` + commit с marker message. Reference: runner.sh ~line 566.
    - Subscriber order в `_run_real_pilot.on(...)`: stage5_commit → build_check → deletion_safety → code_review → merge
    - 30 regression tests
    - `pytest tests/ -q` — 1104 PASS

### Completed

- **id:** P1
  **title:** Patch H (timeout fix) + Patch C (smart deletion check)
  **completed:** 2026-05-18 00:30 UTC
  **commit:** b751e33eeccd92e145e389d85fcd8d878287da93
  **files_changed:** 6
  **tests_passed:** 1057 PASS (was 1034 + 23 new in test_canonical_patches_p1.py)
  **decisions_made:**
    - Patch C subscriber lives in new module `runtime/deletion_safety.py` (not bolted onto `agent/run.py` which is already 2180 LOC).
    - Patch C policy schema is local (DeletionSafetyPolicy in deletion_safety.py), not added to the 3-file PolicyConfig in skills_repo.py — keeps the canonical policy loader untouched.
    - Patch C halts the chain by mutating `event.payload['status'] → 'halted_unsafe_deletion'`. `code_review_subscriber` already short-circuits on `status != 'success'`, so no edits needed there.
    - Patch C subscriber wired FIRST in `_run_real_pilot` bus.on(...) chain (before code_review) — order matters for the payload-mutation gating to work.
    - test_embed_phase45_fixes_f1 subscriber-count assertion updated 3 → 4.
  **deferred_items:** []

- **id:** P2
  **title:** Patch N (build check guard subscriber)
  **completed:** 2026-05-18 01:30 UTC
  **commit:** 87e3c9f
  **files_changed:** 6 (3 new + 3 modified)
  **tests_passed:** 1080 PASS (1057 baseline + 23 new in test_canonical_patches_p2.py)
  **decisions_made:**
    - build_check_subscriber lives in new module `runtime/build_check.py` (same locality pattern as P1's deletion_safety.py — keeps run.py from growing).
    - Same halt-via-payload-mutation contract as P1: `status → 'halted_build_check_failed'` + HUMAN_QUERY emit. No new EventType added — keeps the gating contract consistent across patches.
    - Subscriber registered FIRST in `_run_real_pilot` (before deletion_safety). Final target order from P3 is stage5_commit → build_check → deletion_safety → code_review → merge → quarterly_sweep. Wiring build_check first NOW avoids a re-order in P3.
    - Existing P1 "deletion_safety is first" assertion was relaxed to "deletion_safety precedes code_review" — invariant the halt mutation actually depends on. Both build_check and deletion_safety asserted at indices 0 and 1.
    - test_embed_phase45_fixes_f1 subscriber-count assertion updated 4 → 5.
    - `skip_if_missing_executable=true` default (per policy yaml) — a Python-only worktree must not halt because `cargo` is absent. Required commands with non-zero from missing executables would otherwise spuriously halt every Python-only worker.
    - `_run_command` uses `/bin/sh -c` to match runner.sh's shell-style invocation (and to allow `echo bad; exit 7` test fixtures). stderr is merged into stdout via `STDOUT` redirection so the tail captures both streams.
    - Test count overshot spec estimate (23 vs 17 in tracker, 20 in spec) — coverage of timeout / missing-executable / optional-command / empty-policy paths warranted the extra cases.
  **deferred_items:** []

## Safety Gates Triggered
(none yet)

## Blockers / Pauses
(none yet)

## Decisions Log

- **date:** 2026-05-18 (bootstrap)
  **session:** bootstrap
  **decision:** Port (не design) 8 patches из Odyssey's bmad-auto-dev-runner.sh — production-tested bash code, читаем + port'им к Python subscribers.
  **rationale:** Doc handoff показал 22 patches A-W в их runner. Наш agent — Python event-loop архитектура, не bash sequential. Но семантика patches portable. Reading bash impl + tests'ing с нуля экономит ~50% времени vs design from scratch.
  **impact:** Coverage 5/22 → 13/22. После — pilot ready с full canonical safety net. Phase 5 patches (Y/threat-model/correct-course) отложены — phase 4 critical first.

- **date:** 2026-05-18 (bootstrap)
  **session:** bootstrap
  **decision:** Delay 120s (быстрее обычного 300s) — port sessions меньше по объёму (reading bash → port → tests), не нужно много sleep.
  **rationale:** User asked «лонг 120 с ватчдогом». Каждая session ~15-25 min, total ~2-3 hours.
  **impact:** Faster iteration на patches; watchdog 90min hard ceiling defends from runaways.

- **date:** 2026-05-18 00:30 UTC
  **session:** P1
  **decision:** deletion_safety_subscriber halts via payload mutation (`status → 'halted_unsafe_deletion'`), not via event suppression or new event type.
  **rationale:** `code_review_subscriber` already gates on `payload['status'] == 'success'` — re-using that gate avoids modifying review code. Adding a new EventType would force every existing/future subscriber to learn about the halt protocol.
  **impact:** Downstream patches (P2 build_check, P3 stage5_commit, P5 security) can use the SAME mutation pattern — keep the contract consistent. Subscriber registration order matters: the halter must run FIRST.

- **date:** 2026-05-18 01:30 UTC
  **session:** P2
  **decision:** build_check_subscriber registers FIRST in the bus chain (before deletion_safety), matching final P3 target order (stage5_commit → build_check → deletion_safety → code_review).
  **rationale:** Wiring in final order now avoids a re-order commit in P3. Both halters mutate `payload.status`; downstream subscribers gate on `status == 'success'`. Order between halters doesn't change correctness (either halt skips code_review) but reflects intent: cheap build guard first, more-expensive deletion scan second.
  **impact:** P1 "first" assertion relaxed to "deletion_safety precedes code_review"; P3 stage5_commit will register before build_check (push the two halters down to indices 1-2).

- **date:** 2026-05-18 01:30 UTC
  **session:** P2
  **decision:** `skip_if_missing_executable=true` is the policy default (not `false`).
  **rationale:** A Python-only worktree must not halt because `cargo` happens to be absent from the runner's PATH; the same policy yaml is reused across surface types (Rust / Python / mixed). Required commands with missing executables would otherwise halt every Python-only worker on a `cargo check` row.
  **impact:** Operators must explicitly set `skip_if_missing_executable: false` if they want a missing executable to be fatal (e.g. a Rust-only project's CI yaml).

## Journal

[2026-05-18 bootstrap] bootstrap: tracker + backup + integration branch созданы, 6 sessions planned (P1-P6), runtime=loop_wrapper, delay=120s, auto_merge=false. Reference: Odyssey handoff doc + bmad-auto-dev-runner.sh.
[2026-05-18 00:30 UTC] P1 execution: Patch H + Patch C ported. 23 new tests in tests/test_canonical_patches_p1.py; full suite 1057 PASS; ruff clean; mypy clean on new code. Subscriber count 3→4 in test_embed_phase45_fixes_f1. Commit b751e33.
[2026-05-18 00:30 UTC] P1 completed → Completed; P2 promoted to next-up (subscriber count 4→5 will land in P2 via build_check_subscriber). Runtime=loop_wrapper — wrapper handles next session iteration; this invocation exits clean.
[2026-05-18 01:30 UTC] P2 execution: Patch N (build_check_subscriber) ported. New `runtime/build_check.py` + `skills/policy/build-check.yaml` (pytest + ruff baseline). 23 new tests in tests/test_canonical_patches_p2.py; full suite 1080 PASS (target was 1074, +6 over plan); ruff clean; mypy --strict clean on new module. Subscriber count 4→5; build_check wired BEFORE deletion_safety so cheap pytest+ruff guard halts before the deletion scan. P1's "deletion_safety first" assertion relaxed to "precedes code_review". Commit 87e3c9f.
[2026-05-18 01:30 UTC] P2 completed → Completed; P3 promoted to Current (Patch Q + Patch R + Patch S — diff size cap + auto-commit recovery + Stage 5 completeness pre-review). Runtime=loop_wrapper — wrapper handles next session iteration; this invocation exits clean.

## Final Report (populated on last session completion)
(empty — P6 will populate)
