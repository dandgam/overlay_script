#!/usr/bin/env bash
# RED-SH-6: q-id conflict — two shards declare the same q-id (via the
# `<!-- q-id: ... -->` comment) but live at distinct shard filenames.
# Acceptance:
#   - both q-id comments preserved in merged output;
#   - they appear in sorted shard-path order (Q-DUP-A.md before Q-DUP-B.md);
#   - each shard body lands under its own §4XX anchor (no dedup — anchors
#     are per-shard); q-id-prefixed section titles disambiguate.
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"
if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-6: merger '$MERGER' not implemented yet" >&2
    exit 1
fi

# shellcheck disable=SC1091
source tests/wtiso/_lib/fixture-helpers.sh

WS="${TMPDIR:-/tmp}/wtiso-red-sh-6-$$"
trap 'rm -rf "$WS"' EXIT INT TERM

BATCH_ID="2026-05-28T11:40:00Z-deadbee6"
wtiso_init_workspace "$WS" > /dev/null

wtiso_make_shard_raw "$WS" "$BATCH_ID" "Q-DUP-A.md" "$(cat <<'EOF'
<!-- q-id: Q-DUP -->
## 4PLACEHOLDER-deadbee6. Q-DUP variant A

Body for q-id collision variant A.
EOF
)"
wtiso_make_shard_raw "$WS" "$BATCH_ID" "Q-DUP-B.md" "$(cat <<'EOF'
<!-- q-id: Q-DUP -->
## 4PLACEHOLDER-deadbee6. Q-DUP variant B

Body for q-id collision variant B.
EOF
)"

rc=0
wtiso_invoke_merger "$WS" "$BATCH_ID" > /tmp/wtiso-red-sh-6.log 2>&1 || rc=$?
if [[ $rc -ne 0 ]]; then
    echo "RED-SH-6 FAIL: merger exit $rc (expected 0)" >&2
    cat /tmp/wtiso-red-sh-6.log >&2
    exit 1
fi

methodology="$WS/methodology-888.md"

qid_count=$(grep -c '^<!-- q-id: Q-DUP -->$' "$methodology" || true)
if [[ "$qid_count" != "2" ]]; then
    echo "RED-SH-6 FAIL: expected exactly 2 q-id comments, got $qid_count" >&2
    exit 1
fi

a_line=$(grep -n 'Q-DUP variant A' "$methodology" | head -1 | cut -d: -f1)
b_line=$(grep -n 'Q-DUP variant B' "$methodology" | head -1 | cut -d: -f1)
if [[ -z "$a_line" || -z "$b_line" ]] || (( a_line >= b_line )); then
    echo "RED-SH-6 FAIL: sorted-path order broken (A line=$a_line, B line=$b_line)" >&2
    exit 1
fi

if ! grep -q '^## 4b\. Q-DUP variant A' "$methodology"; then
    echo "RED-SH-6 FAIL: variant A not under §4b" >&2
    grep -E '^## 4[a-z]+\.' "$methodology" >&2 || true
    exit 1
fi
if ! grep -q '^## 4c\. Q-DUP variant B' "$methodology"; then
    echo "RED-SH-6 FAIL: variant B not under §4c" >&2
    grep -E '^## 4[a-z]+\.' "$methodology" >&2 || true
    exit 1
fi

dup=$(grep -E '^## 4[a-z]+\. ' "$methodology" | sort | uniq -d | head -1)
if [[ -n "$dup" ]]; then
    echo "RED-SH-6 FAIL: duplicate sub-header detected: $dup" >&2
    exit 1
fi

echo "RED-SH-6 GREEN: q-id collision handled — distinct anchors, sorted order, no dup headers"
exit 0
