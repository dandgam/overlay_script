#!/usr/bin/env bash
# RED-SH-5: last-touched semantics — four workers with mtimes T1<T2<T3<T4.
# Acceptance: post-merge frontmatter `last-touched == T4` (clamped via
# min(max(mtimes), now+60s)).
# RED until scripts/888-shard-merger.sh is implemented.
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"

if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-5: merger '$MERGER' not implemented yet (expected pre-impl)" >&2
    exit 1
fi

# TODO(impl Stage 2): build 4-shard batch with explicit `touch -d` mtimes
# T1<T2<T3<T4. Invoke merger, assert post-merge methodology frontmatter
# `last-touched:` == T4 (or clamped to now+60s if future-dated).
echo "RED-SH-5: scenario assertion not implemented" >&2
exit 1
