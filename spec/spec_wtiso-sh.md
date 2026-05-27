---
title: WTISO-SH — Worktree-Isolated Shard Handler for parallel batch methodology merge
spec-id: spec_wtiso-sh
spec-version: v2.1-block-fix
parent: Q-260527-WTISO (umbrella)
q-id: Q-260527-WTISO-SH
authored: 2026-05-28
revised: 2026-05-28 (v2.1 — 3 BLOCK from auto-loop B1/B13/B14 patched)
basis: methodology-888.md §4gw analyst brief + §4gy/gza/gzb/gzc/gzd review findings (15 cumulative items) + auto-loop §4gze deterministic verdict
supersedes: spec_wtiso-sh.v1.4-archived.md (4 rounds NEEDS-REVISION, compounding-bugs pattern empirically validated → Q-260528-DRAFT-PATTERN cleanslate trigger fired)
tier: M
pipeline: full-cycle
security_critical: false
status: handoff-pending (auto-loop re-validation, then implementer)
gate-passed: 888-persona-analyst 2026-05-28 + 888-persona-architect 2026-05-28-v2.1-block-fix
---

# WTISO-SH — Shard Handler for parallel batch methodology merge

## 0. Document status

**Current revision:** v2.1-block-fix (2026-05-28). Patches three BLOCK findings
that emerged after the v2.0 cleanslate was reviewed by sub-agent code-auditor
and by the deterministic auto-loop (§4gze). Patch scope is surgical: B1 path
canonicalisation (ADR-005 + ADR-009), B13 all-skipped gate on `appended_count`
(§3.4 step 11.8 + §3.6 step 14 + F20a/F20b), B14 content-aware idempotency
hash (ADR-001 + §3.6 receipt). Three new RED tests (RED-SH-14/15/16) cement
each fix as a permanent regression fixture.

**v2.0-cleanslate basis** (preserved here for provenance): The v2.0 attempt
was a fresh rewrite of the WTISO-SH spec after `spec/spec_wtiso-sh.v1.4-archived.md`
accumulated four NEEDS-REVISION rounds (§4gy/§4gza/§4gzb/§4gzc/§4gzd). The
v2.0 basis was the analyst brief in methodology-888.md §4gw plus the cumulative
review findings, not the v1.4 body. Per Q-260528-DRAFT-PATTERN, after three or
more incremental revisions the architect rewrites from primary sources. v2.0
broke the v1.x compounding-bugs loop on the four pre-existing defect classes;
v2.1 closes the residual three (B1/B13/B14) detected post-v2.0 by the same
auto-loop infrastructure that shipped under Q-260528-AUTO-LOOP §4gze.

## 1. Purpose & scope

### 1.1 What this feature does

WTISO-SH adds a **single-process post-batch merger** that consolidates
isolated per-worker shards into the shared `methodology-888.md` file in a way
that is collision-free, append-only, idempotent, and rollback-safe.

The merger is the third and final sub-initiative under the WTISO umbrella
(after WTISO-WT worktree isolation and WTISO-BW bwrap sandboxing, both
shipped). With WT+BW shipped, parallel workers already execute in isolated
filesystem views; what remains is the **merge step** from those isolated views
back into shared state.

### 1.2 Why it is needed

`BATCH_PARALLEL_ENABLED=on` + `BATCH_ISOLATION_ENABLED=on` is physically
working, but the moment two or more workers attempt to write into
`methodology-888.md` directly (even via `flock` on the file), the following
races are reachable:

1. **Section-number collision:** worker A and worker B both compute next
   anchor `§4gw` before either has written — the second appender silently
   collides with the first.
2. **`last-touched` race:** four workers complete in the same second; the last
   `flock`-protected write wins and the previous three timestamps are lost.
3. **Append-only violation:** a buggy worker may technically `Edit` middle-of-file
   content; nothing currently enforces the invariant that prior bytes are
   immutable.
4. **Partial-write on crash:** if a worker crashes between writing the section
   body and the frontmatter timestamp, the methodology is left in a
   half-written state with no atomic rollback.

The merger fixes all four by moving every cross-shard concern out of workers
and into a single deterministic post-batch step.

### 1.3 Out of scope (explicit)

- Sequential mode (`BATCH_PARALLEL_ENABLED=off`) — workers continue to write
  into the main methodology directly, the merger code path is inert.
- `security_critical:true` batches — those always force sequential mode per
  the §2 override matrix in the dispatcher; merger is not invoked.
- Inter-worker communication during the shard-write phase — workers remain
  fully isolated; coordination happens **only** in the post-batch merger.
- Worktree / bwrap layers — those are closed (WT/BW shipped); SH assumes
  isolated state.
- Retroactive rewriting of historical methodology sections — append-only
  forward direction only.
- Changes to `§4` alphabetic numbering convention — preserved; readers retain
  current expectations.
- Changes to the patch-counter, intent-detector, or merge-guard hooks beyond
  the addition of a new sibling `per-q-patch-counter.sh`.

## 2. Definitions

