#!/usr/bin/env bash
# RED-SH-1: collision isolation — two workers write shards with placeholder
# anchors; merger renames them deterministically by sorted shard path.
# Acceptance: zero collisions in merged methodology.
# RED until scripts/888-shard-merger.sh is implemented.
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"

if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-1: merger '$MERGER' not implemented yet (expected pre-impl)" >&2
    exit 1
fi

# TODO(impl Stage 2): build 2-shard batch with §4PLACEHOLDER-<uuid> anchors,
# invoke merger, assert no anchor collision in target methodology, assert
# sorted-shard-path order assignment is deterministic.
echo "RED-SH-1: scenario assertion not implemented" >&2
exit 1
