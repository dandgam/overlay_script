#!/usr/bin/env bash
# RED-SH-7: symlink transparency — methodology-888.md is a symlink.
# Acceptance: post-merge symlink still points to same resolved target;
# resolved file content has appended shards; symlink itself not replaced.
# RED until scripts/888-shard-merger.sh is implemented.
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"

if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-7: merger '$MERGER' not implemented yet (expected pre-impl)" >&2
    exit 1
fi

# TODO(impl Stage 2): create temp methodology + symlink pointing to it.
# Invoke merger via symlink path. Assert symlink target unchanged (readlink),
# resolved file has appended content, symlink itself still a symlink.
echo "RED-SH-7: scenario assertion not implemented" >&2
exit 1
