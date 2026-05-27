#!/usr/bin/env bash
# RED-SH-4: append-only — synthetic shard mutates prior methodology content.
# Acceptance: L3 rejects with reason `append_only_violation`, batch RC=14.
# RED until scripts/888-shard-merger.sh is implemented.
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"

if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-4: merger '$MERGER' not implemented yet (expected pre-impl)" >&2
    exit 1
fi

# TODO(impl Stage 2): build single-shard batch where shard violates
# append-only (e.g., writes content at offset 0). Invoke merger, assert
# RC=14, assert audit event contains `append_only_violation`.
echo "RED-SH-4: scenario assertion not implemented" >&2
exit 1
