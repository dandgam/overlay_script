#!/usr/bin/env bash
# RED-SH-13: static check that prohibited negated-`if` pattern is absent and
# direct rc-capture (`NEXT=$(…); rc=$?`) appears at least once.
# GREEN when merger exists and both static invariants hold.
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"

if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-13: merger '$MERGER' not implemented yet" >&2
    exit 1
fi

# #1: prohibited negated-`if` pattern absent.
if grep -nE 'if !.*next_anchor_letter' "$MERGER"; then
    echo "RED-SH-13 FAIL: prohibited negated-if pattern (v1.4 BLOCK-1 regression class)" >&2
    exit 1
fi

# #2: direct rc-capture pattern present (NEXT=$(next_anchor_letter ...) + rc=$?).
if ! grep -qE 'NEXT=\$\(next_anchor_letter[^)]*\)' "$MERGER"; then
    echo "RED-SH-13 FAIL: direct rc-capture NEXT=\$(next_anchor_letter ...) not found" >&2
    exit 1
fi

if ! grep -qE 'rc=\$\?' "$MERGER"; then
    echo "RED-SH-13 FAIL: explicit rc=\$? capture not found" >&2
    exit 1
fi

echo "RED-SH-13 GREEN: rc-capture pattern present, negated-if absent"
exit 0
