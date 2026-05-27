#!/usr/bin/env bash
# RED-SH-8: anchor overflow — batch with 702 shards lands on §4zz cleanly;
# batch with 703 shards exits RC=6 (anchor_pool_exhausted) without partial
# mutation. Cap value 702 asserted as literal (matches §6 ADR-012 + §3.3
# step 9 error message).
# RED until scripts/888-shard-merger.sh is implemented.
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"

if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-8: merger '$MERGER' not implemented yet (expected pre-impl)" >&2
    exit 1
fi

# Literal cap assertion (independent of merger impl):
declare -ri EXPECTED_CAP=702
if [[ "$EXPECTED_CAP" -ne 702 ]]; then
    echo "RED-SH-8: literal cap mismatch — must be 702 per ADR-012" >&2
    exit 1
fi

# TODO(impl Stage 2): build 702-shard batch → assert success at §4zz.
# Build 703-shard batch → assert RC=6, assert methodology unchanged.
echo "RED-SH-8: scenario assertion not implemented (literal cap=702 checked)" >&2
exit 1
