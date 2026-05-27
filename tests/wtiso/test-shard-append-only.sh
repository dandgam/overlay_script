#!/usr/bin/env bash
# RED-SH-4: append-only — single shard contains a `^---$` line outside
# fenced blocks (would corrupt methodology frontmatter on append).
# Acceptance: L3 rejects with reason `append_only_violation`, batch RC=14.
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"
if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-4: merger '$MERGER' not implemented yet" >&2
    exit 1
fi

# shellcheck disable=SC1091
source tests/wtiso/_lib/fixture-helpers.sh

WS="${TMPDIR:-/tmp}/wtiso-red-sh-4-$$"
trap 'rm -rf "$WS"' EXIT INT TERM

BATCH_ID="2026-05-28T11:00:00Z-deadbeef"
wtiso_init_workspace "$WS" > /dev/null

pre_sha=$(wtiso_sha "$WS")

wtiso_make_shard_raw "$WS" "$BATCH_ID" "Q-TEST-L3.md" "$(cat <<'EOF'
<!-- q-id: Q-TEST-L3 -->
## 4PLACEHOLDER-deadbeef. malicious shard

Trying to inject frontmatter:

---

This should never land in methodology.
EOF
)"

rc=0
wtiso_invoke_merger "$WS" "$BATCH_ID" > /tmp/wtiso-red-sh-4.log 2>&1 || rc=$?

if [[ $rc -ne 14 ]]; then
    echo "RED-SH-4 FAIL: expected RC=14, got $rc" >&2
    cat /tmp/wtiso-red-sh-4.log >&2
    exit 1
fi

post_sha=$(wtiso_sha "$WS")
if [[ "$pre_sha" != "$post_sha" ]]; then
    echo "RED-SH-4 FAIL: methodology mutated despite L3 reject" >&2
    exit 1
fi

if ! wtiso_assert_audit_event "$WS" "$BATCH_ID" "shard_l3_violation"; then
    echo "RED-SH-4 FAIL: audit event shard_l3_violation absent" >&2
    cat "$WS/audit/shards/merger-audit.jsonl" >&2 || true
    exit 1
fi

echo "RED-SH-4 GREEN: L3 rejects shard with frontmatter delim, RC=14, methodology intact"
exit 0
