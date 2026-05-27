---
q-id: Q-260527-WTISO-SH
parent: Q-260527-WTISO (umbrella)
siblings: Q-260527-WTISO-WT (done §4fl), Q-260527-WTISO-BW (done §4fo, commit 3a4c243)
phase: 2 (architect — v1.4 sub-agent-review-driven patch)
tier: M
pipeline: full-cycle
security_critical: false
complexity: medium
effort: ~3h (impl) + ~30 min (v1.1) + ~30 min (v1.3) + ~10 min (v1.4 sub-agent verdict)
dep: Q-260527-WTISO-WT, Q-260527-WTISO-BW
blocks: Q-260527-WTISO umbrella close (after SH done — full L-tier umbrella shipped)
created: 2026-05-28
revised: 2026-05-28 v1.4 (1 BLOCK + 5 HIGH + 3 NOTE from FIRST proper sub-agent review §4gzc — Q-260528-WTISO-SH-V13FU closure)
author: 888-persona-architect (auto via /888 WTISO-SH dispatch)
review-protocol: Q-260528-AINTRP lesson applied — sub-agent (Agent code-auditor R7 substitute), NOT inline self-review
---

## Revision history

| Version | Date | Reason | Changes |
|---|---|---|---|
| v1.0 | 2026-05-28 | initial | analyst handoff §4gw → architect §4gx |
| **v1.1** | **2026-05-28** | **edge-case-hunter NEEDS-REVISION (4 BLOCK + 9 HIGH §4gy)** | 4 new ADRs (010-013), amended ADR-002/003/006, hardened §3/§4/§5/§8/§9 |
| **v1.2** | **2026-05-28** | **edge-case-hunter NEEDS-REVISION on v1.1 (3 NEW BLOCK §4gza)** | sort-order fix in ADR-012 (length-then-alpha), `mkdir -p` before flock in P1, ADR-006 sed wrapped in state-machine. HIGH/LOW/NOTE parked as Q-260528-WTISO-SH-V1FU* per R1. |
| **v1.3** | **2026-05-28** | **inline edge-case-hunter NEEDS-REVISION on v1.2 (1 BLOCK + 2 HIGH + 1 NOTE §4gzb) + 1 pre-existing bug discovered during fix-verification** | (1) `next_anchor_letter` two-letter branch fully implemented (`aa`→`ab`, `az`→`ba`, `bz`→`ca`, `zy`→`zz`, `zz`→fail-rc1, malformed→fail-rc2). (2) `LC_COLLATE=C` moved from bare assignment to inline `LC_COLLATE=C sort` (subshell wasn't inheriting unexported var). (3) Deleted duplicated ADR-003 v1.0 text (only v1.1+ADR-013 amendment remains). (4) Path inconsistency unified: 7 `.shards/` → `audit/shards/` per ADR-009 SHARDPATH canonical. (5) **Pre-existing bug fix in scope:** `echo $(printf '\NNN') \| xargs printf '%b'` chain stripped backslash → output was raw `'142'` instead of `'b'`. Replaced with direct `printf '%b' "$(printf '\NNN')"`. 13/13 functional test vectors PASS (validated via ad-hoc bash). **⚠ v1.3 was rejected by FIRST proper sub-agent review (Agent code-auditor R7 substitute, §4gzc) — verdict NEEDS-REVISION, 1 BLOCK + 5 HIGH + 3 NOTE.** |
| **v1.4** | **2026-05-28** | **Agent code-auditor (sub-agent R7 substitute) NEEDS-REVISION on v1.3 — Q-260528-WTISO-SH-V13FU** | (1) **BLOCK removed:** deleted invalid `local _int_to_letter() { ... }` helper line (bash forbids `local` on function declarations — was hard parse error introduced by v1.3 myself). (2) **HIGH fix ADR-006:** regex capture group `Phase[[:space:]][0-9](\.5)?[[:space:]]` extended with `.*` so role suffix isn't dropped (was: `### Phase 1 analyst` → `### Phase 1 `; now: `### Q-NNN Phase 1 analyst`). (3) **HIGH fix caller rc check:** `next_anchor_letter "$HIGHEST"` wrapped in `if !` with explicit rc 1 / rc 2 / unexpected handlers — silent empty NEXT eliminated. (4) **HIGH fix pipefail:** `grep -oE ... \|\| true` guard on first-shard-ever (empty methodology rc 1 no longer aborts pipe under `set -o pipefail`). (5) **HIGH style cleanup:** all output branches use `printf '%s\n'` (was mixed `echo`/`printf '%b\n'`). (6) **NOTE doc:** added comment block about `~~~` fence limitation. **Methodology lesson Q-260528-AINTRP parked:** «inline review» wording in §4gzb led to architect self-review trap — root cause of 3-round NEEDS-REVISION pattern. v1.4 uses Agent sub-agent review (proper R7 substitute) before claiming PASS. |

# spec_wtiso-sh — Methodology shards + per-Q merge gate + rollback contract

> 3rd and final sub-Q under Q-260527-WTISO umbrella. Closes 6 SH adversarial findings (§4fj) deferred from WT/BW. Provides per-Q methodology write isolation + deterministic post-batch merge + 3-layer per-shard gate + transactional rollback. Allows `BATCH_PARALLEL_ENABLED=on` + `BATCH_ISOLATION_ENABLED=on` workers writing to methodology without races on §4-anchor / last-touched / append-only invariant.

## 1. Purpose + scope

**What changes:** post-batch step in `888-batch.sh` invokes new `scripts/888-shard-merger.sh` which reads `audit/shards/<batch-id>/<q-id>.md` files, sorts by Q-NNN, runs per-shard 3-layer gate, appends accepted shards to staging copy of `methodology-888.md`, atomic `mv` to main on full success.

**What workers change:** `_spawn_worker` in `888-batch.sh` passes `--shard-out audit/shards/<batch-id>/<q-id>.md` env to `claude -p` invocation. Workers (persona skills) write their `§4XX` section to that path instead of directly appending to `methodology-888.md`. Workers never touch frontmatter `last-touched` field.

**What stays the same:** `§4`-letter sequential anchor convention (extended to `§4aa..§4zz` overflow per ADR-012); YAML frontmatter format; existing `bmad-review-*` verdict format; storm framework hooks; WT/BW isolation primitives.

**v1.1 placeholder convention (ADR-011):** workers receive `$SHARD_PLACEHOLDER=§4PLACEHOLDER-<8-hex-batch-uuid-suffix>` at spawn-time (e.g., `§4PLACEHOLDER-a3f8d92e`). Workers use this exact string in their section anchor (`## 4PLACEHOLDER-a3f8d92e. <q-id> Phase N <role>`). Merger replaces this unique string with allocated letter via `sed`. The UUID suffix guarantees the placeholder never legitimately appears in any markdown content (code blocks, examples, quotes).

## 2. Storage layout

```
audit/shards/<batch-id>/
  <q-id-1>.md              # worker output, raw §4-content with placeholder anchor
  <q-id-2>.md
  ...
  <q-id-N>.md
  .merge-receipt.json      # idempotency receipt, written by merger on success
  .gate-verdicts/
    <q-id-1>.verdict       # PASS|FAIL + reason + reviewer
    <q-id-2>.verdict
    ...
```

**Receipt schema (`.merge-receipt.json`):**

```json
{
  "batch_id": "20260528-101533-WTISO-SH",
  "merged_at": "2026-05-28T10:25:14Z",
  "shards_hash": "<sha256 of sorted(shard-paths) concatenated>",
  "shards_accepted": ["q-id-1", "q-id-3"],
  "shards_rejected": ["q-id-2"],
  "pre_merge_methodology_sha": "<sha256 of methodology-888.md BEFORE merge>",
  "post_merge_methodology_sha": "<sha256 AFTER merge>",
  "merger_version": "1.0",
  "commit_sha": "<git HEAD of bmad-orchestrator at merge time>"
}
```

**ADR-SH9 SHARDPATH:** chose `audit/shards/` (preferred over `cache/shards/`). Rationale: alignment with `audit/batches/` retention policy (audit retains 30d, cache cleared on session-end); shard files are evidence of work performed, not transient scratch.

## 3. Merger algorithm (v1.1 hardened)

```
Inputs:  batch-id, methodology-path
Outputs: methodology-path (mutated atomically), .merge-receipt.json, .gate-verdicts/*

PRE-FLIGHT (new v1.1, mkdir-fix v1.2):
P1. mkdir -p audit/shards/<batch-id>/ (v1.2 BLOCK-B fix — defends first-merger-run NoEnt)
    Then acquire flock on audit/shards/<batch-id>/.merge.lock (timeout 60s, fail-closed).
    Defends against concurrent merger invocations on same batch-id.
P2. if methodology-path is symlink: resolve to canonical target (per ADR-010 SYMLINK).
    From here on, "methodology" = resolved target.
P3. install SIGINT/SIGTERM trap → cleanup staging + release flock + exit 130
    (per §5 new row "signal handling").
P4. install trap on `set -e` failure → discard staging if exists.
P5. install signal handler: trap 'rm -f $STAGING; flock -u 200; exit 130' INT TERM

MAIN:
1. read audit/shards/<batch-id>/*.md (sorted alphabetically by Q-NNN)
   - if dir missing → exit 1 with reason "shards-dir-missing"
   - if dir empty → exit 0 with WARNING event "empty-batch" (worker spawn upstream failure indicator)
2. validate each shard file: non-empty, UTF-8 well-formed, ≤ SHARD_MAX_LOC (default 5000, validated regex `^[1-9][0-9]{0,4}$`, clamped ≤ 50000)
3. read existing .merge-receipt.json
   - if present:
     a. validate JSON via `jq -e .` — fail-closed if malformed
     b. compute current sorted-shards sha256
     c. if matches receipt.shards_hash → exit 0 with warning "already-merged" event (IDEMPOTENT)
     d. else fail closed with reason "receipt-shard-mismatch" + human-readable diff dump of receipt vs current state to stderr
4. cp methodology → methodology.staging (per ADR-005)
   - if cp partial (disk full) → discard staging, exit 1 "disk-full-mid-cp"
   - fsync staging after cp to guarantee on-disk state
5. compute pre_merge_methodology_sha
6. determine next §4-anchor letter (ADR-012 overflow):
   a. scan staging for highest existing §4XX letter using regex `^## 4([a-z]+)\.`
   b. if highest is single letter and pool not exhausted (< 'z') → next single
   c. if single-letter pool exhausted ('z' allocated) → switch to two-letter starting at 'aa'
   d. if two-letter pool exhausted (>'zz') → exit 1 "anchor-pool-exhausted" (676 shards is hard cap)
7. for each shard in sorted order:
     a. re-compute shard sha256 (TOCTOU defence — must match step 1 hash, or fail-closed)
     b. RUN GATE (3 layers, §4):
        - L1 review verdict (read .gate-verdicts/<q-id>.verdict OR invoke code-reviewer Agent with `timeout 60s` + retry-once)
        - L2 diff-size check (shard LOC ≤ SHARD_MAX_LOC validated above)
        - L3 protected-section denylist (ADR-013 heuristic: `head -50 shard | grep -c '^---$'` must be 0; AND outside-fenced-block `^## [^4]\.` must be 0 — use simple state-machine to track ` ``` ` fence open/close)
     c. if any layer FAIL → write .gate-verdicts/<q-id>.verdict with reason → continue (does not abort batch — per-shard isolation)
     d. if PASS:
        - rename shard's placeholder anchor (`§4PLACEHOLDER-<batch-uuid>`, per ADR-011 unique-uuid suffix) to allocated §4XX letter via `sed -i 's/^## 4PLACEHOLDER-<batch-uuid>\./## 4gx./g'` (UUID guarantees no false-positive match in content)
        - rename sub-section headers to prefix with q-id per ADR-006: `### Phase N` → `### Q-NNN-AAA Phase N`
        - clamp shard mtime: `effective_mtime = min(stat -c %Y shard, now+60s)` (per clock-skew defence)
        - append shard body to staging methodology
        - increment §4-anchor counter (next shard gets next letter)
8. write staging frontmatter:
   - last-touched = max(effective_mtime ISO timestamps) — single-process safe + clock-skew-clamped
9. compute post_merge_methodology_sha
10. if any L3 (protected-section) violation occurred during step 7 → discard staging, exit 1 fail-closed
11. else: atomic mv staging → methodology
    - if NFS detected (`stat -f -c %T methodology` returns "nfs") → emit WARNING "nfs-non-atomic-rename" + proceed (best-effort; user warned)
    - if methodology was symlink, resolved target is target of mv (ADR-010)
12. write .merge-receipt.json (full audit)
    - acquire flock on receipt path during write
13. emit events.jsonl: shard_merge_complete with accepted/rejected counts
14. release pre-flight flock (P1)
15. exit 0
```

**Atomicity guarantee:** step 11 is single `mv` — POSIX atomic on same-FS (non-NFS). Receipt write (step 12) is post-mv → if process killed between 11 and 12, methodology is correct but receipt missing → next idempotent re-run detects missing receipt → recomputes sha and writes receipt (no double-append because shards_hash matches what's already in methodology).

**Pre-merge cleanup (dispatcher responsibility):** before batch starts, `888-batch.sh` runs `bash scripts/888-shard-cleanup.sh --batch-id <id> --retention 7d` to GC orphan `audit/shards/<old-batch-id>/` dirs older than retention. If `audit/shards/<id>/` dir exists pre-batch with `.merge-receipt.json` of different `shards_hash` than current intent → reject batch as collision (per v1.1 batch-id-collision defence).

## 4. Per-Q merge gate (3-layer composition)

| Layer | Check | Threshold | On FAIL | Cost estimate |
|---|---|---|---|---|
| L1 review verdict | code-reviewer Agent (R7 substitute) verdict on shard body OR pre-existing `.gate-verdicts/<q-id>.verdict`. **v1.1:** invoked with `timeout 60s` + retry-once on hang; second hang → FAIL with reason "L1-timeout" | verdict ∈ {PASS, PASS-WITH-FOLLOWUPS, NEEDS-REVISION-ACCEPTABLE} | log + skip shard | ~3-8s if shard pre-reviewed, ~30s if fresh review, hard cap 120s (60+60 retry) |
| L2 diff-size | wc -l shard | ≤ SHARD_MAX_LOC. **v1.1:** env validated regex `^[1-9][0-9]{0,4}$` (rejects negative/NaN/scientific notation/leading zeros), clamped ≤ 50000 hard cap. Default 5000 | log + skip shard | <100ms |
| L3 protected-section (v1.1 heuristic, ADR-013) | (a) `head -50 shard \| grep -c '^---$'` must be 0 (catches frontmatter mutation; legitimate `---` examples in body past line 50 ignored); (b) outside-fenced-block check via bash state-machine: scan line by line, toggle `in_fence` on ` ``` ` lines, ignore lines where in_fence=true, regex `^## [^4]\.` on non-fenced lines must produce 0 matches | strict | log + skip shard + ALSO trigger whole-batch reject if L3 violation = "protected_section" (per §5 invariant I3) | ~200ms (bash state-machine on shard ≤5000 LOC) |

**ADR-SH8 GATEPERF:** for `BATCH_MOCK_MODE=1` runs, L1 is skipped (mock workers don't generate review-worthy content — they emit canned shards). Mock-mode flag: `SHARD_GATE_SKIP_L1=1`. Production runs always invoke L1.

**Total overhead estimate:** N shards × ~5s avg = ~20s for 4-shard batch. Acceptable vs `sum(t1..tN)` parallelism savings (typical 60+s wall-clock saved).

## 5. Rollback contract (ADR-SH5)

**Failure modes + behavior:**

| Failure | Detection | Rollback |
|---|---|---|
| Worker crashes before writing shard | shard file missing post-batch | merger skips missing q-id (no entry to merge) |
| Worker writes corrupted markdown | L1/L2/L3 gate FAIL | shard skipped + `.gate-verdicts/<q-id>.verdict` written + main methodology UNCHANGED for that shard |
| Protected-section violation (L3) | grep regex match (state-machine, outside fences) | **whole-batch reject** — staging discarded, exit 1, no methodology mutation |
| Merger crashes mid-write (between cp and mv) | `methodology.staging` exists post-crash, but main is unchanged | dispatcher pre-batch cleanup removes stale staging files (`rm -f methodology.staging`); v1.1 also installs SIGINT/SIGTERM trap |
| Atomic mv fails (FS error) | exit non-zero | main unchanged (mv didn't complete); staging remains for forensics |
| Post-mv receipt write fails | mv done, but no `.merge-receipt.json` | idempotent re-run detects missing receipt + matching shards_hash → re-writes receipt (no double-append) |
| **v1.1: SIGINT/SIGTERM mid-merge** | trap fires | cleanup staging + release flock + exit 130; pre-batch cleanup also handles orphan `.staging` from prior crashes |
| **v1.1: Concurrent merger invocations on same batch-id** | flock contention on `audit/shards/<batch-id>/.merge.lock` | second merger blocks 60s then fails-closed exit 2 "lock-contention" (per pre-flight P1) |
| **v1.1: Disk full mid-cp** | `cp` returns non-zero OR fsync fails | discard staging, exit 1 "disk-full-mid-cp" |
| **v1.1: NFS where rename is not atomic** | `stat -f -c %T methodology` returns "nfs" | emit WARNING "nfs-non-atomic-rename" + proceed (best-effort); recommend non-NFS storage for production batches |
| **v1.1: Methodology is symlink** | pre-flight `[[ -L methodology ]]` | resolve to canonical target, mv into target (per ADR-010); preserves symlink semantics for multi-project layouts |
| **v1.1: Batch-id collision** (dir exists with conflicting receipt) | pre-batch cleanup detects `.merge-receipt.json` with different `shards_hash` | reject batch as collision, exit 1 "batch-id-collision" |
| **v1.1: Anchor pool exhausted** (>26 single-letter + >676 two-letter) | step 6 detection | exit 1 "anchor-pool-exhausted" (676 shards is hard cap; recommend split into multiple batches) |
| **v1.1: Future-dated shard mtime** (clock skew) | mtime > now+60s | clamp to `now+60s` (per ADR-004 v1.1 clock-skew defence) |
| **v1.1: Malformed receipt JSON** | `jq -e . receipt` fails | exit 1 "receipt-malformed"; do not trust receipt; human intervention required (delete receipt manually after audit) |

**Invariants:**
- I1 — main methodology never partially-written (atomicity via staging + mv)
- I2 — accepted shards isolated from rejected (each shard processed independently in step 7)
- I3 — protected-section violation = whole-batch reject (strong fail-closed; single violating worker poisons entire batch — by design to prevent silent §1-§3/§5 corruption)
- I4 — idempotent re-run = byte-identical methodology (or exit 0 with "already-merged" warning if no work to do)
- **I5 (v1.1)** — exclusive merger access per batch-id (flock pre-flight P1)
- **I6 (v1.1)** — symlink preservation (resolve to target, never replace symlink itself)
- **I7 (v1.1)** — clock-skew bounded (last-touched ≤ now+60s)
- **I8 (v1.1)** — receipt integrity (jq-validated JSON or fail-closed)

## 6. ADRs (SH1-SH9)

### ADR-001 (SH1 IDEM) — Idempotency hashing scheme

**Decision:** sha256 of sorted shard file paths (NOT contents) — concatenated with NUL separator, hashed once. Cached in `.merge-receipt.json#shards_hash`.

**Why paths not contents:** content can vary between runs of same worker (timestamp embedded in section header). Paths are stable identifiers. Re-running merger on same shard SET produces same hash regardless of shard internal content drift.

**Alternatives rejected:**
- Content hash → false-positive non-idempotency if shard re-generated identically with new timestamp
- Per-shard hash → unnecessary granularity; we want batch-level idempotency

### ADR-002 (SH2 SECTNO) — §4 section-number assignment (amended v1.1 by ADR-011)

**Decision (v1.1):** workers receive `$SHARD_PLACEHOLDER=§4PLACEHOLDER-<8-hex-batch-uuid-suffix>` at spawn-time (e.g., `§4PLACEHOLDER-a3f8d92e`). Workers write shards with placeholder anchor `## 4PLACEHOLDER-a3f8d92e. <Q-NNN> Phase N <role>` — merger renames to `## 4gx`, `## 4gy`, etc. based on next-letter scan of staging.

**Rationale:** workers don't need to know each other's anchor letters. Merger has global view. Determinism = sorted Q-NNN order → deterministic letter assignment. **UUID suffix (v1.1) eliminates fenced-block false-positive risk** — the unique batch-suffixed string never legitimately appears in any markdown content (code blocks, examples, quotes).

**Implementation:** `sed -i "s/^## 4PLACEHOLDER-${BATCH_UUID}\. /## 4gx. /g" staging-shard-content` (one sed per shard in step 7d). The `g` flag is safe because UUID-suffixed placeholder is unique per batch.

**v1.0 original (rejected by edge-case-hunter):** `## 4PLACEHOLDER.` literal — could collide with example content inside fenced code blocks. Superseded by v1.1.

### ADR-003 (SH3 APPEND) — Append-only invariant enforcement (amended v1.1 by ADR-013)

**Decision (v1.1):** L3 gate uses **heuristic + state-machine** instead of blind regex:
- **Heuristic (frontmatter check):** `head -50 shard | grep -c '^---$'` must be 0. Workers write shard body only (never frontmatter at top), so any `^---$` in first 50 lines = violation. Legitimate YAML examples in body past line 50 ignored.
- **State-machine (protected-section check):** bash state-machine scans shard line by line, toggles `in_fence` boolean on ` ``` ` lines, checks `^## [^4]\.` regex ONLY on lines where `in_fence=false`. Zero matches = pass.

**Worker discipline (unchanged):** persona SKILL.md writes to `$SHARD_OUT` env var path if set, else appends to methodology-888.md (legacy sequential path). L3 catches violations even if persona misbehaves.

**v1.0 original (rejected by edge-case-hunter):** blind regex `grep -c '^---$' > 0` + `grep -E '^## [0-9]\.'` — false-positive on legitimate frontmatter/section examples in code blocks. Superseded by v1.1.

### ADR-004 (SH4 LASTTOUCH) — last-touched updated by merger only

**Decision:** workers never write `last-touched` frontmatter field. Merger computes `max(shard_mtime ISO)` across accepted shards and writes single update in step 8.

**Sequential mode:** unchanged — workers in sequential mode write directly to main methodology including last-touched (existing path).

### ADR-005 (SH5 ROLLBACK) — staging + atomic mv

**Decision:** see §5 above. cp → append-staging → atomic mv. Whole-batch reject on L3 protected violation; per-shard skip on L1/L2.

### ADR-006 (SH6 CONFLICT) — q-id conflict resolution (amended v1.1 — q-id prefix on sub-headers)

**Decision (v1.1):** if two shards have same q-id (e.g., same Q-NNN split across two parallel phase invocations), merger appends both under same allocated §4 anchor in sorted shard-path order. Receipt records both as accepted. NO duplicate anchor letter assigned. **v1.1:** sub-section headers within each shard are prefixed with q-id to prevent collision: `### Phase 1 analyst` → `### Q-NNN-AAA Phase 1 analyst`.

**Anti-corner:** if both shards have identical full content → merger logs warning and skips second (no duplicate text).

**Implementation hint (v1.2 — state-machine for BLOCK-C):** group sorted shards by q-id before letter-assignment step; group becomes one §4 section with multiple sub-sections (`### Q-NNN-AAA Phase 1 analyst` + `### Q-NNN-AAA Phase 2 architect` headers within). Sub-header rename **must use state-machine** (NOT blind sed), same protection as ADR-013 L3 check:

```bash
# v1.0/v1.1 BUG: blind sed rewrites `### Phase 1` inside fenced code blocks
# v1.2 FIX: state-machine — only rewrite when in_fence=0
in_fence=0
while IFS= read -r line; do
  if [[ "$line" =~ ^\`\`\` ]]; then
    in_fence=$((1 - in_fence))
    printf '%s\n' "$line"
    continue
  fi
  if [[ $in_fence -eq 0 ]] && [[ "$line" =~ ^###[[:space:]](Phase[[:space:]][0-9](\.5)?[[:space:]].*) ]]; then
    # v1.4 FIX: capture remainder (.*) so role suffix isn't dropped (was: 'Phase 1 analyst' → 'Phase 1 ')
    printf '### %s %s\n' "$Q_ID" "${BASH_REMATCH[1]}"
  else
    printf '%s\n' "$line"
  fi
done < shard.in > shard.out
```

State-machine identical to ADR-013 design — single `in_fence` toggle on ` ``` ` lines, regex only applied when `in_fence=0`.

### ADR-007 (SH7 PATCHCNT) — per-Q patch-counter scope

**Decision:** **sibling** new hook `~/.claude/hooks/storm/per-q-patch-counter.sh`, not extension to existing `patch-counter.sh`. Rationale: existing counter is per-session global (correct for sequential); per-Q is fundamentally different scope (per-shard cumulative). Naming sibling preserves backward-compat + lets sequential mode keep using existing counter unchanged.

**Storage:** `audit/shards/<batch-id>/<q-id>.patches` (text file, incremented by hook).

**Reset:** at shard create (worker invocation start). No reset between batch invocations (each batch-id is unique scope).

### ADR-008 (SH8 GATEPERF) — gate runtime budget

**Decision:** L1 (review) skipped in mock-mode via `SHARD_GATE_SKIP_L1=1` env. L2/L3 always run (<200ms total). Production gate ~5s/shard avg.

### ADR-009 (SH9 SHARDPATH) — `audit/shards/` chosen

See §2 above.

### ADR-010 (v1.1 BLOCK-1 SYMLINK) — Methodology symlink handling

**Decision:** **transparent resolve** — if `methodology-888.md` is a symlink (`[[ -L methodology-888.md ]]`), merger resolves to canonical target via `readlink -f` and performs all read/cp/mv operations on the canonical target. The symlink itself is never replaced.

**Why transparent (not fail-closed):** multi-project BMad layouts commonly symlink methodology to a canonical location (e.g., `~/.claude/skills/888/methodology-888.md` → `/home/server/odyssey/methodology.canonical.md`). Fail-closed would break these legitimate setups. Transparent resolve preserves symlink semantics while still providing atomic mv on the actual target.

**Implementation:** in step P2 of pre-flight:
```bash
if [[ -L "$METHODOLOGY" ]]; then
  CANONICAL=$(readlink -f "$METHODOLOGY")
  echo "symlink-resolved: $METHODOLOGY → $CANONICAL" >&2
  METHODOLOGY="$CANONICAL"
fi
```

**Caveat:** if `readlink -f` fails (broken symlink target) → exit 1 "symlink-broken-target".

**Alternatives rejected:**
- Fail-closed reject — too strict; breaks multi-project setups
- mv-through-symlink (delete symlink + create regular file at symlink path) — loses symlink

### ADR-011 (v1.1 BLOCK-2 PLACEHOLDER-UUID) — UUID-suffixed placeholder anchor

**Decision:** workers receive `$SHARD_PLACEHOLDER=§4PLACEHOLDER-<8-hex-batch-uuid>` at spawn-time. The 8-hex suffix is derived from the batch-id (e.g., `printf '%s' "$BATCH_ID" | sha256sum | cut -c1-8`). Workers use this exact string in their section anchor.

**Why UUID-suffixed:** v1.0 used literal `§4PLACEHOLDER.` which could collide with examples in fenced code blocks (e.g., SKILL.md showing «вот так не надо: `## 4PLACEHOLDER. Q-NNN`»). UUID suffix guarantees the placeholder is unique per batch and never legitimately appears in any markdown content (no human would write `§4PLACEHOLDER-a3f8d92e` in an example).

**Merger rename (step 7d):** `sed -i "s/^## 4PLACEHOLDER-${BATCH_UUID}\. /## 4gx. /g" staging-shard-content`. The `g` flag is safe because UUID-suffixed placeholder is globally unique per batch.

**Worker integration:** `_spawn_worker` in `888-batch.sh` exports:
```bash
export SHARD_PLACEHOLDER="§4PLACEHOLDER-$(printf '%s' "$BATCH_ID" | sha256sum | cut -c1-8)"
```

Persona SKILL.md / shard-write helper reads `$SHARD_PLACEHOLDER` and uses it in section anchor template.

**Alternatives rejected:**
- Markdown state-machine parser for ADR-002 sed — bash state-machine for markdown is error-prone; UUID-suffix is simpler and more robust
- Hard escape sequence (e.g., `<!--PLACEHOLDER-->`) — non-markdown; breaks reader habit

### ADR-012 (v1.1 BLOCK-3 ANCHOR-OVERFLOW) — §4 anchor letter namespace

**Decision:** **two-letter overflow** — when single-letter pool exhausted (`§4a..§4z` all allocated, 26 entries), continue with two-letter starting at `§4aa..§4zz` (676 additional entries, total 702 capacity). Hard cap: 676 shards per batch (recommend split into multiple batches if exceeded).

**Why two-letter (not numeric):** preserves existing alphabetic ordering convention used throughout methodology-888.md (readers know `§4a` < `§4b` < `§4c` < ...). Two-letter naturally extends: `§4z` < `§4aa` < `§4ab` < ... < `§4zz`. Numeric `§4-001..§4-999` would break reader habit + require migration of existing entries.

**Implementation (v1.2 — sort-order fix for BLOCK-A):**
```bash
# Step 6 of merger algorithm:
# v1.0/v1.1 BUG: lexical `sort | tail -1` returns 'z' even when 'aa' exists (because 'aa' < 'z' lexically)
# v1.2 FIX: length-then-alpha sort puts longest-then-highest-alpha last
# v1.3 FIX: LC_COLLATE=C inlined on sort invocation (bare assignment was unexported, no effect on subshell sort)
# v1.4 FIX: `|| true` on grep — empty methodology (first-shard-ever) returns rc 1 which would abort
#          under `set -o pipefail`. HIGHEST="" is the intended first-shard signal — next_anchor_letter
#          maps "" → "a" downstream. Implementer MUST enable `set -o pipefail` for the script overall.
HIGHEST=$( { grep -oE '^## 4[a-z]+\.' "$STAGING" || true; } | sed 's/^## 4//; s/\.$//' \
  | awk '{print length, $0}' | LC_COLLATE=C sort -k1,1n -k2,2 | tail -1 | cut -d' ' -f2-)
# v1.4 FIX: explicit rc check on next_anchor_letter — rc 1 (zz exhausted) or rc 2 (malformed)
#          must fail-closed, not silently produce empty NEXT (which would corrupt anchor to `## 4.`).
if ! NEXT=$(next_anchor_letter "$HIGHEST"); then
  case $? in
    1) echo "anchor-pool-exhausted (z..zz used, 702 shards total cap reached)" >&2; exit 1 ;;
    2) echo "next_anchor_letter: malformed HIGHEST='$HIGHEST' (not [a-z]+ or len>2)" >&2; exit 1 ;;
    *) echo "next_anchor_letter: unexpected rc" >&2; exit 1 ;;
  esac
fi
# 'z' → 'aa', 'zz' → exit 1

next_anchor_letter() {
  local cur="$1"
  # v1.3: direct printf '%b' avoids xargs backslash-stripping bug found in v1.0/v1.1
  # v1.4: dead `local _int_to_letter()` helper removed (was invalid bash syntax — local on function decl)
  # v1.4: output style unified to `printf '%s\n'` across all branches
  case "$cur" in
    "")  printf '%s\n' "a"; return 0 ;;       # first shard ever
    "z")  printf '%s\n' "aa"; return 0 ;;      # single → double overflow
    "zz") return 1 ;;                           # hard cap (caller MUST check rc — see line 326)
    [a-y])
      # Single-letter increment via ASCII arithmetic: 'a' → 'b', 'y' → 'z'
      # NOTE: printf '%d' "'$cur" is ASCII-only — safe for [a-z] inputs (verified in case pattern above)
      printf '%s\n' "$(printf '%b' "$(printf '\%03o' $(($(printf '%d' "'$cur") + 1)))")"
      return 0
      ;;
    [a-z][a-z])
      # v1.3 FIX (BLOCK-A regression): explicit two-letter increment
      # v1.4: output style unified to `printf '%s\n'` (was mixed with `echo` in v1.3 simple cases)
      # Semantics: rightmost-first carry, 'aa'→'ab', 'az'→'ba', 'bz'→'ca', ..., 'zy'→'zz', 'zz'→fail
      local hi="${cur:0:1}" lo="${cur:1:1}"
      if [[ "$lo" != "z" ]]; then
        # Increment low digit only (no carry)
        local lo_next
        lo_next=$(printf '%b' "$(printf '\%03o' $(($(printf '%d' "'$lo") + 1)))")
        printf '%s\n' "${hi}${lo_next}"
        return 0
      fi
      # lo == 'z' → carry to high digit, reset lo to 'a'
      if [[ "$hi" == "z" ]]; then
        # 'zz' case already short-circuited above, but defensive: hi=z + lo=z = exhausted
        return 1
      fi
      local hi_next
      hi_next=$(printf '%b' "$(printf '\%03o' $(($(printf '%d' "'$hi") + 1)))")
      printf '%s\n' "${hi_next}a"
      return 0
      ;;
    *)
      # Defensive: malformed input (non-[a-z]+, length>2, mixed case, empty after the "" case) → fail-closed
      return 2
      ;;
  esac
}

