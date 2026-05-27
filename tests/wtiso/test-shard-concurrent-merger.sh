#!/usr/bin/env bash
# RED-SH-11: concurrent merger — two merger processes invoked simultaneously
# on same batch-id. Acceptance: one acquires flock, the other times out at
# RC=2; methodology mutated exactly once.
# RED until scripts/888-shard-merger.sh is implemented.
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"

if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-11: merger '$MERGER' not implemented yet (expected pre-impl)" >&2
    exit 1
fi

# TODO(impl Stage 2): spawn merger A in background with sleep-injected pause
# while holding flock. Spawn merger B simultaneously with `flock -w 5`.
# Assert B exits RC=2 (timeout). After A completes, assert methodology
# mutated exactly once (pre→post diff is exactly the expected appends).
echo "RED-SH-11: scenario assertion not implemented" >&2
exit 1
