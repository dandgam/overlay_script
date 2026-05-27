#!/usr/bin/env bash
# RED-SH-9: fenced block — shard contains fenced code block with literal
# `## 4XXX.` heading example inside. Acceptance: state-machine L3 does not
# count in-fence heading; in-fence example not rewritten by placeholder sed;
# round-trip preserves example byte-for-byte.
# RED until scripts/888-shard-merger.sh is implemented.
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"

if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-9: merger '$MERGER' not implemented yet (expected pre-impl)" >&2
    exit 1
fi

# TODO(impl Stage 2): build shard with embedded ```bash code-fence containing
# literal `## 4xx. example` line. Invoke merger, assert L3 count of headings
# outside fences matches expected, assert in-fence text is byte-identical
# pre- and post-merge.
echo "RED-SH-9: scenario assertion not implemented" >&2
exit 1
