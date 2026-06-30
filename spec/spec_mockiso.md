# Spec: Q-260527-MOCKISO — fix worktree prepare gate divergence

**Parent:** Q-260527-WTISO-WT (closed §4fo)
**Type:** bug-fix-S-tier
**Tier:** simple (1 file, ~20-40 LOC diff)
**Security_critical:** false
**Author:** 888-persona-architect 2026-05-27
**Brief:** methodology-888.md §4fp

## 1. Problem

`_spawn_worker` (888-batch.sh:562-582) expects `/tmp/888-bat-<batch>/<q_id>` worktree directory to exist when `_should_use_isolation` returns 0. `_prepare_worktrees` (line 433) is called only inside `if [ -n "$_GROUPS_JSON" ] && [ "$_BATCH_PARALLEL" = "1" ]` (line 1260). When spec lacks `parallel-groups` JSON block (case: 4×HKQM spec) or `_parse_spec_parallel_groups` silently fails (line 1257 `|| echo ""`), prepare is skipped → all workers fail exit 78 "prepare not called?".

Live repro confirmed §4fp.6 (2026-05-27T04:38).

## 2. Design — Pattern P1 linear (refined Option B)

Hoist `_prepare_worktrees` + EXIT trap out of parallel-only branch to top-level `_cmd_run_from_spec` body, gated by single `_should_use_isolation` call. Parallel branch's inner duplicate check + explicit cleanup become redundant (removed for symmetry).

**Why P1 not P3/P4:** single bash patch in single function — no orchestration. Anthropic patterns apply to LLM call graphs; this is procedural code.