# v1.4 NOTE — fence handling: ADR-006 and ADR-013 state-machines toggle on ` ``` ` only.
# CommonMark also permits `~~~` fences. By worker convention only triple-backtick is used;
# `~~~` is not a recognized shard fence. Workers writing `~~~` blocks will be processed
# as if they were body content (potentially triggering false-positive on L3 if `## [^4]\.`
# appears inside). This is documented limitation — not a bug. Future: extend state-machine
# to accept both fence types if `~~~` adoption increases.
```

**Migration concern:** existing single-letter entries `§4a..§4gv` remain valid; merger only allocates new letters. No retroactive renaming.

**Alternatives rejected:**
- Hard cap N≤26 — too restrictive for large initiatives
- Numeric `§4-NNN` — breaks reader habit + migration cost

### ADR-013 (v1.1 BLOCK-4 L3-HEURISTIC) — L3 gate heuristic + state-machine

**Decision:** L3 (protected-section denylist) uses two checks:
- **Heuristic for frontmatter:** `head -50 shard | grep -c '^---$'` must be 0. Workers write shard body only (never frontmatter at top), so any `^---$` in the first 50 lines = violation. Legitimate YAML examples in body past line 50 are ignored (acceptable because frontmatter is always at the top of a methodology file).
- **State-machine for protected sections:** bash state-machine scans shard line by line, toggles `in_fence` boolean on ` ``` ` lines, checks regex `^## [^4]\.` ONLY on lines where `in_fence=false`. Zero matches = pass.