| Term | Definition |
|---|---|
| **batch** | A set of one or more Q-IDs executed by `888-batch.sh run`. May contain one or more parallel groups. |
| **parallel group** | A subset of the batch where members run concurrently (one worker each). |
| **worker** | A `claude -p` subprocess assigned to exactly one Q-ID within a parallel group. |
| **shard** | A single markdown file written by a worker into `audit/shards/<batch-id>/<q-id>.md` containing one (or more) `## 4XXX.` section bodies to be appended into `methodology-888.md`. |
| **batch-id** | A UUID-suffixed identifier minted by `888-batch.sh` at batch start (`<ISO-ts>-<8-hex>`), used as the directory name under `audit/shards/`. |
| **placeholder anchor** | A reserved section heading `## 4PLACEHOLDER-<8-hex-batch-uuid>.` written by workers, intended to be rewritten by the merger to a final `## 4XXX.` letter. The UUID suffix makes the literal string globally unique and therefore safe to `sed`-rewrite without false-positives. |
| **final anchor** | A canonical `## 4XXX.` section heading written into `methodology-888.md` (`§4a`..`§4z`, `§4aa`..`§4zz`). |
| **merger** | The single-process script `scripts/888-shard-merger.sh` that runs once at batch end. |
| **staging file** | `methodology-888.md.staging-<batch-id>` — a copy of the live methodology to which shards are appended; promoted by atomic `mv` only after all per-shard gates pass. |
| **merge receipt** | `audit/shards/<batch-id>/.merge-receipt.json` — idempotency evidence containing `{batch_id, shards_sha256, pre_merge_sha, post_merge_sha, ts, exit_code}`. |
| **anchor letter** | The variable-length lowercase ASCII suffix of `§4XXX`, drawn from the closed sequence `a, b, …, z, aa, ab, …, zz` (total cardinality 702). |

## 3. Algorithm

The merger executes a linear pipeline (pattern P1 with one P2 fan-out for L1
gate verdict). Each step has a defined failure mode in §5.

### 3.1 Pre-flight (P1..P5)

1. **P1 mkdir + flock.** Compute `LOCK=audit/shards/<batch-id>/.merger.lock`.
   Run `mkdir -p audit/shards/<batch-id>/` first (the lock file's directory
   must exist before `flock` opens it). Acquire `flock -x -w 30` on `$LOCK`;
   on timeout exit `RC=2` (`merger_contention`).
2. **P2 batch-id validation.** Assert `<batch-id>` matches
   `^[0-9TZ:-]+-[0-9a-f]{8}$`. Reject any other string (shell-injection guard).
3. **P3 receipt scan.** If `audit/shards/<batch-id>/.merge-receipt.json`
   already exists, validate it with `jq -e .` and confirm
   `shards_sha256` matches the current shard set hash; on match exit `RC=0`
   with audit event `shard_merger_skipped_idempotent`. On hash mismatch exit
   `RC=3` (`receipt_conflict`).
4. **P4 symlink resolve.** If `methodology-888.md` is a symlink (`[[ -L … ]]`),
   resolve it via `readlink -f`. Treat the resolved path as the merge target
   for the rest of the pipeline; never replace the symlink itself. If
   `readlink -f` returns an empty string (cycle), exit `RC=4`
   (`symlink_cycle`).
5. **P5 signal trap.** Install a single `trap '_merger_cleanup' INT TERM EXIT`
   that removes any leftover `*.staging-<batch-id>` and releases the flock.
   Install only **once**, in P5 (no duplicate trap in later steps).

### 3.2 Shard enumeration & sort

6. **Enumerate.** `find audit/shards/<batch-id>/ -maxdepth 1 -name '*.md' -type f`,
   sorted with `LC_ALL=C sort` so the ordering is byte-deterministic and
   locale-independent.
7. **Distinguish empty-vs-missing.** If the directory is missing → exit
   `RC=5` (`batch_unknown`). If it is present but empty → exit `RC=0` with
   audit `shard_merger_noop_empty_batch` and no methodology mutation.

### 3.3 Anchor allocation

8. **Read current highest anchor.** Extract from the target methodology the
   highest existing `^## 4([a-z]{1,2})\.` letter. Implementation must:
   (a) gate the pipeline on `[[ -r "$TARGET" ]]` before reading;
   (b) set `LC_ALL=C` at function entry (not just `LC_COLLATE=C` on `sort`,
   which only covers the sort step);
   (c) use a fully-parenthesised pipeline `{ grep …; } || true` only inside
   the explicit-readability branch.
9. **Allocate next letter per shard.** For each shard in sorted order, call
   `next_anchor_letter "$current_highest"`. Capture the result with the
   **mandatory** pattern:

   ```bash
   NEXT=$(next_anchor_letter "$current_highest")
   rc=$?
   if [[ $rc -ne 0 ]]; then
       case $rc in
           1) echo "anchor pool exhausted (702 cap)" >&2 ; exit 6 ;;
           2) echo "malformed current letter: $current_highest" >&2 ; exit 7 ;;
           *) echo "next_anchor_letter unknown rc=$rc" >&2 ; exit 8 ;;
       esac
   fi
   current_highest="$NEXT"
   ```

   **Critical:** the caller must capture `$?` immediately on the line after the
   command substitution. The negated-`if` form
   `if ! NEXT=$(next_anchor_letter "$X"); then case $? in …` is **prohibited**
   because the bash semantics of `!` make `$?` always `0` inside the
   then-branch, dropping the rc-distinguishing arms into dead code (this was
   the v1.4 BLOCK-1 regression and is the single most expensive defect class
   in the spec history).

### 3.4 Staging build

10. **Copy.** `cp --reflink=auto "$TARGET" "$STAGING"` (reflink for speed on
    btrfs/xfs; falls back to full copy elsewhere). Run `sync -f "$STAGING"`
    on completion to defend against power-loss.
