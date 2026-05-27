#!/usr/bin/env bash
# RED-SH-3: partial rollback — synthetic shard-3 fails L3 (append-only).
# Acceptance: shards 1, 2, 4 also not merged (whole-batch reject),
# audit/shards/<batch-id>/ retained for forensics, methodology byte-identical.
# RED until scripts/888-shard-merger.sh is implemented.
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"

if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-3: merger '$MERGER' not implemented yet (expected pre-impl)" >&2
    exit 1
fi

# TODO(impl Stage 2): build 4-shard batch where shard-3 mutates prior
# methodology content. Invoke merger, assert RC=14, assert methodology
# sha256 identical to pre-batch, assert audit/shards/<batch-id>/ still exists.
echo "RED-SH-3: scenario assertion not implemented" >&2
exit 1