**Why heuristic + state-machine (not full parser):**
- Frontmatter at top is a strict positional invariant — first 50 lines covers all real cases (frontmatter is usually 10-30 lines).
- State-machine for fenced blocks is simple (one variable toggle) and reliable for the L3 use case (no nested fences need to be handled — markdown spec disallows nested triple-backtick).

**Reference state-machine (bash):**
```bash
in_fence=0
violations=0
while IFS= read -r line; do
  if [[ "$line" =~ ^\`\`\` ]]; then
    in_fence=$((1 - in_fence))
    continue
  fi
  if [[ $in_fence -eq 0 ]] && [[ "$line" =~ ^##\ [^4]\. ]]; then
    violations=$((violations + 1))
    echo "L3-violation: $line" >&2
  fi
done < "$shard"
[[ $violations -eq 0 ]]  # exit non-zero if any violation
```

**Alternatives rejected:**
- Blind regex (v1.0) — false-positives on legitimate examples in code blocks; rejected by edge-case-hunter
- Full markdown parser (e.g., `pandoc`) — overengineered; adds dependency
- AST-based check — bash state-machine sufficient for L3 use case

## 7. Cross-impact tracing

| File | Change | Impact |
|---|---|---|
| `scripts/888-batch.sh` | post-batch invoke `888-shard-merger.sh`; pass `--shard-out` env to workers | central orchestration entrypoint |
| `scripts/888-batch.sh:_spawn_worker` | export `SHARD_OUT=audit/shards/<batch-id>/<q-id>.md` for child `claude -p` | child persona inherits env |
| `scripts/888-batch-commit-policy.sh` | adds receipt path to commit message; receipt is part of commit | commit links shard merge to git SHA |
| `scripts/888-shard-merger.sh` (new) | merger algorithm §3 | core new logic |
| `scripts/888-shard-cleanup.sh` (new) | pre-batch GC orphan shard dirs | called from 888-batch.sh pre-spawn |
| `~/.claude/hooks/storm/per-q-patch-counter.sh` (new) | per-Q increment | sibling to existing patch-counter |
| `~/.claude/skills/888-persona-*/SKILL.md` (6 personas) | write to `$SHARD_OUT` if set, else append to methodology | persona behavior conditional on env |
| `scripts/regression-smoke.sh` | add SH1-SH6 scenarios | smoke-level coverage |
| `tests/wtiso/test-shard-*.sh` (6 RED) | new test files | Iron Law per RED test plan |

