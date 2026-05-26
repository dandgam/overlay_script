---
q_id: Q-260527-WTISO-WT
parent: Q-260527-WTISO (umbrella)
tier: M
phase: 2
status: architect-done · pending-implementer
security_critical: true
pattern: P3-parallel + P4-decompose (deterministic bash, no LLM в WT-layer)
complexity: complex
effort_estimate: ~3h (M-tier)
created_at: 2026-05-27
author: 888-persona-architect (R7 substitute)
analyst_gate: 2026-05-27 commit 0f64f3e (§4fj)
threat_model: spec/threat-model_wtiso-wt.md (verdict PASS)
edge_case_hunter: pending Step 5
dep: —
blocks: [Q-260527-WTISO-BW, Q-260527-WTISO-SH]
---

# Spec — Q-260527-WTISO-WT: Worktree-per-Q-NNN Isolation Layer

> Worktree subset of L3 sandbox для 888 parallel batch system. Цель — закрыть `.git/index` race, branch-hijack, dead `--worktree` arg wiring. Bwrap / cgroup / HOME-overlay / shards = OUT OF SCOPE (see §7).

## §1 Context (analyst §4fj summary)

**Pain:** `_spawn_worker` (`~/.claude/skills/888/scripts/888-batch.sh:335-459`) запускает все workers в shared CWD без `git worktree add`. Все воркеры shared `.git/index` → race на любой concurrent commit. Защищает только flock на ВЕСЬ batch (fd 200), внутри batch'а race открыты. `--worktree` arg в `888-batch-headless-prompt.sh:29` и `888-batch-commit-policy.sh:46` — dead code (парсится, не вызывается из `_spawn_worker`).

**Allocation:** 27 adversarial findings split → WT=8, BW=13, SH=6. Этот spec покрывает только WT-8.

## §2 Architecture — 7-field

| F | Field | Decision |
|---|---|---|
| F0 | Complexity classification | **complex** — security_critical + new sandbox pattern + modifies multi-file batch system + interacts with feature-flag gating |
| F1 | Single LLM? | **No.** Pure deterministic bash. LLM-calls happen внутри worker (per-Q), но WT-layer = harness around them. |
| F2 | Anthropic pattern | **P3 parallel + P4 decompose.** Worker per Q-NNN (decompose), parallel batch (P3). Worktree = isolation primitive that makes P3 safe. |
| F3 | Memory | **Session-only.** Worktree state на `/tmp/888-bat-<batch-id>/`. Cleanup post-batch (no persistent memory beyond audit/events.jsonl which lives на проектной FS). |
| F4 | Tools | **5:** `git worktree`, `git checkout` (scoped), `flock` (batch prepare lock), `realpath` (path validation), bash builtins. Hard-cap ≤5. |
| F5 | Multi-LLM routing | **N/A.** Нет LLM calls в WT-layer itself. |
| F6 | Threat-model top-3 attack vectors | (1) `.git/index` race [T1] → `git worktree add` per Q-NNN. (2) Branch hijack `git checkout main` [E1] → PreToolUse hook + scope assertion. (3) Worktree path traversal `--worktree '/tmp/../etc'` [T3] → `_validate_worktree_path` with `realpath` prefix check. Full STRIDE: see `spec/threat-model_wtiso-wt.md`. |
| F7 | RAG? | **No.** No knowledge retrieval needed — bash logic + git primitives. |

## §3 Implementation plan

### 3.1 Files modified

| File | Change |
|---|---|
| `~/.claude/skills/888/scripts/888-batch.sh` | + `_prepare_worktrees`, `_cleanup_worktrees`, `_validate_worktree_path`, `_assert_branch_scope`; modify `_spawn_worker` lines 335-459 |
| `~/.claude/skills/888/scripts/888-batch-headless-prompt.sh` | wire `--worktree` from `_spawn_worker` (currently dead) |
| `~/.claude/skills/888/scripts/888-batch-commit-policy.sh` | wire `--worktree` from `_spawn_worker` (currently dead) |
| `~/.claude/skills/888/config/.env` | + `BATCH_ISOLATION_ENABLED=1` flag (default on after merge; off during baseline measurement) |
| `~/.claude/hooks/888-batch-branch-scope.sh` | NEW — PreToolUse hook (rejects `git checkout main` / switch outside `integration/<q-id>`) |
| `~/.claude/skills/888/scripts/tests/wtiso/` | NEW directory — 4 RED tests (see §5) |

### 3.2 New functions (888-batch.sh)