11. **For each shard, in sorted order:**
    1. Compute `shard_sha = sha256sum < "$shard"` (used for both TOCTOU
       defence and the merge receipt).
    2. Run **L1** gate (see §4): code-review verdict, with `timeout 60s`
       wrapping the Agent invocation; on hang retry once, on second hang
       record verdict `error` and skip the shard (the shard is **not**
       appended; the batch continues with the remaining shards).
    3. Run **L2** gate: LOC bounds + frontmatter discipline. Failure ⇒ skip
       the shard with audit event `shard_l2_rejected`.
    4. Run **L3** gate: append-only invariant + fence-aware structural
       checks (see §4 and ADR-013). Failure ⇒ **abort the entire batch**
       (whole-batch reject; see ADR-005).
    5. Rewrite the placeholder anchor. Use a **state-machine rewriter**
       (not blind `sed`) that toggles fenced-block state on lines matching
       `^( {0,3})(\`\`\`|~~~)` and only rewrites the placeholder string
       outside a fenced block. The placeholder string contains the
       per-batch UUID (`§4PLACEHOLDER-<8-hex>`), so even a worst-case fence
       miss is unlikely to find a literal match elsewhere. Q-ID prefixes on
       sub-section headers (`### Q-260527-WTISO-SH Phase 1 analyst`) protect
       against sub-header collision when two shards land under the same
       anchor (see ADR-006).
    6. Re-check `sha256sum < "$shard"` matches the value captured in step
       11.1 (TOCTOU defence). Mismatch ⇒ abort batch, audit event
       `shard_toctou_mismatch`.
    7. Append the post-rewrite shard body to `$STAGING`.
    8. Increment `appended_count` (initialised to `0` before the loop). Only
       shards that survive steps 11.2–11.7 contribute to this counter; per-shard
       skips (L1/L2 failure) leave `appended_count` unchanged. The counter is
       consumed in §3.6 step 14 to distinguish the legitimate all-skipped
       outcome from an anomalous post-merge noop.

### 3.5 Frontmatter update (merger-only)

12. Compute `effective_mtime = min(max(shard_mtimes), now+60s)` to clamp
    future-dated mtimes (NTP drift / container clock jump). Update only the
    single line matching `^last-touched:` in the frontmatter to
    `last-touched: <effective_mtime>`. Workers must **never** touch the
    frontmatter (enforced by L3, ADR-003).

### 3.6 Atomic promotion

13. Verify pre-merge SHA: `pre_merge_sha = sha256sum < "$TARGET"`.
14. `mv "$STAGING" "$TARGET"` (POSIX-atomic on same filesystem). If `$TARGET`
    was resolved from a symlink in P4, the resolved path is used here so the
    symlink itself is preserved. Compute
    `post_merge_sha = sha256sum < "$TARGET"`. The post-merge SHA is then
    interpreted with `appended_count` as a precondition:

    - **All-skipped (legitimate noop)** — `appended_count == 0` (every shard
      failed L1/L2 and was skipped per ADR-005). The staging file is a
      byte-for-byte copy of the live methodology by construction; therefore
      `pre_merge_sha == post_merge_sha` is the **expected** outcome. Exit
      `RC=0` with the merge receipt populated via `skipped_shards[]` /
      `rejected_shards[]`. Audit event: `shard_merger_all_skipped`.
    - **Unexpected noop** — `appended_count > 0` but
      `pre_merge_sha == post_merge_sha`. Staging claimed appends yet the file
      did not change; this is anomalous (rewriter bug, empty-shard race, or
      filesystem corruption). Exit `RC=9` (`merge_noop_unexpected`).
    - **Normal merge** — `appended_count > 0` and the SHAs differ. Exit
      `RC=0` and continue to step 15.

    The gate on `appended_count` is the v2.1 BLOCK-2 fix: the v2.0 spec
    unconditionally treated equal SHAs as RC=9, which incorrectly errored on
    every legitimate all-skipped batch.
15. Write merge receipt:

    ```json
    {
      "batch_id": "<batch-id>",
      "shards_sha256": "<content-aware hash over sorted (path, sha256(file-bytes)) pairs — formula in ADR-001>",
      "pre_merge_sha": "<…>",
      "post_merge_sha": "<…>",
      "ts": "<ISO-8601>",
      "exit_code": 0,
      "skipped_shards": [],
      "rejected_shards": []
    }
    ```

    `jq -e .` validate before writing (rejects partial write on disk-full).

16. Release flock (via the EXIT trap installed in P5).

### 3.7 Commit integration

17. `scripts/888-batch-commit-policy.sh` stages the methodology change **and**
    the merge receipt in the same commit. The receipt is the operational
    record; the methodology is the user-visible state. Both belong to the
    same atomic git event. Pre-push only — no `--amend` is used after push.

## 4. Gate table

| Gate | Layer | Check | Failure action | Rationale |
|---|---|---|---|---|
| **L1** | LLM review | `code-reviewer` Agent (Sonnet, R7 substitute when outside `_bmad/`) verdict on the shard body. `timeout 60s`; on hang, retry once; on second hang record `error`. | Skip shard (`shard_l1_rejected` audit), batch continues. | A bad shard should never block the whole batch; isolation is the point of the design. |
| **L2** | bounded checks | (a) shard LOC ≤ `SHARD_MAX_LOC` (env, validated against `^[1-9][0-9]{0,4}$`, hard-clamped to ≤ 50000). (b) frontmatter discipline: workers must not write `^---$` lines outside the first 50 lines. | Skip shard (`shard_l2_rejected`). | Cheap, deterministic guard rails. |
| **L3** | structural invariant | Append-only check with **fence-aware state machine**: count `^## [a-z]?\d+\.` headings and `^---$` lines **outside fenced code blocks only**. Top 50 lines (heuristic): frontmatter must be unchanged byte-for-byte against the live methodology head. | **Abort entire batch** (`shard_l3_violation`). | L3 violation is a class of bug that indicates the worker is mis-implementing the protocol — failing closed on the whole batch is safer than partial mutation. |

The L3 fence state machine maintains a single boolean `in_fence`, toggled by
any line matching `^( {0,3})(\x60\x60\x60|~~~)`. Heading-shaped lines inside
a fenced block are ignored. **Limitation:** four-or-more-backtick fences and
indented code blocks (≥4 leading spaces) are out of scope; see §11.

## 5. Failure modes