**Sequential mode regression check:** with `BATCH_PARALLEL_ENABLED=off`, `$SHARD_OUT` not set → personas use legacy append-to-methodology path → identical behavior to pre-SH. RED test: `tests/wtiso/test-shard-sequential-mode-unchanged.sh`.

**Storm framework cross-impact:** intent-detector / patch-counter (existing) / merge-guard / destructive-guard / audit-trail / code-gate — none modified directly. Per-Q patch-counter is sibling, not replacement.

## 8. Iron Law test plan (12 RED tests committed BEFORE implementation — v1.1 expanded from 6)

### Original 6 (RED-SH-1..6 from v1.0)

| RED test | What it asserts | Initial state |
|---|---|---|
| `test-shard-collision-isolation.sh` (RED-SH-1) | 2 workers write conflicting placeholder anchors (different q-id, both with UUID-suffixed placeholder) → merger renames per Q-NNN sort → 0 final-state collisions | RED until merger ADR-002 + ADR-011 land |
| `test-shard-idempotent-rerun.sh` (RED-SH-2) | merger invoked 2× on same batch-id → second = byte-identical methodology + `already-merged` warning event | RED until merger ADR-001 lands |
| `test-shard-partial-rollback.sh` (RED-SH-3) | synthetic shard-3 violates L3 → whole-batch reject; synthetic shard-3 fails L1 → shard-3 only skipped | RED until merger gate composition lands |
| `test-shard-append-only.sh` (RED-SH-4) | synthetic shard mutating frontmatter or §1-§3/§5 → L3 reject + reason `append_only_violation`; synthetic shard with `---` example in fenced code block AFTER line 50 → PASS (no false-positive) | RED until L3 gate v1.1 ADR-013 lands |
| `test-shard-last-touched.sh` (RED-SH-5) | 4 workers w/ timestamps T1<T2<T3<T4 → post-merge `last-touched == T4`; future-dated T5 (now+120s) → clamped to now+60s | RED until ADR-004 + clock-skew defence land |
| `test-shard-q-id-conflict.sh` (RED-SH-6) | 2 shards same q-id → both appended under same §4 anchor in sorted shard-path order; sub-section headers prefixed with q-id (no duplicate `### Phase 1 analyst`) | RED until ADR-006 v1.1 lands |

