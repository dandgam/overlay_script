#!/usr/bin/env bash
# RED-SH-9: fenced block — shard contains a ```bash fence with a literal
# `## 4xx. example heading` line and a fake `---` inside the fence.
# Acceptance:
#   - state-machine L3 does NOT count in-fence `^---$` as a frontmatter
#     delim violation (so the batch must succeed, RC=0);
#   - placeholder rewriter does NOT touch the in-fence heading example;
#   - the in-fence content (including the fake heading and `---`) is
#     byte-identical pre- and post-merge.
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"
if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-9: merger '$MERGER' not implemented yet" >&2
    exit 1
fi

# shellcheck disable=SC1091
source tests/wtiso/_lib/fixture-helpers.sh

WS="${TMPDIR:-/tmp}/wtiso-red-sh-9-$$"
trap 'rm -rf "$WS"' EXIT INT TERM

BATCH_ID="2026-05-28T12:00:00Z-deadbee9"
wtiso_init_workspace "$WS" > /dev/null

# Build shard whose body contains a fenced bash block carrying a literal
# `## 4xx. example` heading AND a fake `---` line. Both must survive merge.
fenced_body=$(cat <<'EOF'
<!-- q-id: Q-FENCE -->
## 4PLACEHOLDER-deadbee9. fenced-block survival test

Example of an anchor heading and frontmatter inside a code fence:

```bash
# example markup
## 4xx. example heading (literal — must not be rewritten)
---
echo "literal dashes above must not be counted as frontmatter delim"
## 4PLACEHOLDER-deadbee9. fake placeholder inside fence
```

End of shard body.
EOF
)
wtiso_make_shard_raw "$WS" "$BATCH_ID" "Q-FENCE.md" "$fenced_body"

# Snapshot the fenced-region bytes (lines between the opening and closing
# ```bash fence) BEFORE merge for round-trip verification.
shard_file="$WS/audit/shards/$BATCH_ID/Q-FENCE.md"
pre_fence_bytes=$(awk '/^```bash/,/^```$/' "$shard_file")

rc=0
wtiso_invoke_merger "$WS" "$BATCH_ID" > /tmp/wtiso-red-sh-9.log 2>&1 || rc=$?
if [[ $rc -ne 0 ]]; then
    echo "RED-SH-9 FAIL: merger exit $rc (expected 0 — in-fence delim must not trigger L3)" >&2
    cat /tmp/wtiso-red-sh-9.log >&2
    exit 1
fi

methodology="$WS/methodology-888.md"

# In-fence content preserved byte-for-byte in merged methodology.
post_fence_bytes=$(awk '/^```bash/,/^```$/' "$methodology")
if [[ "$pre_fence_bytes" != "$post_fence_bytes" ]]; then
    echo "RED-SH-9 FAIL: in-fence content mutated" >&2
    diff <(echo "$pre_fence_bytes") <(echo "$post_fence_bytes") >&2 || true
    exit 1
fi

# In-fence example heading still says `## 4xx.` (NOT rewritten to merged letter).
if ! grep -qF '## 4xx. example heading (literal — must not be rewritten)' "$methodology"; then
    echo "RED-SH-9 FAIL: in-fence heading mutated by placeholder rewriter" >&2
    exit 1
fi

# In-fence fake placeholder still present (rewriter must skip inside fence).
if ! grep -qF '## 4PLACEHOLDER-deadbee9. fake placeholder inside fence' "$methodology"; then
    echo "RED-SH-9 FAIL: in-fence placeholder rewritten (state machine broken)" >&2
    exit 1
fi

# Out-of-fence anchor was rewritten to §4b.
if ! grep -qE '^## 4b\. fenced-block survival test' "$methodology"; then
    echo "RED-SH-9 FAIL: out-of-fence anchor not rewritten to §4b" >&2
    grep -E '^## 4[a-zA-Z]+' "$methodology" >&2 || true
    exit 1
fi

echo "RED-SH-9 GREEN: fenced block preserved byte-for-byte, in-fence heading + placeholder untouched"
exit 0