| # | Where | Cause | Detection | Action | RC |
|---|---|---|---|---|---|
| F1 | P1 | flock timeout (concurrent merger) | `flock -w 30` returns non-zero | exit | 2 |
| F2 | P2 | malformed batch-id | regex mismatch | exit | 10 |
| F3 | P3 | receipt JSON malformed | `jq -e .` fails | exit | 11 |
| F4 | P3 | receipt exists but shard set hash differs | hash compare | exit | 3 |
| F5 | P4 | symlink target is a cycle | empty `readlink -f` | exit | 4 |
| F6 | P5 | SIGINT/SIGTERM during pipeline | trap | cleanup staging + lock, exit | 130/143 |
| F7 | 3.2 step 6 | shard dir missing | `[[ -d … ]]` | exit | 5 |
| F8 | 3.2 step 7 | shard dir present but empty | `find … | wc -l == 0` | noop, exit | 0 |
| F9 | 3.3 step 8 | methodology unreadable | `[[ -r "$TARGET" ]]` | exit | 12 |
| F10 | 3.3 step 9 | anchor pool exhausted (>702 shards in one batch) | `next_anchor_letter` rc=1 | exit | 6 |
| F11 | 3.3 step 9 | anchor letter malformed | `next_anchor_letter` rc=2 | exit | 7 |
| F12 | 3.4 step 10 | disk full mid-copy | `cp` non-zero | cleanup staging, exit | 13 |
| F13 | 3.4 step 11.2 | L1 hung twice | `timeout 60s` twice | record `error`, skip shard, continue | (per-shard) |
| F14 | 3.4 step 11.3 | L2 reject | bounded check fails | skip shard, continue | (per-shard) |
| F15 | 3.4 step 11.4 | L3 violation | state-machine check | abort batch, discard staging | 14 |
| F16 | 3.4 step 11.6 | TOCTOU mismatch | sha256 recheck | abort batch | 15 |
| F17 | 3.5 step 12 | future-dated mtime | clamp `min(mtime, now+60s)` | no error, just clamped | (n/a) |
| F18 | 3.6 step 14 | mv across filesystems | `mv` returns EXDEV | abort batch (staging stays for inspection) | 16 |
| F19 | 3.6 step 14 | NFS rename not atomic | per filesystem; emit warning | warn, continue (best-effort) | (n/a) |
| F20a | 3.6 step 14 | post-merge SHA equal AND `appended_count > 0` | unexpected noop (staging claimed appends but file unchanged) | exit | 9 |
| F20b | 3.6 step 14 | post-merge SHA equal AND `appended_count == 0` | legitimate all-skipped outcome (every shard rejected by L1/L2) | exit + populate skipped/rejected in receipt | 0 |
| F21 | 3.7 | git not in clean state pre-merger | git status check upstream | dispatcher pre-flight handles | (n/a) |

## 6. ADRs

### ADR-001 SH1 — Content-aware idempotency hashing

`shards_sha256` is a **content-aware** hash over the sorted shard set: for
each path in `sorted_shard_paths`, the formula incorporates **both** the path
and the SHA-256 of the file body, NUL-delimited so paths with spaces or
non-ASCII characters cannot be confused with the hash boundary:

```bash
shards_sha256=$(
  for p in "${sorted_shard_paths[@]}"; do
    content_hash=$(sha256sum < "$p" | awk '{print $1}')
    printf '\0%s\0%s' "$p" "$content_hash"
  done | sha256sum | awk '{print $1}'
)
```

Path-only hashing was the v2.0 BLOCK-3 defect: re-running the merger after
an in-place shard mutation produced an identical path-set hash and falsely
treated the batch as idempotent, silently dropping the mutation. Including
`sha256(file_bytes)` per shard detects any cross-invocation content change.

Stored in `audit/shards/<batch-id>/.merge-receipt.json`. Re-running the
merger on the same batch-id is a byte-identical noop iff the content-aware
hash matches.

### ADR-002 SH2 — `§4` anchor assignment

Workers write the placeholder
`## 4PLACEHOLDER-<8-hex-batch-uuid>. <human-readable section title>`. The
merger sorts shards by file path (deterministic byte order under `LC_ALL=C`)
and assigns final letters in order. The UUID suffix means the placeholder
string is globally unique and safe to rewrite via state-machine sed without
false-positives in fenced blocks (see ADR-013).

### ADR-003 SH3 — Append-only enforcement

Workers may only **append** to their own shard file; they never read or write
the live methodology. The merger enforces append-only on the staging step via
L3: the staging file's first N bytes must equal the live methodology's first
N bytes (where N = pre-merge size). Any deviation aborts the batch. This is
the hard guarantee that no historical content can be rewritten by a worker.

### ADR-004 SH4 — `last-touched` semantics

`last-touched` is updated exclusively by the merger using
`min(max(shard_mtimes), now+60s)`. The clamp defends against NTP drift and
container clock jumps. Workers never modify the frontmatter.

### ADR-005 SH5 — Failure modes per layer

- **L1 failure** (per-shard): skip that shard, continue batch.
- **L2 failure** (per-shard): skip that shard, continue batch.
- **L3 failure** (whole batch): discard staging, batch RC=14, no methodology
  mutation, `audit/shards/<batch-id>/` retained for forensics.
- **TOCTOU failure** (whole batch): same as L3.
- **Symlink cycle, anchor exhausted, malformed letter**: exit pre-mutation.

Per-shard skip is recorded in receipt `skipped_shards[]` / `rejected_shards[]`
so the operator can re-spawn just the failed shards manually if needed.

### ADR-006 SH6 — Duplicate q-id conflict