### New 6 from §4gy gap coverage (RED-SH-7..12, v1.1)

| RED test | What it asserts | Initial state |
|---|---|---|
| `test-shard-symlink-methodology.sh` (RED-SH-7) | methodology-888.md is symlink → merger resolves to target, mv writes to target, symlink preserved post-merge | RED until ADR-010 SYMLINK lands |
| `test-shard-anchor-overflow.sh` (RED-SH-8) | batch with 27 shards → first 26 get `§4a..§4z`, 27th gets `§4aa`; 700-shard batch → exits cleanly at `§4zz`; 701-shard → exit 1 "anchor-pool-exhausted" | RED until ADR-012 ANCHOR-OVERFLOW lands |
| `test-shard-fenced-block-false-positive.sh` (RED-SH-9) | shard with example `## 4PLACEHOLDER-deadbeef` inside fenced code block (different UUID than current batch) → NOT renamed by sed (different UUID); shard with example `## 1.` in fenced block → NOT rejected by L3 state-machine | RED until ADR-011 + ADR-013 land |
| `test-shard-empty-and-missing.sh` (RED-SH-10) | empty `audit/shards/<batch-id>/` dir → exit 0 with `empty-batch` warning event; missing dir entirely → exit 1 `shards-dir-missing`; binary garbage shard file → L2 reject with reason `non-utf8` | RED until §3 step 1+2 validation lands |
| `test-shard-injection-batch-id.sh` (RED-SH-11) | shell-injection batch-id `audit/shards/$(rm -rf ~)/...` → input validation rejects before any FS op; batch-id with `..` → rejected; batch-id with NUL bytes → rejected | RED until input-validation hardening lands |
| `test-shard-concurrent-merger.sh` (RED-SH-12) | two merger processes invoked on same batch-id within 5s → second blocks on flock, fails-closed after 60s timeout with exit 2 "lock-contention"; methodology unmodified by second process | RED until pre-flight P1 flock lands |

