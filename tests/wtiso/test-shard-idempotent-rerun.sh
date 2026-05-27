#!/usr/bin/env bash
# RED-SH-2: idempotent re-run — invoke merger twice on same batch-id.
# Acceptance: second invocation byte-identical (no second mutation),
# audit event `shard_merger_skipped_idempotent`.
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"

if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-2: merger '$MERGER' not implemented yet" >&2
    exit 1
fi

# shellcheck disable=SC1091
source tests/wtiso/_lib/fixture-helpers.sh

WS="${TMPDIR:-/tmp}/wtiso-red-sh-2-$$"
trap 'rm -rf "$WS"' EXIT INT TERM

BATCH_ID="2026-05-28T10:00:00Z-deadbeef"

wtiso_init_workspace "$WS" > /dev/null
wtiso_make_shard "$WS" "$BATCH_ID" "Q-TEST-IDEMP" "Lorem ipsum body content."

# First invocation — should mutate methodology + write receipt.
if ! wtiso_invoke_merger "$WS" "$BATCH_ID" > /tmp/wtiso-red-sh-2-run1.log 2>&1; then
    echo "RED-SH-2 FAIL: first invocation exit non-zero" >&2
    cat /tmp/wtiso-red-sh-2-run1.log >&2
    exit 1
fi

sha_after_first=$(wtiso_sha "$WS")

receipt_path="$WS/audit/shards/$BATCH_ID/.merge-receipt.json"
if [[ ! -f "$receipt_path" ]]; then
    echo "RED-SH-2 FAIL: receipt not written after first invocation" >&2
    exit 1
fi

prior_hash=$(jq -r '.shards_sha256' "$receipt_path")

# Second invocation — should be idempotent skip, no methodology mutation,
# emit audit `shard_merger_skipped_idempotent`.
if ! wtiso_invoke_merger "$WS" "$BATCH_ID" > /tmp/wtiso-red-sh-2-run2.log 2>&1; then
    echo "RED-SH-2 FAIL: second invocation exit non-zero" >&2
    cat /tmp/wtiso-red-sh-2-run2.log >&2
    exit 1
fi

sha_after_second=$(wtiso_sha "$WS")

if [[ "$sha_after_first" != "$sha_after_second" ]]; then
    echo "RED-SH-2 FAIL: methodology mutated on second invocation (idempotency broken)" >&2
    echo "  after first:  $sha_after_first" >&2
    echo "  after second: $sha_after_second" >&2
    exit 1
fi

# Audit event presence check.
if ! wtiso_assert_audit_event "$WS" "$BATCH_ID" "shard_merger_skipped_idempotent"; then
    echo "RED-SH-2 FAIL: audit event 'shard_merger_skipped_idempotent' not recorded" >&2
    cat "$WS/audit/shards/merger-audit.jsonl" >&2 || true
    exit 1
fi

# Receipt unchanged.
post_hash=$(jq -r '.shards_sha256' "$receipt_path")
if [[ "$prior_hash" != "$post_hash" ]]; then
    echo "RED-SH-2 FAIL: receipt shards_sha256 changed on idempotent re-run" >&2
    exit 1
fi

echo "RED-SH-2 GREEN: idempotent re-run preserves methodology + emits skip event"
exit 0
