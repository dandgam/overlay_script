#!/usr/bin/env bash
# RED-SH-13: rc-capture pattern — static check that prohibited negated-`if`
# pattern is absent. Acceptance: zero matches for `if !.*next_anchor_letter`,
# direct rc-capture `NEXT=$(…); rc=$?` appears at least once.
# RED until scripts/888-shard-merger.sh is implemented.
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"

if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-13: merger '$MERGER' not implemented yet (expected pre-impl)" >&2
    exit 1
fi

# Static check #1: prohibited negated-`if` pattern absent.
if grep -nE 'if !.*next_anchor_letter' "$MERGER"; then
    echo "RED-SH-13: prohibited negated-if pattern detected (v1.4 BLOCK-1 regression class)" >&2
    exit 1
fi

# Static check #2: direct rc-capture pattern present at least once.
if ! grep -qE 'NEXT=\$\(next_anchor_letter[^)]*\)' "$MERGER"; then
    echo "RED-SH-13: direct rc-capture pattern (NEXT=\$(next_anchor_letter ...)) not found" >&2
    exit 1
fi

if ! grep -qE 'rc=\$\?' "$MERGER"; then
    echo "RED-SH-13: explicit rc capture (rc=\$?) not found" >&2
    exit 1
fi

# All static checks would pass; but merger doesn't exist yet → already RED above.
echo "RED-SH-13: would PASS if merger existed; awaiting Stage 2 impl" >&2
exit 1