### Additional regression

- `test-shard-sequential-mode-unchanged.sh` — `BATCH_PARALLEL_ENABLED=off` → no shard code path invoked, exit codes match baseline (NOT counted in 12 — regression smoke instead).
- `test-shard-nfs-warning.sh` (optional) — synthetic NFS mount point detection → emit warning but proceed (best-effort).
- `test-shard-receipt-malformed.sh` (optional) — partial-write `.merge-receipt.json` → fail-closed with `receipt-malformed`.

**Storm T1 coverage:** SH does not introduce new T1 (feature_intent) — it's infrastructure under existing WTISO umbrella spec. No separate `feature_wtiso-sh_storm.md`.

## 9. Mini threat model (security_critical=false → no bmad-threat-model invocation)

| # | Threat | Layer | Mitigation |
|---|---|---|---|
| T1 | Shard injection (worker writes `<script>` or `</methodology>` markup) | merger | raw markdown append — no special markup interpretation; receipt sha256 covers content integrity |
| T2 | Merge-time TOCTOU (`audit/shards/<batch-id>/` modified after gate but before merge) | merger | shard sha256 computed at gate time + recompute pre-mv → mismatch = fail-closed |
| T3 | Cleanup-after-fail (orphan `audit/shards/<batch-id>/`) | dispatcher | `888-shard-cleanup.sh` pre-batch with 7d retention; `audit/` retention policy aligned |
| T4 | Receipt forgery (malicious actor writes fake `.merge-receipt.json` to bypass idempotency check) | filesystem perms | `audit/shards/` dir 700 perms; receipt write only by merger uid; threat acceptable for single-user solo-operator scenario |
| T5 | Gate bypass via env (`SHARD_GATE_SKIP_L1=1` in production) | dispatcher | env strictly local to mock-mode invocation; production batch.sh **explicitly unsets** variable before invocation (regression test `test-t5-env-bypass.sh` asserts unset behavior); `.envrc`/`direnv`/parent-shell export blocked by dispatcher pre-flight env scrub; SHARD_MAX_LOC validated via regex `^[1-9][0-9]{0,4}$` + clamp ≤50000 (rejects `999999999` bypass attempt) |

