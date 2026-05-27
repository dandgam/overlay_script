#!/usr/bin/env bash
# RED-SH-7: symlink transparency — methodology-888.md is a symlink to a
# real file in a sibling directory. Acceptance: post-merge the symlink
# still points to the same resolved target; the resolved file has the
# appended shards; the symlink itself was not replaced.
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"
if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-7: merger '$MERGER' not implemented yet" >&2
    exit 1
fi

# shellcheck disable=SC1091
source tests/wtiso/_lib/fixture-helpers.sh

WS="${TMPDIR:-/tmp}/wtiso-red-sh-7-$$"
trap 'rm -rf "$WS"' EXIT INT TERM

BATCH_ID="2026-05-28T11:50:00Z-deadbee7"
wtiso_init_workspace "$WS" > /dev/null

# Replace plain methodology with a symlink to a real file in real-dir/.
mkdir -p "$WS/real-dir"
mv "$WS/methodology-888.md" "$WS/real-dir/methodology-real.md"
ln -s "real-dir/methodology-real.md" "$WS/methodology-888.md"

# Capture pre-merge state.
if [[ ! -L "$WS/methodology-888.md" ]]; then
    echo "RED-SH-7 FAIL: pre-condition broken (symlink not set up)" >&2
    exit 1
fi
pre_link_target=$(readlink "$WS/methodology-888.md")
pre_resolved=$(readlink -f "$WS/methodology-888.md")
pre_sha=$(sha256sum < "$pre_resolved" | awk '{print $1}')

wtiso_make_shard "$WS" "$BATCH_ID" "Q-SYM" "Symlink-traversal test body."

rc=0
wtiso_invoke_merger "$WS" "$BATCH_ID" > /tmp/wtiso-red-sh-7.log 2>&1 || rc=$?
if [[ $rc -ne 0 ]]; then
    echo "RED-SH-7 FAIL: merger exit $rc (expected 0)" >&2
    cat /tmp/wtiso-red-sh-7.log >&2
    exit 1
fi

# Symlink itself unchanged.
if [[ ! -L "$WS/methodology-888.md" ]]; then
    echo "RED-SH-7 FAIL: symlink replaced with regular file" >&2
    exit 1
fi
post_link_target=$(readlink "$WS/methodology-888.md")
if [[ "$pre_link_target" != "$post_link_target" ]]; then
    echo "RED-SH-7 FAIL: symlink target changed ($pre_link_target → $post_link_target)" >&2
    exit 1
fi

# Resolved file path unchanged.
post_resolved=$(readlink -f "$WS/methodology-888.md")
if [[ "$pre_resolved" != "$post_resolved" ]]; then
    echo "RED-SH-7 FAIL: resolved path changed ($pre_resolved → $post_resolved)" >&2
    exit 1
fi

# Resolved file content actually mutated AND contains shard body.
post_sha=$(sha256sum < "$pre_resolved" | awk '{print $1}')
if [[ "$pre_sha" == "$post_sha" ]]; then
    echo "RED-SH-7 FAIL: resolved file unchanged (merge did not happen)" >&2
    exit 1
fi
if ! grep -q 'Symlink-traversal test body' "$pre_resolved"; then
    echo "RED-SH-7 FAIL: shard body not in resolved file" >&2
    exit 1
fi

echo "RED-SH-7 GREEN: symlink preserved, resolved file received appended shard"
exit 0
