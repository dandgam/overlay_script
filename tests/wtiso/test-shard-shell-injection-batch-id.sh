#!/usr/bin/env bash
# RED-SH-10: shell injection — invoke merger with batch-id containing
# command-substitution metachars. Acceptance: P2 regex rejects, RC=10,
# no filesystem mutation.
# RED until scripts/888-shard-merger.sh is implemented.
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"

if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-10: merger '$MERGER' not implemented yet (expected pre-impl)" >&2
    exit 1
fi

# TODO(impl Stage 2): invoke merger with --batch-id='"; rm -rf /tmp"' and
# variants. Assert exit RC=10, assert no files created under audit/shards/,
# assert no methodology mutation. Use canary file in /tmp to detect any
# leaked command execution.
echo "RED-SH-10: scenario assertion not implemented" >&2
exit 1