**No PII, no auth, no crypto, no money flow** — security_critical=false confirmed.

**Cross-link to umbrella threat-model:** WTISO-WT covered .git/index race + branch hijack (T/E STRIDE); WTISO-BW covered cross-worker FS read + DoS via fork-bomb + ENV var leakage (I/D); SH adds methodology corruption layer (Tampering @ merge step). Full umbrella STRIDE coverage: T(WT+SH) / I(BW) / D(BW) / E(WT) / S(N/A) / R(N/A).

## 10. Acceptance criteria

- AC1 — `tests/wtiso/test-shard-*.sh` (6 RED → GREEN after implementation)
- AC2 — `test-shard-sequential-mode-unchanged.sh` GREEN
- AC3 — `bash scripts/evals/wtiso-sh-baseline.sh` produces `evals/baselines/wtiso-sh-baseline-2026-05-28.json` with non-zero collisions/losses in baseline (proves test fixtures detect bugs)
- AC4 — `bash scripts/evals/wtiso-sh-replay.sh` post-impl produces M1-M5 = target values
- AC5 — `scripts/regression-smoke.sh` extended w/ SH scenarios → 0 regressions in existing 20/20
- AC6 — code-review-gate passes on impl (via Agent code-reviewer R7 substitute)
- AC7 — manual end-to-end: 4-Q fake parallel batch → methodology contains 4 new §4 sections in sorted Q-NNN order, frontmatter last-touched == max, no duplicate anchors

## 11. Handoff to implementer (Phase 2.5)

**Order of operations:**
1. RED-SH-1..6 test stubs (committed FIRST, before any implementation)
2. `scripts/888-shard-merger.sh` (core merger §3)
3. `scripts/888-shard-cleanup.sh` (pre-batch GC)
4. `scripts/888-batch.sh` integration (`--shard-out` env + post-batch merger invoke)
5. Persona SKILL.md updates (6 personas — conditional `$SHARD_OUT` write)
6. `~/.claude/hooks/storm/per-q-patch-counter.sh` (sibling hook)
7. `scripts/regression-smoke.sh` extension
8. RED → GREEN walk-through (each RED test passes after corresponding impl piece lands)
9. `evals/baselines/wtiso-sh-baseline-2026-05-28.json` (baseline run pre-merge of full impl)
10. Replay post-impl, assert M1-M5 targets

**Implementer tier hint:** M (single-file mostly, but ≥6 personas touched). If `IMPLEMENTER_TIER_MAX=M` set → in-scope. Else delegate to external `/featurenew` or BMB.

**Code-review-gate (mandatory after impl):** code-reviewer Agent (R7 substitute) opus on full diff.

**No threat-model invocation required** (security_critical=false; mini threat-model §9 sufficient).
