#!/usr/bin/env bash
# RED-SH-12: next_anchor_letter functional matrix — 13 vectors covering
# happy / boundary / error paths. Acceptance: all 13 pass; output uses
# no `xargs printf '%b'` antipattern.
# RED until scripts/888-shard-merger.sh is implemented.
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"

if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-12: merger '$MERGER' not implemented yet (expected pre-impl)" >&2
    exit 1
fi

# Antipattern static check (independent of impl): if merger exists, it must
# NOT contain the legacy xargs-printf-b chain that swallows backslashes.
if grep -qE "xargs[[:space:]]+printf[[:space:]]+'?%b'?" "$MERGER" 2>/dev/null; then
    echo "RED-SH-12: legacy xargs/'%b' chain detected in merger (forbidden)" >&2
    exit 1
fi

# TODO(impl Stage 2): source merger, invoke next_anchor_letter with each of
# the 13 vectors:
#   ("" → "a"), ("a" → "b"), ("y" → "z"), ("z" → "aa"),
#   ("aa" → "ab"), ("ab" → "ac"), ("az" → "ba"), ("bz" → "ca"),
#   ("zy" → "zz"), ("zz" → rc=1), ("aaa" → rc=2), ("aZ" → rc=2), ("1a" → rc=2)
# Assert all 13 stdout/$? pairs match.
echo "RED-SH-12: scenario assertion not implemented (antipattern check passed)" >&2
exit 1
