#!/usr/bin/env bash
# RED-SH-16 (v2.1 B14): content-aware idempotency hash — two shard sets A
# and B with identical sorted path lists but different file contents must
# yield different shards_sha256. In-place mutation between merger invocations
# must be detected (non-idempotent on second run).
# RED until scripts/888-shard-merger.sh is implemented.
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"

if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-16: merger '$MERGER' not implemented yet (expected pre-impl)" >&2
    exit 1
fi

# TODO(impl Stage 2):
#   Step 1: build shard set A at audit/shards/test-a/ with shard1.md = "x".
#           Capture hash_A = sha256(file-contents-included-formula).
#   Step 2: build shard set B at audit/shards/test-b/ with shard1.md = "y".
#           Same path layout. Capture hash_B.
#   Step 3: assert hash_A != hash_B  (path-only formula would falsely pass).
#   Step 4: invoke merger on test-a, capture receipt.shards_sha256.
#   Step 5: in-place mutate audit/shards/test-a/shard1.md → "z".
#           Invoke merger again on same batch-id. Capture new receipt hash.
#   Step 6: assert new hash != prior receipt hash (idempotency check
#           correctly detected the in-place mutation; if it falsely passed,
#           the merger would silently drop the mutation).
echo "RED-SH-16: scenario assertion not implemented" >&2
exit 1
