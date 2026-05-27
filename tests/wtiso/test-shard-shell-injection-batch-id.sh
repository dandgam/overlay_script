#!/usr/bin/env bash
# RED-SH-10: shell-injection guard — invoke merger with --batch-id values
# containing shell metachars. Acceptance:
#   - P2 regex rejects every variant with RC=10;
#   - no canary file created anywhere (no command-substitution leaks);
#   - no audit/shards/<bid>/ dir created for the malformed batch-id;
#   - methodology byte-identical.
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"
if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-10: merger '$MERGER' not implemented yet" >&2
    exit 1
fi

# shellcheck disable=SC1091
source tests/wtiso/_lib/fixture-helpers.sh

CANARY="/tmp/wtiso-red-sh-10-canary-$$"
rm -f "$CANARY"

WS="${TMPDIR:-/tmp}/wtiso-red-sh-10-$$"
trap 'rm -rf "$WS"; rm -f "$CANARY"' EXIT INT TERM

wtiso_init_workspace "$WS" > /dev/null
pre_sha=$(wtiso_sha "$WS")

# Injection variants — each MUST be rejected by P2 regex with RC=10.
INJECTIONS=(
    "\"; touch $CANARY; #"
    "'; touch $CANARY; #"
    "\$(touch $CANARY)"
    "\`touch $CANARY\`"
    "../etc/passwd"
    "2026-05-28T12:00:00Z-NOTHEX99"           # bad hex
    "2026-05-28T12:00:00Z-deadbeef extra"     # trailing junk
    "2026-05-28T12:00:00Z-deadbeef && touch $CANARY"
)

fail=0
for bid in "${INJECTIONS[@]}"; do
    rc=0
    wtiso_invoke_merger_raw_bid "$WS" "$bid" > /tmp/wtiso-red-sh-10.log 2>&1 || rc=$?
    if [[ $rc -ne 10 ]]; then
        echo "RED-SH-10 FAIL: injection '$bid' → rc=$rc, expected 10" >&2
        cat /tmp/wtiso-red-sh-10.log >&2
        fail=1
    fi
    if [[ -e "$CANARY" ]]; then
        echo "RED-SH-10 FAIL: canary file created by injection '$bid'" >&2
        ls -la "$CANARY" >&2
        rm -f "$CANARY"
        fail=1
    fi
done

# Methodology untouched.
post_sha=$(wtiso_sha "$WS")
if [[ "$pre_sha" != "$post_sha" ]]; then
    echo "RED-SH-10 FAIL: methodology mutated by injection attempts" >&2
    fail=1
fi

# No bogus shard dirs created for malformed batch-ids. Only the parent
# `audit/shards/` should exist with at most a stale lock file from any
# variant that reached P1 (it shouldn't — P2 runs after parse but the
# spec orders mkdir BEFORE P2). We accept the parent dir but require no
# subdir matching any injection-string fragment.
if find "$WS/audit/shards" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | grep -qE 'touch|\\$|passwd|extra|NOTHEX'; then
    echo "RED-SH-10 FAIL: bogus shard subdir created from injection string" >&2
    find "$WS/audit/shards" -mindepth 1 -maxdepth 1 -type d >&2
    fail=1
fi

if (( fail )); then exit 1; fi

echo "RED-SH-10 GREEN: ${#INJECTIONS[@]} injection variants rejected (RC=10), no canary, methodology intact"
exit 0