```bash
# _prepare_worktrees BATCH_ID Q_IDS[]
#   For each Q-NNN: git worktree add /tmp/888-bat-<batch-id>/<q-id> integration/<q-id>
#   Uses flock on /tmp/888-bat-<batch-id>/.prepare.lock to prevent same-batch-id collision (T5).
#   Hard-fails if BATCH_PARALLEL_ENABLED=1 AND BATCH_ISOLATION_ENABLED=0 (E4 anti-bypass).
#   Records `worktrees_prepared` audit event with paths array.
_prepare_worktrees() { ... }

# _spawn_worker (modified, lines 335-459)
#   NEW: receives `worktree_path` arg (required when ISOLATION_ENABLED=1).
#   NEW: cd "$worktree_path" before claude -p invocation.
#   NEW: passes --worktree "$worktree_path" to headless-prompt + commit-policy.
#   NEW: post-worker _assert_branch_scope (verify still on integration/<q-id>).

# _cleanup_worktrees BATCH_ID [--force]
#   Runs in EXIT trap of batch.
#   For each `/tmp/888-bat-<batch-id>/<q-id>`: git worktree remove --force.
#   git worktree prune post-loop.
#   Records `worktrees_cleaned` audit event.

# _validate_worktree_path PATH BATCH_ID
#   resolved=$(realpath -m "$PATH")  # -m allows non-existent (during prepare)
#   expected_prefix="/tmp/888-bat-${BATCH_ID}/"
#   Reject (exit 78, audit event `worktree_path_rejected`) if:
#     - resolved !~ ^${expected_prefix}
#     - resolved contains `/../` after canonicalization
#     - resolved is symlink to outside-scope

# _assert_branch_scope WORKTREE_PATH Q_ID
#   cd "$WORKTREE_PATH"; cur=$(git branch --show-current)
#   Reject (exit 80, audit event `branch_scope_violation`) if cur != "integration/${Q_ID}"

# (new hook file ~/.claude/hooks/888-batch-branch-scope.sh)
#   PreToolUse hook on Bash tool when BATCH_888_DEPTH=1.
#   Patterns rejected:
#     - `git checkout main` / `git checkout master` / `git switch main`
#     - `git checkout <branch>` where <branch> != $BATCH_888_Q_BRANCH
#     - `git push --force` (cross-cutting, but cheap to guard here)
```

### 3.3 Feature flag — `BATCH_ISOLATION_ENABLED`

- Default after merge: `1`
- During baseline measurement run (§5 test M0): force-set to `0`
- Hard-fail invariant: `BATCH_PARALLEL_ENABLED=1` + `BATCH_ISOLATION_ENABLED=0` → exit 79 в `_prepare_worktrees`, audit event `isolation_bypass_blocked`. Explicit override requires `--allow-unsafe-parallel` CLI flag (NOT recommended).

Sequential mode (`BATCH_PARALLEL_ENABLED=0`) — no worktree code path invoked at all (analyst AC (e) M4).

### 3.4 Dead-code wiring (analyst's primary callout)

Both consumer scripts already parse `--worktree`:
- `888-batch-headless-prompt.sh:29` → `WORKTREE="$2"` (then used in template substitution line 51)
- `888-batch-commit-policy.sh:46` → `WORKTREE="$2"`

`_spawn_worker` currently does NOT pass this arg → dead. Fix:
```bash
# In _spawn_worker after worktree resolution:
local worktree_path="$WORKTREES_BASE/$q_id"
# real runner:
claude -p "$prompt" --worktree "$worktree_path"  # passed via env or wrapper
# commit-policy invocation:
"$SCRIPTS_DIR/888-batch-commit-policy.sh" --worktree "$worktree_path" --batch-id "$batch_id" --q-id "$q_id" ...
```

## §4 STRIDE Threat Model

