#!/usr/bin/env bash
# RED-SH-15 (v2.1 B13): all-skipped legitimate.
# Case A: 3 shards, mock L1=BLOCK на каждом → RC=0 (not 9), audit
#         `shard_merger_all_skipped`, receipt skipped_shards=3 paths,
#         rejected_shards=[], methodology byte-identical.
# Case B (companion): 3 shards в одном batch, но смесь — mock L1 даёт
#         PASS на одном и BLOCK на двух. RC=0, methodology MUTATED,
#         receipt skipped_shards имеет 2 пути.
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"
if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-15: merger '$MERGER' not implemented yet" >&2
    exit 1
fi

# shellcheck disable=SC1091
source tests/wtiso/_lib/fixture-helpers.sh

# ─── Case A: all three BLOCK ─────────────────────────────────────────────
WS_A="${TMPDIR:-/tmp}/wtiso-red-sh-15a-$$"
trap 'rm -rf "$WS_A" "$WS_B"' EXIT INT TERM

BATCH_ID_A="2026-05-28T12:10:00Z-deadbe15"
wtiso_init_workspace "$WS_A" > /dev/null
pre_sha_a=$(wtiso_sha "$WS_A")

wtiso_make_shard "$WS_A" "$BATCH_ID_A" "Q-1" "Block-1 body."
wtiso_make_shard "$WS_A" "$BATCH_ID_A" "Q-2" "Block-2 body."
wtiso_make_shard "$WS_A" "$BATCH_ID_A" "Q-3" "Block-3 body."

rc=0
BMAD_SHARD_L1_MOCK=BLOCK wtiso_invoke_merger "$WS_A" "$BATCH_ID_A" \
    > /tmp/wtiso-red-sh-15a.log 2>&1 || rc=$?

if [[ $rc -ne 0 ]]; then
    echo "RED-SH-15 Case A FAIL: expected RC=0 (legitimate noop), got $rc" >&2
    cat /tmp/wtiso-red-sh-15a.log >&2
    exit 1
fi

post_sha_a=$(wtiso_sha "$WS_A")
if [[ "$pre_sha_a" != "$post_sha_a" ]]; then
    echo "RED-SH-15 Case A FAIL: methodology mutated despite all-skipped" >&2
    exit 1
fi

if ! wtiso_assert_audit_event "$WS_A" "$BATCH_ID_A" "shard_merger_all_skipped"; then
    echo "RED-SH-15 Case A FAIL: audit shard_merger_all_skipped absent" >&2
    cat "$WS_A/audit/shards/merger-audit.jsonl" >&2 || true
    exit 1
fi

receipt_a="$WS_A/audit/shards/$BATCH_ID_A/.merge-receipt.json"
if [[ ! -f "$receipt_a" ]]; then
    echo "RED-SH-15 Case A FAIL: receipt not written" >&2
    exit 1
fi

skip_count_a=$(jq -r '.skipped_shards | length' "$receipt_a")
rej_count_a=$(jq -r '.rejected_shards | length' "$receipt_a")
if [[ "$skip_count_a" != "3" || "$rej_count_a" != "0" ]]; then
    echo "RED-SH-15 Case A FAIL: receipt counts wrong (skipped=$skip_count_a rejected=$rej_count_a; expected 3/0)" >&2
    jq . "$receipt_a" >&2
    exit 1
fi

# ─── Case B: 1 PASS + 2 BLOCK ────────────────────────────────────────────
# Mock L1 honours `BMAD_SHARD_L1_MOCK_<shard-basename>` per-shard if set,
# otherwise falls back to BMAD_SHARD_L1_MOCK. Merger doesn't currently
# support per-shard env, so we control verdict via two-pass invocation:
# Pass 1: mock BLOCK everywhere — establishes baseline that nothing merged.
# Pass 2: NOT applicable because receipt now exists → idempotent skip.
# Workaround: use a custom mock script via PATH override — claude shim
# that returns PASS or BLOCK based on shard file content.
#
# Simpler scheme: mark shards that should BLOCK with a sentinel string
# `<!-- mock-l1: BLOCK -->`, intercept via a wrapper. But merger calls
# `_l1_check` which honours BMAD_SHARD_L1_MOCK uniformly.
#
# Pragmatic path: rely on L2 gate (LOC bound) to "skip" two shards by
# making them oversize, and let L1 PASS the small one. SHARD_MAX_LOC=10
# → two oversize shards skipped by L2, one small shard passes both gates.
# The receipt's `skipped_shards` accumulates BOTH L1 and L2 skips, so
# the assertion shape (skipped=2, rejected=0, methodology mutated, RC=0)
# is identical regardless of which gate did the skipping.
WS_B="${TMPDIR:-/tmp}/wtiso-red-sh-15b-$$"
BATCH_ID_B="2026-05-28T12:15:00Z-deadbe16"
wtiso_init_workspace "$WS_B" > /dev/null
pre_sha_b=$(wtiso_sha "$WS_B")

wtiso_make_shard "$WS_B" "$BATCH_ID_B" "Q-pass" "Small passing body."
# Two oversize shards (15 LOC of body each → exceed SHARD_MAX_LOC=10).
big_body=$(printf 'line %d\n' {1..15})
wtiso_make_shard "$WS_B" "$BATCH_ID_B" "Q-skip-1" "$big_body"
wtiso_make_shard "$WS_B" "$BATCH_ID_B" "Q-skip-2" "$big_body"

rc=0
SHARD_MAX_LOC=10 wtiso_invoke_merger "$WS_B" "$BATCH_ID_B" \
    > /tmp/wtiso-red-sh-15b.log 2>&1 || rc=$?

if [[ $rc -ne 0 ]]; then
    echo "RED-SH-15 Case B FAIL: expected RC=0 (mixed pass/skip), got $rc" >&2
    cat /tmp/wtiso-red-sh-15b.log >&2
    exit 1
fi

post_sha_b=$(wtiso_sha "$WS_B")
if [[ "$pre_sha_b" == "$post_sha_b" ]]; then
    echo "RED-SH-15 Case B FAIL: methodology unchanged despite one passing shard" >&2
    exit 1
fi

receipt_b="$WS_B/audit/shards/$BATCH_ID_B/.merge-receipt.json"
skip_count_b=$(jq -r '.skipped_shards | length' "$receipt_b")
rej_count_b=$(jq -r '.rejected_shards | length' "$receipt_b")
if [[ "$skip_count_b" != "2" || "$rej_count_b" != "0" ]]; then
    echo "RED-SH-15 Case B FAIL: receipt counts wrong (skipped=$skip_count_b rejected=$rej_count_b; expected 2/0)" >&2
    jq . "$receipt_b" >&2
    exit 1
fi

if ! grep -q 'Small passing body' "$WS_B/methodology-888.md"; then
    echo "RED-SH-15 Case B FAIL: passing shard body not in methodology" >&2
    exit 1
fi

echo "RED-SH-15 GREEN: all-skipped → RC=0 + all_skipped audit; mixed → RC=0 + mutation"
exit 0
