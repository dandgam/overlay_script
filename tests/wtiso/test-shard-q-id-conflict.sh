#!/usr/bin/env bash
# RED-SH-6: q-id conflict — two shards declare the same q-id.
# Acceptance: both appended under one §4XXX anchor in sorted shard-path
# order; sub-headers q-id-prefixed; no duplicate sub-headers.
# RED until scripts/888-shard-merger.sh is implemented.
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"

if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-6: merger '$MERGER' not implemented yet (expected pre-impl)" >&2
    exit 1
fi

# TODO(impl Stage 2): build 2-shard batch with identical `q-id:` first-line
# comment. Invoke merger, assert merged methodology has exactly one §4XXX
# anchor with both bodies under it, sub-headers prefixed with q-id,
# zero duplicate sub-header strings.
echo "RED-SH-6: scenario assertion not implemented" >&2
exit 1
