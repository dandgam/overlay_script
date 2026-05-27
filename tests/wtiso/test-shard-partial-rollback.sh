#!/usr/bin/env bash
# RED-SH-3: partial rollback — 4 shards, shard-3 violates L3 (frontmatter
# delimiter outside fence). Acceptance: whole-batch reject — shards 1,2,4
# also unmerged, RC=14, methodology byte-identical, audit/shards/<batch-id>/
# retained for forensics.
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"
if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-3: merger '$MERGER' not implemented yet" >&2
    exit 1
fi

# shellcheck disable=SC1091
source tests/wtiso/_lib/fixture-helpers.sh

WS="${TMPDIR:-/tmp}/wtiso-red-sh-3-$$"
trap 'rm -rf "$WS"' EXIT INT TERM

BATCH_ID="2026-05-28T11:10:00Z-deadbee3"
wtiso_init_workspace "$WS" > /dev/null
pre_sha=$(wtiso_sha "$WS")

# Shards sort by filename; 03 carries the L3 violation.
wtiso_make_shard      "$WS" "$BATCH_ID" "Q-01" "Shard 1 body."
wtiso_make_shard      "$WS" "$BATCH_ID" "Q-02" "Shard 2 body."
wtiso_make_shard_raw  "$WS" "$BATCH_ID" "Q-03.md" "$(cat <<'EOF'
<!-- q-id: Q-03 -->
## 4PLACEHOLDER-deadbee3. shard 3 with forbidden frontmatter delim

---

body
EOF
)"
wtiso_make_shard      "$WS" "$BATCH_ID" "Q-04" "Shard 4 body."

rc=0
wtiso_invoke_merger "$WS" "$BATCH_ID" > /tmp/wtiso-red-sh-3.log 2>&1 || rc=$?

if [[ $rc -ne 14 ]]; then
    echo "RED-SH-3 FAIL: expected RC=14, got $rc" >&2
    cat /tmp/wtiso-red-sh-3.log >&2
    exit 1
fi

post_sha=$(wtiso_sha "$WS")
if [[ "$pre_sha" != "$post_sha" ]]; then
    echo "RED-SH-3 FAIL: methodology mutated despite whole-batch L3 reject" >&2
    exit 1
fi

# Forensics retention: shard dir + all 4 shard files still present.
shard_dir="$WS/audit/shards/$BATCH_ID"
if [[ ! -d "$shard_dir" ]]; then
    echo "RED-SH-3 FAIL: audit shard dir removed (forensics broken)" >&2
    exit 1
fi
for f in Q-01.md Q-02.md Q-03.md Q-04.md; do
    if [[ ! -f "$shard_dir/$f" ]]; then
        echo "RED-SH-3 FAIL: shard $f missing post-abort (forensics broken)" >&2
        exit 1
    fi
done

# Receipt MUST NOT be written for L3-aborted batches (RC=14 happens before
# §3.6 step 15). If it were, a retry on the same batch-id would falsely
# short-circuit at the idempotency gate.
if [[ -f "$shard_dir/.merge-receipt.json" ]]; then
    echo "RED-SH-3 FAIL: receipt written despite L3 abort" >&2
    exit 1
fi

if ! wtiso_assert_audit_event "$WS" "$BATCH_ID" "shard_l3_violation"; then
    echo "RED-SH-3 FAIL: audit event shard_l3_violation absent" >&2
    cat "$WS/audit/shards/merger-audit.jsonl" >&2 || true
    exit 1
fi

echo "RED-SH-3 GREEN: whole-batch L3 abort, RC=14, methodology intact, forensics retained"
exit 0
