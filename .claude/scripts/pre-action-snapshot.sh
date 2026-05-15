#!/usr/bin/env bash
# pre-action-snapshot.sh — capture state before destructive action
#
# Called by /auto-loop-spec infra-with-recovery workflow BEFORE any
# destructive systemctl / DB / .env / file operation.
#
# Usage:
#   pre-action-snapshot.sh <session_id> <action_description>
#
# Example:
#   pre-action-snapshot.sh S2 "systemctl stop crm.service + .env flip"
#
# Captures (best-effort — continues on partial failure):
#   1. pg_dump of DATABASE_URL → .claude/db-snapshots/<session>-<ts>.sql
#   2. .env copy              → .claude/state/<session>-env-<ts>.backup
#   3. systemd state record   → .claude/state/<session>-systemd-<ts>.txt
#   4. git HEAD record        → .claude/state/<session>-githead-<ts>.txt
#
# Exit 0 — at least one snapshot succeeded (agent may proceed).
# Exit 1 — ALL snapshots failed (agent must NOT proceed; no recovery possible).

set -uo pipefail

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <session_id> [<action_description>]"
    exit 1
fi

SESSION_ID="$1"
ACTION="${2:-destructive}"
TS=$(date +%Y%m%d-%H%M%S)
PROJECT_ROOT="${PROJECT_ROOT:-/home/server/crm}"
SNAPSHOT_DIR="$PROJECT_ROOT/.claude/db-snapshots"
STATE_DIR="$PROJECT_ROOT/.claude/state"
SUCCESS_COUNT=0

cd "$PROJECT_ROOT"
mkdir -p "$SNAPSHOT_DIR" "$STATE_DIR"

echo "=== pre-action-snapshot ${SESSION_ID} @ ${TS} ==="
echo "Action: ${ACTION}"

# --- 1. DB snapshot ---
if [[ -f .env ]]; then
    # shellcheck disable=SC1091
    set +u
    source <(grep -E '^DATABASE_URL=' .env | sed 's/^/export /')
    set -u
fi

if [[ -n "${DATABASE_URL:-}" ]]; then
    SQL_OUT="$SNAPSHOT_DIR/${SESSION_ID}-${TS}.sql"
    if pg_dump "$DATABASE_URL" > "$SQL_OUT" 2>/dev/null; then
        SIZE=$(stat -c%s "$SQL_OUT")
        echo "✓ DB snapshot: $SQL_OUT ($SIZE bytes)"
        SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
    else
        echo "✗ DB snapshot FAILED (pg_dump error). Check DATABASE_URL."
        rm -f "$SQL_OUT"
    fi
else
    echo "- DB snapshot SKIPPED (no DATABASE_URL in .env)"
fi

# --- 2. .env backup ---
if [[ -f .env ]]; then
    ENV_OUT="$STATE_DIR/${SESSION_ID}-env-${TS}.backup"
    if cp .env "$ENV_OUT"; then
        echo "✓ .env backup: $ENV_OUT"
        SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
    else
        echo "✗ .env backup FAILED"
    fi
else
    echo "- .env not present, skipping"
fi

# --- 3. systemd state ---
SYS_OUT="$STATE_DIR/${SESSION_ID}-systemd-${TS}.txt"
{
    echo "# systemd state @ ${TS}"
    for svc in crm.service crm-watchdog.service crm-rust.service crm-grpc.service \
               crm-tg-gateway.service crm-wa-gateway.service \
               crm-max-gateway.service crm-ig-gateway.service; do
        enabled=$(systemctl is-enabled "$svc" 2>&1 || true)
        active=$(systemctl is-active "$svc" 2>&1 || true)
        echo "$svc: enabled=$enabled active=$active"
    done
} > "$SYS_OUT" 2>&1
echo "✓ systemd state: $SYS_OUT"
SUCCESS_COUNT=$((SUCCESS_COUNT + 1))

# --- 4. git HEAD ---
HEAD_OUT="$STATE_DIR/${SESSION_ID}-githead-${TS}.txt"
{
    echo "branch: $(git branch --show-current)"
    echo "HEAD:   $(git rev-parse HEAD)"
    echo "main:   $(git rev-parse main 2>/dev/null || echo 'unknown')"
    echo "status:"
    git status --short
} > "$HEAD_OUT"
echo "✓ git HEAD record: $HEAD_OUT"
SUCCESS_COUNT=$((SUCCESS_COUNT + 1))

# --- summary ---
echo "=== snapshot complete: $SUCCESS_COUNT/4 components saved ==="

if [[ $SUCCESS_COUNT -eq 0 ]]; then
    echo "FATAL: zero snapshots. Do NOT proceed with destructive action."
    exit 1
fi

# Symlink latest for convenience
ln -sfn "$(basename "$SYS_OUT")"  "$STATE_DIR/latest-systemd.txt"
ln -sfn "$(basename "$HEAD_OUT")" "$STATE_DIR/latest-githead.txt"
[[ -f "$STATE_DIR/${SESSION_ID}-env-${TS}.backup" ]] && \
    ln -sfn "$(basename "$STATE_DIR/${SESSION_ID}-env-${TS}.backup")" "$STATE_DIR/latest-env.backup"
[[ -f "$SNAPSHOT_DIR/${SESSION_ID}-${TS}.sql" ]] && \
    ln -sfn "$(basename "$SNAPSHOT_DIR/${SESSION_ID}-${TS}.sql")" "$SNAPSHOT_DIR/latest.sql"

exit 0
