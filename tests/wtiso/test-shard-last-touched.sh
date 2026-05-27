#!/usr/bin/env bash
# RED-SH-5: last-touched semantics — 4 shards with explicit mtimes
# T1 < T2 < T3 < T4 (all in past). Acceptance: post-merge methodology
# frontmatter `last-touched: <T4>` (no clamp triggered).
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"
if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-5: merger '$MERGER' not implemented yet" >&2
    exit 1
fi

# shellcheck disable=SC1091
source tests/wtiso/_lib/fixture-helpers.sh

WS="${TMPDIR:-/tmp}/wtiso-red-sh-5-$$"
trap 'rm -rf "$WS"' EXIT INT TERM

BATCH_ID="2026-05-28T11:30:00Z-deadbee5"
wtiso_init_workspace "$WS" > /dev/null

# Past timestamps relative to system clock; max(mtimes) = T4 must be at
# least 60s BEFORE `now` so the merger's clamp `min(max, now+60s)` is a noop.
now_epoch=$(date -u +%s)
T1=$(date -u -d "@$((now_epoch - 4000))" +%FT%TZ)
T2=$(date -u -d "@$((now_epoch - 3000))" +%FT%TZ)
T3=$(date -u -d "@$((now_epoch - 2000))" +%FT%TZ)
T4=$(date -u -d "@$((now_epoch - 1000))" +%FT%TZ)

wtiso_make_shard "$WS" "$BATCH_ID" "Q-01" "Shard 1 body."
wtiso_make_shard "$WS" "$BATCH_ID" "Q-02" "Shard 2 body."
wtiso_make_shard "$WS" "$BATCH_ID" "Q-03" "Shard 3 body."
wtiso_make_shard "$WS" "$BATCH_ID" "Q-04" "Shard 4 body."

sd="$WS/audit/shards/$BATCH_ID"
touch -d "$T1" "$sd/Q-01.md"
touch -d "$T2" "$sd/Q-02.md"
touch -d "$T3" "$sd/Q-03.md"
touch -d "$T4" "$sd/Q-04.md"

rc=0
wtiso_invoke_merger "$WS" "$BATCH_ID" > /tmp/wtiso-red-sh-5.log 2>&1 || rc=$?
if [[ $rc -ne 0 ]]; then
    echo "RED-SH-5 FAIL: merger exit $rc (expected 0)" >&2
    cat /tmp/wtiso-red-sh-5.log >&2
    exit 1
fi

fm_line=$(grep -E '^last-touched: ' "$WS/methodology-888.md" | head -1)
if [[ -z "$fm_line" ]]; then
    echo "RED-SH-5 FAIL: last-touched frontmatter line missing" >&2
    head -10 "$WS/methodology-888.md" >&2
    exit 1
fi

if [[ "$fm_line" != "last-touched: $T4" ]]; then
    echo "RED-SH-5 FAIL: expected 'last-touched: $T4', got '$fm_line'" >&2
    exit 1
fi

echo "RED-SH-5 GREEN: last-touched updated to max(mtimes) = $T4"
exit 0