If two shards declare the same `q-id` in their first-line comment, the
merger appends both under a **single** final `§4XXX` anchor in sorted shard-path
order. Sub-section headers within each shard are required to be prefixed
with the q-id (`### Q-260527-WTISO-SH Phase 1 analyst` rather than
`### Phase 1 analyst`) so the merged section has no duplicate sub-headers.
The sub-header rewrite is implemented via the same state-machine pattern as
ADR-013 so fenced-block examples are not corrupted.

If two shards have identical content (sha256 equal), the second is dropped
with a warning `shard_duplicate_content`.

### ADR-007 SH7 — Per-Q patch counter (sibling hook)

A new hook `~/.claude/hooks/storm/per-q-patch-counter.sh` is added **alongside**
the existing `patch-counter.sh`. It maintains per-Q counters in
`audit/shards/<batch-id>/<q-id>.patches`. The existing per-session global
counter is **not** modified — sequential mode keeps its current semantics.
The sibling pattern is chosen over extension because the two counters have
fundamentally different state shapes (per-session vs per-Q) and conflating
them would risk regressions in the well-tested existing counter.

### ADR-008 SH8 — Gate runtime budget

L1 (the LLM review) is skipped when `BATCH_MOCK_MODE=1` is set, controlled by
the env `SHARD_GATE_SKIP_L1=1` derived from `BATCH_MOCK_MODE`. This keeps mock
runs token-free and matches `888-batch.sh` mock-mode contract. Production L1
budget: ≤60s per shard via `timeout`; retry-once policy bounds worst case at
~125s. Four shards in parallel batches give ~5min worst-case merger window.

### ADR-009 SH9 — Storage path

`audit/shards/<batch-id>/` is the canonical path for shard storage. Aligned
with `audit/batches/` for shared retention policy (7-30 days default). All
references in this spec use the `audit/shards/` form; the earlier-draft form
(with a leading-dot prefix on the directory name) is **not** used and must
not appear in implementation code, audit emissions, or test fixtures.

### ADR-010 SYMLINK — Transparent resolve

If `methodology-888.md` is a symlink, `readlink -f` resolves it once at P4 and
all subsequent `cp` / `mv` operations target the resolved path. The symlink
itself is never replaced. Cycle case (empty readlink output) aborts the
merger with RC=4.

### ADR-011 PLACEHOLDER-UUID — Safe rewrite token

Placeholder anchor format:
`## 4PLACEHOLDER-<8-hex-of-batch-uuid>. <title>` (e.g.
`## 4PLACEHOLDER-a1b2c3d4. Phase 1 analyst — …`). The 8-hex suffix is the
last 8 characters of the batch-id UUID, derived deterministically from
`<batch-id>` so all workers in the same batch agree. 8-hex = 4 billion
combinations; at one installation per operator and ~10k batches/year, the
collision probability over a 10-year horizon is negligible (`<10^-5`). Bump
to 12-hex if a multi-install scenario is introduced (parked
Q-260527-WTISO-SH-UUID-THRESHOLD).

### ADR-012 ANCHOR-OVERFLOW — Lookup table + cap=702

The `next_anchor_letter` function uses a **static lookup table** (bash array)
indexed 0..701, not a `printf '\NNN'` octal trick. This kills an entire class
of bugs (xargs swallowing backslashes, locale-dependent byte ranges,
out-of-range octal values).

```bash
declare -ga _ANCHOR_TABLE=(
    a b c d e f g h i j k l m n o p q r s t u v w x y z
    aa ab ac ad ae af ag ah ai aj ak al am an ao ap aq ar as at au av aw ax ay az
    ba bb bc bd be bf bg bh bi bj bk bl bm bn bo bp bq br bs bt bu bv bw bx by bz
    # … 24 more rows …
    za zb zc zd ze zf zg zh zi zj zk zl zm zn zo zp zq zr zs zt zu zv zw zx zy zz
)
# 26 + 26*26 = 702 entries.
```

(The full table is generated once in the script preamble via a nested loop.
The literal listing above is illustrative; implementation may generate
programmatically as long as the result is the same 702-entry array indexed
in canonical order `a..z, aa..zz`.)

Function contract:

```bash
next_anchor_letter() {
    local LC_ALL=C
    local cur="${1:-}"

    # Empty → 'a'.
    if [[ -z "$cur" ]]; then
        printf '%s\n' "${_ANCHOR_TABLE[0]}"
        return 0
    fi

    # Validate format: 1 or 2 lowercase ASCII letters.
    if ! [[ "$cur" =~ ^[a-z]{1,2}$ ]]; then
        return 2  # malformed
    fi

    # Find current position. Linear scan is fine (≤702 entries).
    local i
    for ((i = 0; i < 702; i++)); do
        if [[ "${_ANCHOR_TABLE[i]}" == "$cur" ]]; then
            local next_i=$((i + 1))
            if (( next_i >= 702 )); then
                return 1  # exhausted (cap = 702 total)
            fi
            printf '%s\n' "${_ANCHOR_TABLE[next_i]}"
            return 0
        fi
    done

    # Should be unreachable given the regex guard, but treat as malformed.
    return 2
}
```

**Cap value: 702.** This is the **total combined** capacity (26 single +
676 two-letter). All three locations in this spec — §6 ADR-012 (here),
§8 RED-SH-8 test, and the merger error message at §3.3 step 9 — use the
value 702. No site uses 676 or 700.

**Caller contract:** the caller must capture `$?` directly on the line
immediately following the command substitution; see §3.3 step 9 for the
mandatory pattern. The negated-`if` form is prohibited (bash semantics make
`$?` always 0 inside the then-branch of `if !`, which causes all
rc-distinguishing arms to become dead code — this was the v1.4 BLOCK-1
regression and is the highest-cost defect class in the spec history).

