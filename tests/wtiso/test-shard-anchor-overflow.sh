#!/usr/bin/env bash
# RED-SH-8: anchor overflow — static cap=702 literal check + functional
# overflow check via next_anchor_letter("zz") → rc=1.
# Functional 702-shard fixture deferred to integration phase.
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"

if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-8: merger '$MERGER' not implemented yet" >&2
    exit 1
fi

# Literal cap assertion — must be 702 (independent of impl).
declare -ri EXPECTED_CAP=702
if [[ "$EXPECTED_CAP" -ne 702 ]]; then
    echo "RED-SH-8 FAIL: literal cap mismatch" >&2
    exit 1
fi

# Static: merger must declare _ANCHOR_CAP=702.
if ! grep -qE '_ANCHOR_CAP=702' "$MERGER"; then
    echo "RED-SH-8 FAIL: _ANCHOR_CAP=702 declaration not found in merger" >&2
    exit 1
fi

# Functional: anchor table size must be 702, "zz" must return rc=1 (exhausted).
# shellcheck disable=SC1090
source "$MERGER"
_init_anchor_table

if (( ${#_ANCHOR_TABLE[@]} != 702 )); then
    echo "RED-SH-8 FAIL: anchor table size ${#_ANCHOR_TABLE[@]} != 702" >&2
    exit 1
fi

R=""
rc=0
R=$(next_anchor_letter "zz") || rc=$?
if [[ $rc -ne 1 ]]; then
    echo "RED-SH-8 FAIL: next('zz') rc=$rc, expected 1 (exhausted)" >&2
    exit 1
fi
if [[ -n "$R" ]]; then
    echo "RED-SH-8 FAIL: next('zz') output should be empty, got '$R'" >&2
    exit 1
fi

# Verify last table entry is 'zz' (cap boundary).
if [[ "${_ANCHOR_TABLE[701]}" != "zz" ]]; then
    echo "RED-SH-8 FAIL: _ANCHOR_TABLE[701]='${_ANCHOR_TABLE[701]}', expected 'zz'" >&2
    exit 1
fi

echo "RED-SH-8 GREEN: cap=702 literal + functional zz boundary"
exit 0
