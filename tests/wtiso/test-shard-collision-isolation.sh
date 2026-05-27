#!/usr/bin/env bash
# RED-SH-1: collision isolation — two shards share the same placeholder
# anchor `## 4PLACEHOLDER-<uuid>.`. Merger sorts by shard path and assigns
# distinct final letters (`b`, `c` after pre-existing `4a`).
# Acceptance: both anchors present, distinct, in sorted shard-path order,
# placeholder string fully eliminated.
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"
if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-1: merger '$MERGER' not implemented yet" >&2
    exit 1
fi

# shellcheck disable=SC1091
source tests/wtiso/_lib/fixture-helpers.sh

WS="${TMPDIR:-/tmp}/wtiso-red-sh-1-$$"
trap 'rm -rf "$WS"' EXIT INT TERM

BATCH_ID="2026-05-28T11:20:00Z-deadbee1"
wtiso_init_workspace "$WS" > /dev/null

# Two shards, same placeholder (derived from batch-id UUID), different q-id.
# Sorted shard-path order: Q-A.md before Q-B.md → Q-A gets letter b, Q-B → c.
wtiso_make_shard "$WS" "$BATCH_ID" "Q-A" "Body for shard A."
wtiso_make_shard "$WS" "$BATCH_ID" "Q-B" "Body for shard B."

rc=0
wtiso_invoke_merger "$WS" "$BATCH_ID" > /tmp/wtiso-red-sh-1.log 2>&1 || rc=$?
if [[ $rc -ne 0 ]]; then
    echo "RED-SH-1 FAIL: merger exit $rc (expected 0)" >&2
    cat /tmp/wtiso-red-sh-1.log >&2
    exit 1
fi

methodology="$WS/methodology-888.md"

# Both new anchors present.
if ! grep -q '^## 4b\. ' "$methodology"; then
    echo "RED-SH-1 FAIL: anchor `## 4b.` not present post-merge" >&2
    cat "$methodology" >&2
    exit 1
fi
if ! grep -q '^## 4c\. ' "$methodology"; then
    echo "RED-SH-1 FAIL: anchor `## 4c.` not present post-merge" >&2
    cat "$methodology" >&2
    exit 1
fi

# Placeholder fully eliminated.
if grep -q '4PLACEHOLDER-' "$methodology"; then
    echo "RED-SH-1 FAIL: placeholder anchor leaked into methodology" >&2
    grep -n '4PLACEHOLDER-' "$methodology" >&2 || true
    exit 1
fi

# Distinct anchors — count occurrences (heading lines) is exactly 1 each.
b_count=$(grep -c '^## 4b\. ' "$methodology" || true)
c_count=$(grep -c '^## 4c\. ' "$methodology" || true)
if [[ "$b_count" != "1" || "$c_count" != "1" ]]; then
    echo "RED-SH-1 FAIL: anchor duplication b_count=$b_count c_count=$c_count" >&2
    exit 1
fi

# Order: 4b must come before 4c in the merged file (sorted shard-path order).
b_line=$(grep -n '^## 4b\. ' "$methodology" | head -1 | cut -d: -f1)
c_line=$(grep -n '^## 4c\. ' "$methodology" | head -1 | cut -d: -f1)
if (( b_line >= c_line )); then
    echo "RED-SH-1 FAIL: ordering broken (4b line=$b_line, 4c line=$c_line)" >&2
    exit 1
fi

# Cross-check shard-path order: Q-A came first → bears the 4b anchor body.
if ! awk "NR>=$b_line && NR<$c_line" "$methodology" | grep -q 'Body for shard A\.'; then
    echo "RED-SH-1 FAIL: shard A body not under 4b anchor" >&2
    exit 1
fi
if ! awk "NR>=$c_line" "$methodology" | grep -q 'Body for shard B\.'; then
    echo "RED-SH-1 FAIL: shard B body not under 4c anchor" >&2
    exit 1
fi

echo "RED-SH-1 GREEN: 2 shards, distinct anchors (4b/4c), sorted-path order preserved"
exit 0