**All output via `printf '%s\n'`.** The previous octal trick
(`printf '\%03o' N | xargs printf '%b'`) is removed everywhere; `xargs`
strips the backslash before passing to `printf %b`, which empirically
produces `'142'` instead of `'b'` for `'a' → next`. Direct table lookup
sidesteps this whole problem.

**Locale:** `local LC_ALL=C` at function entry, not just `LC_COLLATE=C` on
sort. `LC_ALL=C` covers the case-pattern matching used by the regex
validation, which under non-C locales can match accented characters (e.g.
`ñ` under UTF-8) that are not valid anchor letters.

### ADR-013 L3-HEURISTIC — `head -50` + fence-aware state machine

The L3 gate uses a `head -50` heuristic plus a fenced-block state machine.
Frontmatter is positionally invariant (always in the top of the file); a
50-line window covers every real case in the existing methodology
(`grep -n '^---$' methodology-888.md | head -2` returns lines 1 and N where
N≤30 in all observed states).

The state machine toggles `in_fence` on lines matching
`^( {0,3})(\x60\x60\x60|~~~)` (markdown-spec-compliant for the two fence
styles in actual use). `in_fence == true` lines are excluded from L3
heading/frontmatter counts.

**Limitations** (top-level, see §11):

- Four-or-more-backtick fences (CommonMark extended-fence syntax) are not
  recognised. If the methodology starts using them, the state machine must
  be extended.
- Indented code blocks (≥4 leading spaces, no fence markers) are not
  recognised. They are not used in the existing methodology.
- Tilde fences (`~~~`) are recognised but not heavily tested.

These limitations are documented here at the ADR level and re-listed in §11
so they are not buried in inline comments where they get lost across
revisions (a defect class observed in v1.4 NOTE-1).

## 7. Cross-impact (9 files)

| File | Change |
|---|---|
| `scripts/888-batch.sh` | Post-batch merger invocation; propagate `$SHARD_OUT` env into `_spawn_worker`. |
| `scripts/888-batch-commit-policy.sh` | Include merge receipt path in commit; stage both methodology and receipt atomically. |
| `scripts/888-shard-merger.sh` | **NEW** — core merger described in §3. |
| `scripts/888-shard-cleanup.sh` | **NEW** — pre-batch GC of stale `audit/shards/<batch-id>/` directories with `--retention 7d` (or env override). |
| `~/.claude/hooks/storm/per-q-patch-counter.sh` | **NEW** — sibling hook per ADR-007. |
| `~/.claude/skills/888-persona-*/SKILL.md` | Six personas — each updated to write to `$SHARD_OUT` (placeholder anchor) when the env is set, otherwise current behaviour. A single shared helper `scripts/persona-shard-write.sh "<content>"` is preferred to per-persona inline conditionals (DRY). |
| `scripts/regression-smoke.sh` | Add 2 SH scenarios: sequential-mode-unchanged + parallel-mode-merge-success. |
| `tests/wtiso/test-shard-*.sh` | **NEW** — 13 RED tests per §8. |
| `evals/baselines/wtiso-sh-baseline-2026-05-28.json` (+ runner) | **NEW** — DELTA targets per analyst §4gw Field 6. |

Sequential-mode regression test (`BATCH_PARALLEL_ENABLED=off`) confirms the
merger code path is fully inert: byte-identical methodology output, no new
audit events, no shard directory created.

## 8. Iron Law test plan (16 RED tests required pre-impl)

All RED tests must be committed and **failing** before any implementation
code is written. The implementation is complete when all 16 tests are green.
RED-SH-14/15/16 are the v2.1 BLOCK-coverage tests added to cement the three
auto-loop findings (B1 path canonicalisation, B13 all-skipped gate, B14
content-aware idempotency hash) as permanent regression fixtures.

