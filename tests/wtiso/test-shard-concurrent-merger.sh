#!/usr/bin/env bash
# RED-SH-11: concurrent merger — external holder takes flock on the
# batch's `.merger.lock`, then merger B is invoked with a short
# `MERGER_FLOCK_TIMEOUT_OVERRIDE` and must exit RC=2 (flock contention).
# After the external holder releases, a fresh invocation succeeds and the
# methodology is mutated exactly once (single round of appends).
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"
if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-11: merger '$MERGER' not implemented yet" >&2
    exit 1
fi

# shellcheck disable=SC1091
source tests/wtiso/_lib/fixture-helpers.sh

WS="${TMPDIR:-/tmp}/wtiso-red-sh-11-$$"
HOLDER_PID=""
cleanup() {
    if [[ -n "${HOLDER_PID:-}" ]]; then
        kill "$HOLDER_PID" 2>/dev/null || true
        wait "$HOLDER_PID" 2>/dev/null || true
    fi
    rm -rf "$WS"
}
trap cleanup EXIT INT TERM

BATCH_ID="2026-05-28T13:00:00Z-deadbe11"
wtiso_init_workspace "$WS" > /dev/null
pre_sha=$(wtiso_sha "$WS")

wtiso_make_shard "$WS" "$BATCH_ID" "Q-CONC" "Concurrency test body."

LOCK_DIR="$WS/audit/shards/$BATCH_ID"
mkdir -p "$LOCK_DIR"
LOCK_FILE="$LOCK_DIR/.merger.lock"

# External holder acquires the lock and sleeps. flock-via-bash subshell
# means closing the FD on exit releases the lock automatically.
(
    exec {hfd}>"$LOCK_FILE"
    flock -x "$hfd"
    # Signal readiness via the lock file's mtime (any subsequent stat shows it).
    touch "$LOCK_DIR/.holder-ready"
    sleep 8
) &
HOLDER_PID=$!

# Wait for the holder to acquire the lock (max ~3s).
for _ in $(seq 1 30); do
    if [[ -f "$LOCK_DIR/.holder-ready" ]]; then break; fi
    sleep 0.1
done
if [[ ! -f "$LOCK_DIR/.holder-ready" ]]; then
    echo "RED-SH-11 FAIL: external lock holder failed to acquire flock" >&2
    exit 1
fi

# Now run merger B with a short flock timeout — must exit RC=2.
start_ts=$(date +%s)
rc=0
MERGER_FLOCK_TIMEOUT_OVERRIDE=2 wtiso_invoke_merger "$WS" "$BATCH_ID" \
    > /tmp/wtiso-red-sh-11-B.log 2>&1 || rc=$?
end_ts=$(date +%s)
elapsed=$((end_ts - start_ts))

if [[ $rc -ne 2 ]]; then
    echo "RED-SH-11 FAIL: merger B expected RC=2 (flock contention), got $rc" >&2
    cat /tmp/wtiso-red-sh-11-B.log >&2
    exit 1
fi
if (( elapsed > 6 )); then
    echo "RED-SH-11 FAIL: merger B took ${elapsed}s, expected ~2s (timeout override broken)" >&2
    exit 1
fi

# Methodology untouched while contention was in effect.
mid_sha=$(wtiso_sha "$WS")
if [[ "$pre_sha" != "$mid_sha" ]]; then
    echo "RED-SH-11 FAIL: methodology mutated despite both invocations failing/blocked" >&2
    exit 1
fi

# Wait for holder to release, then run a fresh merger — must succeed,
# methodology mutated EXACTLY once.
wait "$HOLDER_PID" 2>/dev/null || true
HOLDER_PID=""

rc=0
wtiso_invoke_merger "$WS" "$BATCH_ID" > /tmp/wtiso-red-sh-11-C.log 2>&1 || rc=$?
if [[ $rc -ne 0 ]]; then
    echo "RED-SH-11 FAIL: post-release merger C rc=$rc (expected 0)" >&2
    cat /tmp/wtiso-red-sh-11-C.log >&2
    exit 1
fi

post_sha=$(wtiso_sha "$WS")
if [[ "$mid_sha" == "$post_sha" ]]; then
    echo "RED-SH-11 FAIL: post-release merger did not mutate methodology" >&2
    exit 1
fi

body_occurrences=$(grep -c 'Concurrency test body' "$WS/methodology-888.md" || true)
if [[ "$body_occurrences" != "1" ]]; then
    echo "RED-SH-11 FAIL: shard body appears $body_occurrences times (expected 1)" >&2
    exit 1
fi

echo "RED-SH-11 GREEN: contended invocation → RC=2 in ${elapsed}s; fresh invocation mutated exactly once"
exit 0
