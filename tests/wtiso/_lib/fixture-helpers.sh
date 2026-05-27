#!/usr/bin/env bash
# tests/wtiso/_lib/fixture-helpers.sh — shared test fixture builders.
# Sourced by integration tests (RED-SH-* runtime scenarios).

set -euo pipefail

# wtiso_init_workspace <dir> — create clean workspace with methodology stub.
# Echoes the absolute workspace path.
wtiso_init_workspace() {
    local ws="$1"
    rm -rf "$ws"
    mkdir -p "$ws/audit/shards"
    cat > "$ws/methodology-888.md" <<'EOF'
---
title: test methodology
last-touched: 2026-05-28T00:00:00Z
---

## 4a. Existing section

Body.
EOF
    printf '%s' "$ws"
}

# wtiso_make_shard <workspace> <batch-id> <q-id> <body-content>
# Creates a shard file at <workspace>/audit/shards/<batch-id>/<q-id>.md with
# a §4PLACEHOLDER-<uuid8> anchor (uuid8 = last 8 chars of batch-id).
wtiso_make_shard() {
    local ws="$1" bid="$2" qid="$3" body="$4"
    local sd="$ws/audit/shards/$bid"
    local uuid8="${bid##*-}"
    mkdir -p "$sd"
    cat > "$sd/${qid}.md" <<EOF
<!-- q-id: $qid -->
## 4PLACEHOLDER-${uuid8}. $qid synthetic test section

$body
EOF
}

# wtiso_invoke_merger <workspace> <batch-id> [extra env...]
# Runs merger inside workspace with mock L1=PASS by default.
# Returns merger exit code; preserves stdout/stderr.
wtiso_invoke_merger() {
    local ws="$1" bid="$2"; shift 2
    # Resolve merger to absolute path BEFORE cd (test's MERGER may be relative
    # to repo root, which becomes invalid once we cd into the workspace).
    local merger="${MERGER:-scripts/888-shard-merger.sh}"
    if [[ "$merger" != /* ]]; then
        merger="$(cd "$(dirname "$merger")" && pwd)/$(basename "$merger")"
    fi
    (
        cd "$ws"
        BATCH_MOCK_MODE=1 BMAD_SHARD_L1_MOCK="${BMAD_SHARD_L1_MOCK:-PASS}" \
            bash "$merger" --batch-id "$bid" --target methodology-888.md
    )
}

# wtiso_assert_audit_event <workspace> <batch-id> <event-name>
# Returns 0 if event present in merger audit jsonl, 1 otherwise.
wtiso_assert_audit_event() {
    local ws="$1" bid="$2" event="$3"
    local audit_file="$ws/audit/shards/merger-audit.jsonl"
    if [[ ! -f "$audit_file" ]]; then
        echo "wtiso_assert_audit_event: audit file missing: $audit_file" >&2
        return 1
    fi
    if grep -q "\"event\":\"$event\".*\"batch_id\":\"$bid\"" "$audit_file"; then
        return 0
    fi
    echo "wtiso_assert_audit_event: event '$event' not found for batch '$bid'" >&2
    return 1
}

# wtiso_sha <workspace>
# Echoes sha256 of methodology-888.md in workspace.
wtiso_sha() {
    sha256sum < "$1/methodology-888.md" | awk '{print $1}'
}