| ID | File | Scenario |
|---|---|---|
| RED-SH-1 | `tests/wtiso/test-shard-collision-isolation.sh` | Two workers write shards with placeholder anchors. Merger renames them deterministically by sorted shard path. Assert: zero collisions in merged methodology. |
| RED-SH-2 | `tests/wtiso/test-shard-idempotent-rerun.sh` | Invoke merger twice on the same batch-id. Assert: second invocation is byte-identical (no second mutation), audit event `shard_merger_skipped_idempotent`. |
| RED-SH-3 | `tests/wtiso/test-shard-partial-rollback.sh` | Synthetic shard-3 fails L3 (append-only violation). Assert: shards 1, 2, 4 are also not merged (whole-batch reject), `audit/shards/<batch-id>/` retained for forensics, methodology byte-identical to pre-batch. |
| RED-SH-4 | `tests/wtiso/test-shard-append-only.sh` | Synthetic shard mutates prior methodology content. Assert: L3 rejects with reason `append_only_violation`, batch RC=14. |
| RED-SH-5 | `tests/wtiso/test-shard-last-touched.sh` | Four workers with mtimes `T1<T2<T3<T4`. Assert: post-merge frontmatter `last-touched == T4`. |
| RED-SH-6 | `tests/wtiso/test-shard-q-id-conflict.sh` | Two shards declare the same q-id. Assert: both appended under one §4XXX anchor in sorted shard-path order; sub-headers are q-id-prefixed; no duplicate sub-headers in merged output. |
| RED-SH-7 | `tests/wtiso/test-shard-symlink.sh` | `methodology-888.md` is a symlink. Assert: post-merge the symlink still points to the same resolved target; the resolved file content has the appended shards; the symlink itself was not replaced. |
| RED-SH-8 | `tests/wtiso/test-shard-anchor-overflow.sh` | Batch with 702 shards lands on `§4zz` cleanly; batch with 703 shards exits RC=6 (`anchor_pool_exhausted`) cleanly without partial mutation. The cap value **702** is asserted as a literal in the test (matches §6 ADR-012 and the §3.3 step 9 error message). |
| RED-SH-9 | `tests/wtiso/test-shard-fenced-block.sh` | Shard contains a fenced code block with a literal `## 4XXX.` heading example inside it. Assert: state-machine L3 does not count the in-fence heading; the in-fence example is not rewritten by the placeholder sed; round-trip preserves the example byte-for-byte. |
| RED-SH-10 | `tests/wtiso/test-shard-shell-injection-batch-id.sh` | Invoke merger with `batch-id="; rm -rf /tmp"`. Assert: P2 regex rejects, RC=10, no filesystem mutation. |
| RED-SH-11 | `tests/wtiso/test-shard-concurrent-merger.sh` | Two merger processes invoked simultaneously on the same batch-id. Assert: one acquires the flock, the other times out at RC=2; methodology mutated exactly once. |
| RED-SH-12 | `tests/wtiso/test-shard-anchor-letter-fn.sh` | 13-vector functional matrix for `next_anchor_letter`: `("" → "a"), ("a" → "b"), ("y" → "z"), ("z" → "aa"), ("aa" → "ab"), ("ab" → "ac"), ("az" → "ba"), ("bz" → "ca"), ("zy" → "zz"), ("zz" → rc=1), ("aaa" → rc=2), ("aZ" → rc=2), ("1a" → rc=2)`. Assert all 13 pass; assert output uses no `xargs printf '%b'` antipattern. |
| RED-SH-13 | `tests/wtiso/test-shard-rc-capture-pattern.sh` | Static check: `grep -nE 'if !.*next_anchor_letter' scripts/888-shard-merger.sh` returns 0 matches (the prohibited negated-`if` pattern is absent). Assert the direct rc-capture pattern (`NEXT=$(…); rc=$?`) appears at least once. |
| RED-SH-14 (v2.1 B1) | `tests/wtiso/test-shard-path-canonical.sh` | Static check across implementation and test fixtures: assert that no source line contains the legacy leading-dot directory form for shard storage (the regex source is built at test runtime as `'\\' + 'dot' + 'shards/'`-equivalent to avoid embedding the literal token in this spec; equivalent egrep target = backslash-dot followed by `shards/`). Functional check: forensics-retain path on L3 failure is `audit/shards/<batch-id>/` literally (verify `[[ -d audit/shards/$batch_id ]]` after synthetic L3 abort). The legacy form (with the leading dot on the directory name) must not appear in implementation, audit emissions, or test fixtures. |
| RED-SH-15 (v2.1 B13) | `tests/wtiso/test-shard-all-skipped-legitimate.sh` | Batch of 3 shards where all three are forced to L1-reject (e.g., via a mock code-reviewer returning `BLOCK` on every shard). Assert: `appended_count == 0`, merger exits `RC=0` (not `RC=9`), audit event `shard_merger_all_skipped` recorded, receipt contains `skipped_shards` with all three paths and `rejected_shards: []`, methodology byte-identical to pre-batch. A companion case with one passing shard and two L1-rejected shards (`appended_count == 1`) must still produce `RC=0` with the methodology actually mutated. |
| RED-SH-16 (v2.1 B14) | `tests/wtiso/test-shard-content-aware-hash.sh` | Build two shard sets `A` and `B` with **identical sorted path lists** but **different file contents** (`A/shard1.md` = "x"; `B/shard1.md` = "y"; same path strings). Assert: `shards_sha256(A) != shards_sha256(B)`. Then in-place mutate one shard between merger invocations on the same batch-id and assert the second invocation detects the mutation (does **not** treat as idempotent noop) — the receipt's `shards_sha256` differs from the first run. A path-only hash would falsely pass; this test fails on the v2.0 formula and passes on the v2.1 ADR-001 formula. |

Regression smoke (not counted in 16): `test-shard-sequential-mode-unchanged.sh`
proves `BATCH_PARALLEL_ENABLED=off` yields byte-identical behaviour.

## 9. Mini threat model (security_critical=false)

This is a single-user solo-operator threat profile. Full STRIDE/threat-model
is out of scope (analyst Field 6 baseline). Top 4 surface vectors:

| # | Vector | Mitigation |
|---|---|---|
| T1 | Shard injection (worker writes `</methodology>`, control characters, unicode tricks). | Merger does raw markdown append, no HTML/markdown re-parse. SHA256 in receipt covers content integrity for forensics. |
| T2 | Merge-time TOCTOU (`audit/shards/<batch-id>/` modified between gate-time and merge-time). | Dual-hash check (gate-time + pre-mv); mismatch ⇒ abort batch. |
| T3 | Orphan cleanup (`audit/shards/<batch-id>/` left after failed batch eventually consumes disk). | `scripts/888-shard-cleanup.sh --retention 7d` invoked pre-batch by `888-batch.sh`. Audit retention aligned with `audit/batches/`. |
| T4 | Receipt forgery / gate-bypass env. | Receipt `jq -e .` validated on read. Gate-skip env (`SHARD_GATE_SKIP_L1`) only honoured when `BATCH_MOCK_MODE=1`; explicit regression test forbids dotfile-pollution path. |

Deferred to qa Phase 3 (DACI: qa Driver for coverage, architect Contributor):
explicit OWASP ASI mappings for T1-T4, fuzz testing of shard parser.

## 10. Performance budget

- Per-shard L1 gate: ~5s expected, 60s hard timeout, 125s worst case after
  one retry.
- Per-shard L2 + L3: ~50ms each.
- Staging copy: ~50ms for typical methodology (~1MB). Reflink-aware
  filesystems (btrfs, xfs) drop this to ~5ms.
- Atomic mv: ~5ms.
- Four-shard parallel batch in production (no mock): ~25s merger wall-clock.
- Four-shard mock batch (`BATCH_MOCK_MODE=1`): ~200ms (L1 skipped).
- Sequential mode (`BATCH_PARALLEL_ENABLED=off`): zero merger overhead
  (code path inert).

