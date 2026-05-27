#!/usr/bin/env bash
# RED-SH-2: idempotent re-run — invoke merger twice on same batch-id.
# Acceptance: second invocation byte-identical (no second mutation),
# audit event `shard_merger_skipped_idempotent`.
# RED until scripts/888-shard-merger.sh is implemented.
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"

if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-2: merger '$MERGER' not implemented yet (expected pre-impl)" >&2
    exit 1
fi

# TODO(impl Stage 2): first invoke → assert receipt written + methodology
# mutated. Second invoke on same batch-id → assert methodology sha256
# unchanged + audit jsonl contains `shard_merger_skipped_idempotent`.
echo "RED-SH-2: scenario assertion not implemented" >&2
exit 1
