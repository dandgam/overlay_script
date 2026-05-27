#!/usr/bin/env bash
# RED-SH-15 (v2.1 B13): all-skipped legitimate — batch of 3 shards all
# L1-rejected. Acceptance: appended_count==0, RC=0 (not RC=9), audit event
# `shard_merger_all_skipped`, receipt populated with skipped_shards and
# empty rejected_shards, methodology byte-identical to pre-batch.
# Companion: 1 pass + 2 reject must still mutate methodology + RC=0.
# RED until scripts/888-shard-merger.sh is implemented.
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"

if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-15: merger '$MERGER' not implemented yet (expected pre-impl)" >&2
    exit 1
fi

# TODO(impl Stage 2):
#   Case A — 3 shards, mock L1-reviewer returns BLOCK on every shard:
#     pre_sha=$(sha256sum methodology); $MERGER --batch-id X
#     assert exit RC=0 (not 9!)
#     assert post_sha == pre_sha
#     assert audit/auto-loop or merger audit log contains `shard_merger_all_skipped`
#     assert receipt.skipped_shards has 3 paths, receipt.rejected_shards == []
#   Case B — 3 shards, mock L1-reviewer returns BLOCK on 2 and PASS on 1:
#     assert exit RC=0
#     assert post_sha != pre_sha (mutation happened)
#     assert receipt.skipped_shards has 2, exit_code=0
echo "RED-SH-15: scenario assertion not implemented" >&2
exit 1