## 11. Limitations

Documented at top-level (not buried in code comments) so they survive
revisions:

1. **Markdown fences:** only triple-backtick (`\x60\x60\x60`) and tilde
   (`~~~`) fences are recognised by the L3 state machine. Four-or-more-
   backtick (CommonMark extended fence) is not supported. Indented code
   blocks (≥4 leading spaces, no fence markers) are not recognised.
   Workaround if needed: convert to triple-backtick or escape via the
   placeholder convention.
2. **Line endings:** the parser assumes LF (`\n`). CRLF (`\r\n`) lines may
   cause `^---$` regex to fail to match (`---\r` ≠ `---`). The L2 gate
   rejects shards containing `\r` bytes with reason `crlf_disallowed`.
3. **Locale:** the entire merger runs with `LC_ALL=C` for byte-exact sort
   and case-pattern matching. Workers must produce ASCII-clean shard content
   for anchor letter context; non-ASCII content in section bodies is fine.
4. **Filesystem semantics:** the atomic `mv` step requires that `$STAGING`
   and `$TARGET` be on the **same filesystem**. NFS rename atomicity is
   filesystem-dependent; the merger emits a warning when running on NFS
   (`findmnt -no FSTYPE`) but does not refuse to operate.
5. **8-hex placeholder UUID collision space:** ~4×10^9 combinations. Safe
   for solo-operator workloads (~10k batches/year). Bump to 12-hex if a
   multi-install scenario emerges (parked Q-260527-WTISO-SH-UUID-THRESHOLD).
6. **Anchor pool cap:** 702 shards per batch. Any single batch needing more
   sections must be split into multiple batches (each with its own merger
   invocation). 702 is well above any realistic per-batch shard count
   (current max observed: ~12).
7. **Symlink:** transparent resolve via `readlink -f`. Cycle case aborts.
   Multi-level symlinks resolve correctly (`readlink -f` follows the chain).
8. **GNU coreutils:** `stat -f -c %T` is GNU-specific (used in the NFS
   warning). On BSD-coreutils systems the warning is silently skipped; the
   merger still operates.

## 12. Open Q for implementer

1. **`scripts/persona-shard-write.sh` shape.** Recommended: single shared
   helper consumed by all six personas (DRY). Helper reads `$SHARD_OUT`
   and `$SHARD_PLACEHOLDER` env, writes a section with the correct
   UUID-suffixed anchor and q-id-prefixed sub-headers. Alternative:
   per-persona inline conditional (explicit but six places to keep in
   sync). Recommendation: shared helper.
2. **L1 sync vs async.** Recommended: **async-with-fallback**. Workers
   pre-invoke `code-reviewer` while writing the shard and persist the
   verdict to `audit/shards/<batch-id>/.gate-verdicts/<q-id>.verdict`. The
   merger reads the cached verdict; if absent (e.g. worker crashed before
   writing), the merger does a fresh `timeout 60s + retry-once`
   invocation. This keeps the merger wall-clock down without sacrificing
   correctness.
3. **Receipt commit semantics.** Recommended: same commit as the
   methodology change, staged together in `888-batch-commit-policy.sh`. No
   `--amend` is needed because both files are added before
   `git commit -m …`. The receipt is the operational record of what the
   methodology change represents.

## 13. Handoff payload to implementer

```yaml
handoff:
  to: 888-persona-implementer
  source: 888-persona-architect (v2.1-block-fix)
  payload:
    q_id: Q-260527-WTISO-SH
    spec: /home/server/bmad-orchestrator/spec/spec_wtiso-sh.md
    spec_version: v2.1-block-fix
    pattern: P1 linear orchestration (pre-flight → per-shard gate → atomic mv → receipt)
    memory: persistent (audit/shards/<batch-id>/.merge-receipt.json)
    tools: [bash, git, sha256sum, jq, flock, readlink, find, sort, awk]
    multi-llm:
      worker: claude -p Sonnet (inherits batch flag)
      l1_review_gate: Sonnet (R7 substitute via Agent when outside _bmad/)
    threat-model: [§9 — 4 vectors, security_critical=false]
    rag: null
    test_plan: §8 — 16 RED tests (13 original + 3 v2.1 BLOCK-coverage RED-SH-14/15/16), all must be failing before any impl code
    security_critical: false
    complexity: medium
    tier: M
  next-step: |
    implementer first action — bash -n on all 16 test stubs + commit them as RED.
    Then implement the script per §3 algorithm. No code allowed until 16 RED tests are committed.
```

## 14. Glossary of terms (non-technical readers)

- **batch** — a list of tasks that 888 runs together. Сейчас часто 1-4 задачи.
- **worker** — отдельный процесс `claude -p`, который делает одну задачу.
- **shard** — markdown-файл, который worker пишет в свой angle of the disk
  (`audit/shards/<batch-id>/<q-id>.md`), потом merger переносит контент в
  общую методичку.
- **merger** — финальный одноразовый скрипт, который соединяет shards в
  методичку безопасно (без коллизий, append-only).
- **append-only** — пишем только в конец; никогда не правим уже написанное.
  Гарантирует что прошлая история не потеряется.
- **staging-файл** — копия методички с приклеенными shards, которую merger
  валидирует перед тем как заменить настоящую методичку одним `mv`.
- **idempotent** — повторный запуск ничего не ломает; вторая попытка =
  noop с записью в журнал.
- **anchor letter** — буквенный суффикс заголовка `§4XXX`: a, b, ..., z, aa,
  ab, ..., zz. Всего 702 варианта (26 + 26×26).

---

**End of spec v2.1-block-fix.**
