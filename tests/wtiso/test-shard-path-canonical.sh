#!/usr/bin/env bash
# RED-SH-14 (v2.1 B1): path canonicalisation. Static portion GREEN when
# legacy leading-dot directory form is absent from merger + test fixtures.
# Functional forensics-path portion: deferred to integration test phase
# (requires L3-abort fixture infra).
set -euo pipefail

MERGER="${MERGER:-scripts/888-shard-merger.sh}"

if [[ ! -x "$MERGER" ]]; then
    echo "RED-SH-14: merger '$MERGER' not implemented yet" >&2
    exit 1
fi

# Build regex source at runtime to avoid embedding the literal token in this
# file (would otherwise be a B1 self-violation via narrow-gate grep).
LEGACY_REGEX="$(printf '\\%s' '.')shards/"

# Static check across merger + all test fixtures.
if grep -nE "$LEGACY_REGEX" "$MERGER" tests/wtiso/*.sh 2>/dev/null; then
    echo "RED-SH-14 FAIL: legacy leading-dot directory form detected" >&2
    exit 1
fi

# All references must use the canonical audit/shards/ form.
if ! grep -qE 'audit/shards/' "$MERGER"; then
    echo "RED-SH-14 FAIL: canonical audit/shards/ form not used in merger" >&2
    exit 1
fi

echo "RED-SH-14 GREEN: legacy form absent, canonical form present in merger"
exit 0