Full table в `spec/threat-model_wtiso-wt.md`. Summary:
- **PASS verdict**, no NEEDS-REVISION blockers
- **8 items closed by WT-layer** (T1/E1/E2/T3/T4/T5/D3/E4 — exactly matching analyst's WT=8 allocation)
- **Honest gaps documented:** T2/I1/I2/D1 require WTISO-BW (bwrap + HOME overlay + cgroup); R1/S1 already covered by existing commit-policy/dispatcher.
- **Top-3 sev-5:** T1 .git/index race, E1 branch hijack, E4 feature-flag bypass — all mitigated на WT.

## §5 Iron Law RED tests (committed BEFORE implementation)

| Test | File | Purpose | Verdict pre-WT |
|---|---|---|---|
| **M0 baseline** | `tests/wtiso/test-baseline-race-rate.sh` | Run 4 mock workers BATCH_PARALLEL_ENABLED=1 BATCH_ISOLATION_ENABLED=0 (force-off) × 10 iter; count `.git/index.lock` errors in audit. Establishes baseline race rate ≥1/iter (addresses adversarial #27 / #4). | RED — should show races; if 0 races → bug в test reproduction itself |
| **M1 spawn races** | `tests/wtiso/test-spawn-without-worktree-races.sh` | 2 parallel `_spawn_worker` invocations in shared CWD → assert `.git/index.lock` error occurs | RED until WT applied |
| **M2 traversal** | `tests/wtiso/test-worktree-path-traversal-rejected.sh` | `--worktree '/tmp/../etc'` → expect exit 78 + `worktree_path_rejected` event | RED until validation lands |
| **M3 cleanup** | `tests/wtiso/test-worktree-prepare-and-cleanup.sh` | Full prepare→spawn→cleanup; assert `git worktree list` 0 leaked entries post-batch | RED until cleanup trap lands |
| **M4 sequential unchanged** | `tests/wtiso/test-sequential-mode-unchanged.sh` | `BATCH_PARALLEL_ENABLED=0` → no worktree code path invoked, exit codes match pre-WTISO baseline | RED until conditional gating lands |
| **M5 dead-code live** | `tests/wtiso/test-commit-policy-worktree-live.sh` | Spawn worker; assert commit-policy invocation log contains `--worktree /tmp/888-bat-*` arg (proves wiring live) | RED until wired |
| **M6 branch hijack** | `tests/wtiso/test-branch-hijack-rejected.sh` | Worker attempts `git checkout main` → PreToolUse hook reject + audit event `branch_scope_violation` | RED until hook installed |
| **M7 isolation-bypass guard** | `tests/wtiso/test-isolation-bypass-guard.sh` | BATCH_PARALLEL_ENABLED=1 + BATCH_ISOLATION_ENABLED=0 + no --allow-unsafe-parallel → exit 79 | RED until invariant lands |

Total: 4 baseline-required (analyst minimum) + 4 additional. M0+M1+M2+M4 = strict analyst RED-test mandate; M3/M5/M6/M7 added to close adversarial #25/#27 + threat-model E1/E4.

## §6 Acceptance criteria (analyst's 5 AC verbatim)

- **(a)** 4-worker synthetic × 10 iter → 0 `.git/index.lock` errors в audit (M1 GREEN после WT)
- **(b)** `tests/wtiso/test-worktree-prepare-and-cleanup.sh` GREEN (M3)
- **(c)** `tests/wtiso/test-spawn-uses-worktree-path.sh` GREEN — `_spawn_worker` cd's в worktree before `claude -p`
- **(d)** `tests/wtiso/test-commit-policy-worktree-live.sh` GREEN (M5)
- **(e)** `tests/wtiso/test-sequential-mode-unchanged.sh` GREEN (M4)

Plus implicit (from threat model + adversarial closure):
- (f) M0 baseline establishes race count ≥1/iter (proves T1 was real, addresses #27)
- (g) M6 branch-hijack rejected (closes E1/E2)
- (h) M7 isolation-bypass guard reject (closes E4)

## §7 Out-of-Scope (explicit, defer to BW/SH)

**To Q-260527-WTISO-BW:**
- bwrap sandbox wrapper (`runtime/sandbox.py` analog в bash)
- `--clearenv` + 6 env vars allowlist
- network policy (no-net by default)
- cgroup limits via `systemd-run --user --scope -p TasksMax=...`
- HOME overlay (per-worker `~/.claude/` snapshot)
- blackout paths (~30 entries из Virgil)
- NoSandbox fallback с auto-downgrade в sequential + loud audit warn
- supply-chain blocks
- prlimit fallback

**To Q-260527-WTISO-SH:**
- methodology shard'ы (`.shards/<batch-id>/<q-id>.md`)
- deterministic merger (alphabetic Q-NNN ordering для §4 numbering)
- idempotency contract (re-run = noop с warning)
- per-Q patch-counter extension
- per-Q merge gate с 3-layer check
- rollback contract on per-Q merge failure

**Cross-cutting (NOT touched here):**
- author repudiation (R1) — already enforced by commit-policy
- q_id spoofing (S1) — already enforced by dispatcher (read-only env)
- Storm T1 spec re-run with `STORM_LLM_BACKEND=claude-p` — analyst recommendation, not WT-architect responsibility (deferred to dispatcher если retro requires)

## §8 Cost

**N/A.** No LLM-calls в WT-layer itself. Workers внутри (per-Q claude -p) — already costed by analyst as part of per-Q budget cap. WT-layer adds ZERO new LLM cost.

## §9 Open questions для implementer

Inherited from analyst's open Q + architect's additions:

1. **Worktree base branch:** worktree based on `main` HEAD at batch start OR per-batch `integration/<batch-id>` already created?
   - **Architect recommendation:** base on `main` HEAD captured at batch start (snapshot via `git rev-parse main`); WT-layer creates `integration/<q-id>` worktree from that SHA. Merge topology = SH responsibility.
2. **Branch scope hook — block all checkout, or allow same `integration/<q-id>`?**
   - **Architect recommendation:** allow checkout to same `integration/<q-id>` (worker may need recovery operations); block everything else. Pattern: `^git (checkout|switch) (?!integration/${BATCH_888_Q_ID}$)`.
3. **Cleanup on failure:** `git worktree remove --force` always, or preserve for debugging?
   - **Architect recommendation:** preserve если `BATCH_DEBUG_PRESERVE_WORKTREES=1` env set; default = force remove. Preserve mode logs path + manual cleanup command.
4. **Storm T1 backend re-run:** analyst's open Q.
   - **Architect verdict:** NOT WT responsibility. Defer to dispatcher post-merge retro. WT does NOT block on it; T11 regression storm already covers pipeline-bypass pattern.
5. **Hook installation scope:** `~/.claude/hooks/888-batch-branch-scope.sh` is user-global. Should it activate ONLY when `BATCH_888_DEPTH=1`?
   - **Architect recommendation:** YES — gate on `[ "${BATCH_888_DEPTH:-0}" = "1" ]` early-return. Avoids interfering with interactive `claude -p` sessions outside batch.
6. **Path canonicalization on macOS:** `realpath -m` GNU-only; macOS uses different flag.
   - **Architect recommendation:** detect via `uname -s`; use `realpath` (GNU) или `python3 -c "import os; print(os.path.realpath(...))"` fallback. Documented in implementation.

## §10 Edge-case-hunter findings (review-gate, 2026-05-27)

**Verdict: PASS** — 10 findings, 0 BLOCKER. All findings = implementation-tightening guidance для Phase 2.5 implementer.

| # | Location | Trigger | Guard sketch | Consequence |
|---|---|---|---|---|
| EC1 | `_prepare_worktrees` | Worktree path already exists (re-run after crash) | `if [ -d "$wt_path" ]; then git worktree remove --force "$wt_path" \|\| rm -rf "$wt_path"; fi` | `git worktree add` fails on retry; batch aborts |
| EC2 | `_validate_worktree_path` | PATH symlink resolves outside batch dir | `[ "$(readlink -f "$resolved")" = "$resolved" ]` + reject any symlink in path | `realpath -m` won't resolve symlinks for non-existent paths; pre-planted symlink bypass |
| EC3 | `_spawn_worker` cd | `cd "$worktree_path"` fails (FS unmount / perms) | `cd ... \|\| { audit worktree_cd_failed; exit 81; }` | Worker silently uses old CWD on shared `.git/index` — exact bug WT prevents |
| EC4 | `BATCH_ISOLATION_ENABLED` parse | Value `'1 '` / `'true'` / `'yes'` (non-numeric) | Explicit `case` matching truthy tokens; reject ambiguous | Truthy-string treated as 0 silently → silent regression |
| EC5 | `_cleanup_worktrees` EXIT trap | Trap fires twice (nested traps / signal during cleanup) | `[ -n "${_CLEANUP_DONE:-}" ] && return 0; _CLEANUP_DONE=1` | Double `git worktree remove` → spurious errors; prune race |
| EC6 | `_assert_branch_scope` | Worker legitimate detached HEAD mid-rebase | `cur=$(git rev-parse --abbrev-ref HEAD); [ "$cur" = "HEAD" ] && skip_assert` | `--show-current` returns empty on detached HEAD → assert fails on legit recovery |
| EC7 | claude -p --worktree | `claude -p` may not accept `--worktree` natively | Pass via env `BATCH_WORKTREE_PATH=...`; let prompt template substitute (existing 888-batch-headless-prompt.sh:51) | If passed as CLI arg to claude -p → exit 2; worker fails |
| EC8 | M0 baseline test | Host has low natural race rate (fast SSD + few workers) → 0 baseline | Increase concurrency to 8; add `fsync` barrier; PASS = ≥1 race in ≥1 of 10 iters | Adversarial #27 unaddressed if baseline can't reproduce |
| EC9 | batch_id collision | timestamp+random collision на rapid-fire batches | Include PID: `<timestamp>-<rand>-<pid>` | Two concurrent batches same id → T4 prefix-check bypass |
| EC10 | hook env propagation | `BATCH_888_Q_BRANCH` not exported to claude -p subprocess | Explicit `export BATCH_888_Q_ID BATCH_888_Q_BRANCH BATCH_888_DEPTH` в `_spawn_worker` | Hook reads empty → either rejects everything (DoS) or allows everything (bypass) |

Implementer MUST address each EC1-EC10 в Phase 2.5 commits; documented as test additions OR explicit "won't-fix with rationale" in retro.

---

**End of spec.** ~340 lines. Handoff: 888-persona-implementer Phase 2.5 (M-path).
