#!/usr/bin/env bash
# RED-SH-12: next_anchor_letter functional matrix (13 vectors) + antipattern.
# GREEN when merger exists, table init, all 13 vectors map correctly, and
# legacy xargs/'%b' antipattern is absent.
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"

if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-12: merger '$MERGER' not implemented yet" >&2
    exit 1
fi

# Antipattern static check.
if grep -qE "xargs[[:space:]]+printf[[:space:]]+'?%b'?" "$MERGER" 2>/dev/null; then
    echo "RED-SH-12 FAIL: legacy xargs/'%b' chain detected" >&2
    exit 1
fi

# Functional 13-vector matrix.
# shellcheck disable=SC1090
source "$MERGER"
_init_anchor_table

if (( ${#_ANCHOR_TABLE[@]} != 702 )); then
    echo "RED-SH-12 FAIL: table size ${#_ANCHOR_TABLE[@]} != 702" >&2
    exit 1
fi

_check() {
    local input="$1" expected_out="$2" expected_rc="$3"
    local got_out="" got_rc=0
    got_out=$(next_anchor_letter "$input") || got_rc=$?
    if [[ "$got_out" != "$expected_out" || $got_rc -ne $expected_rc ]]; then
        echo "RED-SH-12 FAIL: input='$input' → got='$got_out' rc=$got_rc; expected out='$expected_out' rc=$expected_rc" >&2
        return 1
    fi
    return 0
}

# Happy path (9 vectors).
_check ""   "a"  0
_check "a"  "b"  0
_check "y"  "z"  0
_check "z"  "aa" 0
_check "aa" "ab" 0
_check "ab" "ac" 0
_check "az" "ba" 0
_check "bz" "ca" 0
_check "zy" "zz" 0

# Boundary + error (4 vectors).
_check "zz"  "" 1
_check "aaa" "" 2
_check "aZ"  "" 2
_check "1a"  "" 2

echo "RED-SH-12 GREEN: 13/13 vectors + antipattern absent"
exit 0
