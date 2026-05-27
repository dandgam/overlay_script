#!/usr/bin/env bash
# RED-SH-16 (v2.1 B14): content-aware idempotency hash.
# Two assertions:
#   1. Two shard sets with identical sorted path lists but different file
#      contents must yield DIFFERENT `shards_sha256` (rules out a path-only
#      formula which would falsely collide).
#   2. In-place mutation of a shard between merger invocations on the same
#      batch-id MUST be detected: the receipt-conflict path triggers
#      (RC=3) instead of falsely treating the second call as idempotent.
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"
if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-16: merger '$MERGER' not implemented yet" >&2
    exit 1
fi

# shellcheck disable=SC1091
source tests/wtiso/_lib/fixture-helpers.sh

WS="${TMPDIR:-/tmp}/wtiso-red-sh-16-$$"
trap 'rm -rf "$WS"' EXIT INT TERM

wtiso_init_workspace "$WS" > /dev/null

# ─── Assertion 1: A vs B with same paths, different content ─────────────
# Source the merger to access `_compute_shards_sha256` and `_init_anchor_table`.
# shellcheck disable=SC1090
source "$(realpath "$MERGER")"
_init_anchor_table

mkdir -p "$WS/setA" "$WS/setB"
echo "x" > "$WS/setA/shard1.md"
echo "y" > "$WS/setB/shard1.md"

# Path strings to feed the hash function. The formula incorporates path
# basename, so we use identical basenames in both calls (just different
# directories). To match production exactly, simulate the same sorted
# path list relative to setA/setB.
hash_A=$(printf '%s\n' "$WS/setA/shard1.md" | _compute_shards_sha256)
hash_B=$(printf '%s\n' "$WS/setB/shard1.md" | _compute_shards_sha256)

if [[ -z "$hash_A" || -z "$hash_B" ]]; then
    echo "RED-SH-16 FAIL: empty hash output (compute helper broken)" >&2
    exit 1
fi
if [[ "$hash_A" == "$hash_B" ]]; then
    echo "RED-SH-16 FAIL: content-aware hash collided across different file bodies" >&2
    echo "  hash_A=$hash_A" >&2
    echo "  hash_B=$hash_B" >&2
    exit 1
fi

# Additionally, with IDENTICAL paths and IDENTICAL bytes, the hash MUST
# match (deterministic). Reuse same file path twice — identical content.
echo "z" > "$WS/setA/shard1.md"
hash_Az=$(printf '%s\n' "$WS/setA/shard1.md" | _compute_shards_sha256)
hash_Az2=$(printf '%s\n' "$WS/setA/shard1.md" | _compute_shards_sha256)
if [[ -z "$hash_Az" || "$hash_Az" != "$hash_Az2" ]]; then
    echo "RED-SH-16 FAIL: identical inputs produced different hashes (non-deterministic)" >&2
    echo "  hash_Az=$hash_Az hash_Az2=$hash_Az2" >&2
    exit 1
fi

# ─── Assertion 2: in-place shard mutation detected on re-run ────────────
BATCH_ID="2026-05-28T12:30:00Z-deadbe16"
wtiso_make_shard_raw "$WS" "$BATCH_ID" "Q-MUT.md" "$(cat <<'EOF'
<!-- q-id: Q-MUT -->
## 4PLACEHOLDER-deadbe16. content-aware hash test (v1)

Original body content — version 1.
EOF
)"

rc=0
wtiso_invoke_merger "$WS" "$BATCH_ID" > /tmp/wtiso-red-sh-16-run1.log 2>&1 || rc=$?
if [[ $rc -ne 0 ]]; then
    echo "RED-SH-16 FAIL: first invocation rc=$rc (expected 0)" >&2
    cat /tmp/wtiso-red-sh-16-run1.log >&2
    exit 1
fi

receipt="$WS/audit/shards/$BATCH_ID/.merge-receipt.json"
first_hash=$(jq -r '.shards_sha256' "$receipt")

# In-place mutate the shard (different bytes, same filename / path).
cat > "$WS/audit/shards/$BATCH_ID/Q-MUT.md" <<'EOF'
<!-- q-id: Q-MUT -->
## 4PLACEHOLDER-deadbe16. content-aware hash test (v2 — MUTATED)

Mutated body content — version 2, byte-different.
EOF

rc=0
wtiso_invoke_merger "$WS" "$BATCH_ID" > /tmp/wtiso-red-sh-16-run2.log 2>&1 || rc=$?

# Spec §3.1 P3: receipt exists, hash differs → RC=3 receipt_conflict.
# This proves the merger did NOT falsely treat the mutated batch as
# idempotent. A path-only hash would yield rc=0 + silent drop of mutation.
if [[ $rc -ne 3 ]]; then
    echo "RED-SH-16 FAIL: expected RC=3 (receipt_conflict on content mutation), got $rc" >&2
    cat /tmp/wtiso-red-sh-16-run2.log >&2
    exit 1
fi

# Verify the merger logged a conflict (current hash != prior hash).
if ! grep -q 'receipt_conflict' /tmp/wtiso-red-sh-16-run2.log; then
    echo "RED-SH-16 FAIL: receipt_conflict log line missing" >&2
    cat /tmp/wtiso-red-sh-16-run2.log >&2
    exit 1
fi

echo "RED-SH-16 GREEN: content-aware hash differs on body change; in-place mutation → RC=3"
exit 0
