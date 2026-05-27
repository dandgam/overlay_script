#!/usr/bin/env bash
# RED-SH-14 (v2.1 B1): path canonicalisation — static check that no
# implementation source contains the legacy leading-dot directory form.
# Plus functional check that audit/shards/<batch-id>/ is the forensics
# retain path on L3 abort.
# RED until scripts/888-shard-merger.sh is implemented.
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"

if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-14: merger '$MERGER' not implemented yet (expected pre-impl)" >&2
    exit 1
fi

# Build regex source at runtime to avoid embedding the literal token in this
# file (the narrow-gate would otherwise flag this test as a B1 violation).
LEGACY_REGEX="$(printf '\\%s' '.')shards/"

# Static check across merger + all test fixtures.
if grep -nE "$LEGACY_REGEX" "$MERGER" tests/wtiso/*.sh 2>/dev/null; then
    echo "RED-SH-14: legacy leading-dot directory form detected (must be audit/shards/)" >&2
    exit 1
fi

# TODO(impl Stage 2): provoke L3 abort with synthetic shard, assert
# `audit/shards/$batch_id/` directory exists post-abort for forensics.
echo "RED-SH-14: scenario assertion not implemented (legacy-form static check passed)" >&2
exit 1