**Decision tree applied:**
- Q1 multi-decision dependency? **no** → P1 linear ✅
- Field 0 complexity: **simple** (auto-classify heuristic #3 — known pattern, 1 verb «fix», no new deps)

## 3. Diff sketch (REVISED v2 after edge-case review FAIL — 2026-05-27T05:05Z)

**Target file:** `~/.claude/skills/888/scripts/888-batch.sh`

**Revision summary (review issues addressed):**
- [A4] **Composite trap** — modify existing trap at line 1228-1233, NOT separate `trap` call (was silent flock+watchdog leak regression)
- [A1] Explicit handling of `_should_use_isolation` rc values 0/1/79/* (no silent fallthrough)
- [E15] `worktrees_cleaned` event preserved — emitted from composite trap path
- [E14] Implementer pre-flight: read `test-sequential-mode-unchanged.sh` env before claim pass
- [A2] `sort -u` dedup on `q_ids_arr` before prepare
- [A3] `|| true` on 79-branch audit calls (graceful degradation)
- [D11] `total_q=0` short-circuit (skip prepare, no halt)
- [F17] batch_id format pre-assertion (defensive printf %q)
- [B6] Cleanup BEFORE flock-release in composite trap (avoid lockless cleanup window)

**Location 1 — `_cmd_run_from_spec` body — MODIFY existing trap block at lines 1226-1233:**

```bash
  # Q-260527-MOCKISO: composite EXIT trap (review A4 fix) — single trap
  # combines worktree cleanup + flock release + watchdog kill + done flag.
  # ORDER critical: cleanup BEFORE flock-release (review B6) to avoid lockless
  # cleanup window where concurrent rerun could observe partial state.
  _TBEX_WATCHDOG_PID=""
  _TBEX_DONE_FLAG=""
  _MOCKISO_BATCH_ID="$batch_id"   # captured for trap (single-quote-safe)
  _MOCKISO_USE_ISO=0              # set below by hoisted gate
  trap '
    # 1. Worktree cleanup (only if isolation was active for this run)
    if [ "${_MOCKISO_USE_ISO:-0}" = "1" ] && [ -n "${_MOCKISO_BATCH_ID:-}" ]; then
      _cleanup_worktrees "${_MOCKISO_BATCH_ID}" 2>/dev/null || true
      # E15: preserve worktrees_cleaned event emission (was at line 1296-1297)
      batch_event_log "${_MOCKISO_BATCH_ID}" "worktrees_cleaned" \
        "$(jq -cn --arg b "${_MOCKISO_BATCH_ID}" "{batch_id:\$b}" 2>/dev/null)" 2>/dev/null || true
    fi
    # 2. Existing trap body (preserved verbatim, order unchanged)
    flock -u 200 2>/dev/null || true
    exec 200>&- 2>/dev/null || true
    [ -n "${_TBEX_WATCHDOG_PID:-}" ] && kill -TERM "${_TBEX_WATCHDOG_PID}" 2>/dev/null || true
    [ -n "${_TBEX_DONE_FLAG:-}" ] && touch "${_TBEX_DONE_FLAG}" 2>/dev/null || true
  ' EXIT
```

**Location 2 — `_cmd_run_from_spec` body, AFTER `q_ids_arr=…` array declaration (~line 1247), BEFORE worker loop:**

```bash
  # Q-260527-MOCKISO: hoist _prepare_worktrees + isolation gate from parallel-only branch.
  # Was: prepare called only when _GROUPS_JSON non-empty (line 1260 pre-fix).
  # Now: prepare called for ALL specs when isolation active — fixes exit 78 for
  # non-grouped specs (4×HKQM repro §4fp.6).

  # D11: short-circuit if no Q-NNNs (empty spec) — preserve pre-fix no-op
  if [ "$total_q" -gt 0 ]; then
    local _iso_top_rc=0
    _should_use_isolation || _iso_top_rc=$?

    # A1: explicit rc dispatch — no silent fallthrough
    case "$_iso_top_rc" in
      0)
        _MOCKISO_USE_ISO=1
        ;;
      1)
        _MOCKISO_USE_ISO=0   # parallel=off OR explicit unsafe escape — no isolation
        ;;
      79)
        echo "888-batch: hard-fail invariant — refusing run без isolation" >&2
        _audit_update "$audit_file" \
          '.runtime_status = "halted" | .halt_reason = "isolation_bypass_blocked"' 2>/dev/null || true
        batch_event_log "$batch_id" "isolation_bypass_blocked" \
          "$(jq -cn --arg b "$batch_id" '{batch_id:$b, reason:"BATCH_ISOLATION_ENABLED=0 без --allow-unsafe-parallel"}' 2>/dev/null)" 2>/dev/null || true
        return 79
        ;;
      *)
        # Unknown rc (SIGKILL=137, SIGTERM=143, future codes) — fail closed
        echo "888-batch: _should_use_isolation returned unknown rc=$_iso_top_rc — failing closed" >&2
        _audit_update "$audit_file" \
          '.runtime_status = "halted" | .halt_reason = "isolation_unknown_rc"' 2>/dev/null || true
        return 78
        ;;
    esac

    if [ "$_MOCKISO_USE_ISO" = "1" ]; then
      # F17: batch_id format pre-assertion (defensive)
      case "$batch_id" in
        *[!a-zA-Z0-9_-]*)
          echo "888-batch: batch_id contains unsafe chars: $batch_id" >&2
          return 78
          ;;
      esac

      # A2: dedup q_ids before prepare (idempotent on duplicates но avoid wasted git ops)
      local _unique_q_ids
      mapfile -t _unique_q_ids < <(printf '%s\n' "${q_ids_arr[@]}" | sort -u)

      echo "  Preparing ${#_unique_q_ids[@]} worktrees (isolation=on)..."
      if ! _prepare_worktrees "$batch_id" "${_unique_q_ids[@]}"; then
        echo "888-batch: _prepare_worktrees failed для $batch_id" >&2
        _audit_update "$audit_file" \
          '.runtime_status = "halted" | .halt_reason = "worktree_prepare_failed"' 2>/dev/null || true
        # _MOCKISO_USE_ISO=1 already → composite trap fires _cleanup_worktrees (idempotent EC5)
        return 78
      fi
      batch_event_log "$batch_id" "worktrees_prepared" \
        "$(jq -cn --arg b "$batch_id" --argjson n "${#_unique_q_ids[@]}" '{batch_id:$b, count:$n}' 2>/dev/null)" 2>/dev/null || true
    fi
  fi
```

**Location 3 — `if [ -n "$_GROUPS_JSON" ] && [ "$_BATCH_PARALLEL" = "1" ]` branch (lines 1260-1298):**

DELETE the inner duplicate prepare block (lines 1262-1285) + post-group cleanup (lines 1293-1298). Composite trap (Location 1) + hoisted prepare (Location 2) handle both. The `worktrees_cleaned` event is now emitted in the trap when isolation was active for the run.

Keep ONLY:
```bash
  if [ -n "$_GROUPS_JSON" ] && [ "$_BATCH_PARALLEL" = "1" ]; then
    _run_parallel_groups "$audit_file" "$batch_id" "$_GROUPS_JSON" "$_BATCH_MAX" \
      "$per_q_budget" "$log_dir" "$mock_mode" "$pause_sec" "$total_q" "$pids_file"
    any_failed=$?
    q_index=$total_q
    goto_finalize=1
  else
    goto_finalize=0
  fi
```

**Net diff:** ~+50 LOC Location 1+2, ~−30 LOC Location 3 → net +20 LOC, 1 file.

## 4. Acceptance criteria

**Primary:** `bash tests/wtiso/test-mockiso-sequential-prepare.sh` exits 0 (currently absent — implementer creates as RED test).

**Live repro (manual after fix):**
```bash
bash ~/.claude/skills/888/scripts/batch-isolation-flag.sh on
rm -f ~/.claude/skills/888/locks/batch-run.lock
timeout 30 env BMAD_888_BATCH_ENABLED=on BATCH_888_MOCK_RUNNER=1 \
  MOCK_RUNNER_DURATION_SEC=0 MOCK_RUNNER_VERDICT=pass,pass,pass,pass \
  MOCK_RUNNER_EXIT_CODES=0,0,0,0 BMAD_888_BATCH_PAUSE_SEC=1 \
  bash ~/.claude/skills/888/scripts/888-batch.sh run \
  --from-spec spec/spec_batch_hkhk-stop-hook-blockers_5cea572ae82e.md
bash ~/.claude/skills/888/scripts/batch-isolation-flag.sh off
```

Expected:
- `exit 0` overall
- 4 lines `Q-260525-HKQM*: done` (not «failed (exit 78)»)
- assert `jq '.per_q_exit_code | values | all(.==0)' audit/batches/<batch>/result.json` → true
- `audit/batches/<batch>/events.jsonl` contains `worktrees_prepared` event
- `/tmp/888-bat-batch-hkhk-stop-hook-blockers-*` directories created during run, **removed on exit** (EXIT trap)
- 0 stderr lines matching `"prepare not called"`

**Regression guard:** All `tests/wtiso/` tests pass before AND after (architect IMPORTANT enumeration).

**Pre-flight verification (review [E14]):** implementer MUST `grep BATCH_PARALLEL_ENABLED ~/.claude/skills/888/scripts/tests/wtiso/test-sequential-mode-unchanged.sh` to confirm test sets parallel=off (otherwise composite trap path semantics change → may need test update too).
- `test-spawn-uses-worktree-path.sh` · `test-worktree-prepare-and-cleanup.sh` · `test-spawn-without-worktree-races.sh` · `test-sequential-mode-unchanged.sh` · `test-baseline-race-rate.sh`
- `qa-edge-01..10*` (10 файлов)
- Run command: `bash ~/.claude/skills/888/scripts/tests/wtiso/run-all.sh`

## 5. RED tests (for implementer)

### Primary RED — Q-260527-MOCKISO scope

**File:** `~/.claude/skills/888/scripts/tests/wtiso/test-mockiso-sequential-prepare.sh`

**Asserts:**
1. Env `BATCH_PARALLEL_ENABLED=on` (from .env default) + `BATCH_ISOLATION_ENABLED=on` (toggled via flag) + `BATCH_888_MOCK_RUNNER=1` + 4×HKQM spec
2. Exit code 0
3. `per_q_exit_code` JSON path в `audit/batches/<batch>/result.json`: all 4 → `0`
4. 0 stderr lines matching `prepare not called`
5. `audit/batches/<batch>/events.jsonl` contains 1× `worktrees_prepared` event with count=4
6. After run: `/tmp/888-bat-batch-hkhk-stop-hook-blockers-*` does NOT exist (cleanup via EXIT trap)

**RED protocol:** test MUST FAIL on commit BEFORE fix. After fix → MUST PASS. Implementer commits RED first, then GREEN.

### Negative-scenario edge tests (review IMPORTANT #4, deferred to qa Phase 3)

NOT in MOCKISO scope — but flagged for qa:
1. Crash между prepare и spawn → orphan worktree cleanup verification (EXIT trap fires on SIGTERM)
2. `_prepare_worktrees` partial success (3/4 created, 4-й fails) → behavior? Fail-fast per current line 462-464 (return 78 inside loop). Verify.
3. Idempotency: rerun same batch_id после kill -9 — orphan handled? (cross-link Q-260527-LOCK out-of-scope)

## 6. Threat model placeholder (Phase 2 baseline)

**3 vectors (architect-level):**

1. **Tampering — env injection to bypass isolation**
   - Vector: attacker sets `BATCH_ISOLATION_ENABLED=on` in user env, but spec injects malicious path-like `q_ids` causing `_validate_worktree_path` bypass
   - Mitigation: existing `_validate_worktree_path` (line 372-407) realpath canonicalize + prefix check + symlink reject — unchanged by this fix. Hoisting doesn't open new attack surface (same validator, same prefix gate).

2. **Information disclosure — leaked worktrees in /tmp**
   - Vector: crash between prepare and EXIT trap → worktree files left in `/tmp/888-bat-*` with Q-NNN code visible to other tmpwatch users
   - Mitigation: base dir created with `chmod 0700` (line 446 — preserved). EXIT trap added unconditionally at top level (Section 3 Location 1) — runs on all exit paths (clean exit, error return, signal SIGTERM/SIGINT — traps inherited by trap mechanism). For SIGKILL (-9) — no trap fires, leak possible → cross-link Q-260527-LOCK + future tmpwatch cron.

3. **DoS — disk fill via failed prepare loop**
   - Vector: large batch (50+ Q-NNN) + slow disk → `_prepare_worktrees` `git worktree add` per Q takes O(N×latency); on disk-full mid-loop → returns 78 with some worktrees already created, EXIT trap cleans up
   - Mitigation: EXIT trap (Section 3) handles partial cleanup. Preflight `df` check NOT added (S-tier scope) — flag as Q-260527-WTPRE for future improvement if observed.

**Phase 3 qa coverage assignment:** vectors 2 + 3 → explicit edge tests (vector 1 already covered by existing `qa-edge-02-partial-cleanup-recovery.sh` + path-traversal tests).

## 7. Rollback plan

**Reversion checkpoint:** commit `de9d9ad` (last working WTISO-WT layer pre-MOCKISO).

**If MOCKISO fix introduces regression:**
1. Detection: `tests/wtiso/run-all.sh` shows >0 regressions vs baseline-test-stack-20260526.json
2. Hard rollback (immediate): `bash ~/.claude/skills/888/scripts/batch-isolation-flag.sh off` → restores Stage 0 soak (current state); MOCKISO fix dormant.
3. Code revert: `git revert <mockiso-fix-sha>` + re-park MOCKISO с note about regression.
4. **No state cleanup required** — `/tmp/888-bat-*` self-cleans via EXIT trap; no DB writes.

## 8. Out-of-scope

Per analyst §4fp.5:
- WTISO-BW, WTISO-SH (sibling sub-Q under WTISO umbrella)
- MOCKCOV (Q-260527-WTISO-MOCKCOV — comprehensive matrix test, dep:MOCKISO)
- AGGR (Q-260527-AGGR — aggregate counter bug; review IMPORTANT noted: AC asserts per_q JSON not aggregate)
- LOCK (Q-260527-LOCK — orphan lock on kill -9)
- QIDR (Q-260527-QIDR — Q-ID regex extension)
- WTPRE (new, parked: preflight df check для prepare)

## 9. T1/T7 storm decisions

- **T1 (feature_intent):** SKIPPED — bug fix, not feature creation. Storm scenarios T1 require «namespace/slug for new feature» — N/A here.
- **T7 (bug_fix_counter):** evaluated. Scope `scripts/` last 14d: 17 fix commits — **above** 5-commit threshold. Narrow scope (wtiso/batch only): 1 fix commit (de9d9ad before-this) — **below** threshold. Architect decision: T7 storm SKIPPED for narrow scope; **but** flag park follow-up `Q-260527-T7AUDIT` для improver Phase 5 retro consideration (wide scope=17 hints at potential pattern; analyse cluster в next retro).

## 10. Handoff payload

```yaml
handoff:
  to: 888-persona-implementer (via dispatcher §7.6)
  from: 888-persona-architect 2026-05-27T04:55Z
  q_id: Q-260527-MOCKISO
  parent_q: Q-260527-WTISO-WT (closed §4fo)
  parked_alias: Q-260527-WTISO-WT-MOCKISO (§5 active line 7500)
  complexity: simple
  tier: S
  security_critical: false
  pattern: P1-linear (procedural bash patch)
  diff_target: ~/.claude/skills/888/scripts/888-batch.sh
  diff_locations:
    - lines ~1248-1260 — hoist prepare+trap (~25 LOC add)
    - lines 1260-1298 — remove duplicate block (~25 LOC delete)
  net_diff: ±25 LOC (1 file)
  red_test:
    file: ~/.claude/skills/888/scripts/tests/wtiso/test-mockiso-sequential-prepare.sh
    asserts: see §5
    iron_law: commit RED first → must FAIL → then fix → commit GREEN → must PASS
  regression_guard: bash scripts/tests/wtiso/run-all.sh ДО+ПОСЛЕ
  rollback: §7
  threat_model: §6
  spec: spec/spec_mockiso.md (this file)
  next_persona: 888-persona-qa (Phase 3 — edge tests + verify negative scenarios §5b)
```
