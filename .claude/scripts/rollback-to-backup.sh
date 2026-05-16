#!/usr/bin/env bash
# rollback-to-backup.sh — emergency rollback to pre-initiative state
#
# Restores:
#   - git main to backup branch commit
#   - optionally DB from latest .claude/db-snapshots/latest.sql
#   - optionally .env from latest .claude/state/latest-env.backup
#   - optionally systemd state from latest systemd snapshot
#
# Usage:
#   rollback-to-backup.sh <backup_branch_name> [--auto]
#
# Example:
#   rollback-to-backup.sh backup/kill-uvicorn-pre-2026-04-21
#   rollback-to-backup.sh backup/kill-uvicorn-pre-2026-04-21 --auto   (no confirmation, for agent auto-recovery)

set -uo pipefail

BACKUP_BRANCH="${1:-}"
MODE="${2:-interactive}"
PROJECT_ROOT="${PROJECT_ROOT:-/home/server/crm}"
SNAPSHOT_DIR="$PROJECT_ROOT/.claude/db-snapshots"
STATE_DIR="$PROJECT_ROOT/.claude/state"

if [[ -z "$BACKUP_BRANCH" ]]; then
    echo "Usage: $0 <backup_branch_name> [--auto]"
    echo "Available backup branches:"
    git -C "$PROJECT_ROOT" branch --list "backup/*"
    exit 1
fi

cd "$PROJECT_ROOT"

if ! git show-ref --verify --quiet "refs/heads/$BACKUP_BRANCH"; then
    echo "ERROR: branch '$BACKUP_BRANCH' not found"
    echo "Available:"
    git branch --list "backup/*"
    exit 1
fi

BACKUP_SHA=$(git rev-parse "$BACKUP_BRANCH")
MAIN_SHA=$(git rev-parse main)
CUR_BRANCH=$(git branch --show-current)

echo "=== rollback-to-backup ==="
echo "Backup branch: $BACKUP_BRANCH ($BACKUP_SHA)"
echo "Current main:  $MAIN_SHA"
echo "Current branch: $CUR_BRANCH"
echo ""
echo "This will:"
echo "  1. sudo systemctl stop crm-rust.service"
echo "  2. git checkout main && git reset --hard $BACKUP_BRANCH"
echo "  3. Offer: restore .env from $STATE_DIR/latest-env.backup"
echo "  4. Offer: restore DB from $SNAPSHOT_DIR/latest.sql"
echo "  5. sudo systemctl restart crm-rust.service"
echo ""

if [[ "$MODE" != "--auto" ]]; then
    read -r -p "Proceed? (yes/no): " CONFIRM
    if [[ "$CONFIRM" != "yes" ]]; then
        echo "Aborted."
        exit 0
    fi
fi

# --- 1. stop crm-rust ---
echo "[1/5] stopping crm-rust.service..."
sudo systemctl stop crm-rust.service 2>&1 || echo "  warning: could not stop (already stopped?)"

# --- 2. git reset ---
echo "[2/5] git checkout main && git reset --hard $BACKUP_BRANCH ..."
git checkout main
git reset --hard "$BACKUP_BRANCH"
echo "  main is now at $BACKUP_SHA"

# --- 3. .env restore (optional) ---
if [[ -f "$STATE_DIR/latest-env.backup" ]]; then
    echo "[3/5] .env backup available at $STATE_DIR/latest-env.backup"
    if [[ "$MODE" == "--auto" ]]; then
        cp "$STATE_DIR/latest-env.backup" .env
        echo "  restored (auto mode)"
    else
        read -r -p "  Restore .env? (yes/no): " R
        [[ "$R" == "yes" ]] && cp "$STATE_DIR/latest-env.backup" .env && echo "  restored"
    fi
else
    echo "[3/5] no .env backup, skipping"
fi

# --- 4. DB restore (optional) ---
if [[ -f "$SNAPSHOT_DIR/latest.sql" ]]; then
    SIZE=$(stat -c%s "$SNAPSHOT_DIR/latest.sql")
    echo "[4/5] DB snapshot available at $SNAPSHOT_DIR/latest.sql ($SIZE bytes)"
    echo "  WARNING: restoring overwrites current DB. Consider COUNT(*) queries first to verify damage scope."
    if [[ "$MODE" == "--auto" ]]; then
        echo "  (auto mode) — NOT restoring DB automatically; human must decide."
    else
        read -r -p "  Restore DB from snapshot? (yes/no): " R
        if [[ "$R" == "yes" ]]; then
            if [[ -f .env ]]; then
                # shellcheck disable=SC1091
                set +u
                source <(grep -E '^DATABASE_URL=' .env | sed 's/^/export /')
                set -u
            fi
            if [[ -n "${DATABASE_URL:-}" ]]; then
                psql "$DATABASE_URL" < "$SNAPSHOT_DIR/latest.sql" || echo "  WARN: psql restore had errors"
            else
                echo "  ERR: no DATABASE_URL; restore manually with psql \$DATABASE_URL < $SNAPSHOT_DIR/latest.sql"
            fi
        fi
    fi
else
    echo "[4/5] no DB snapshot, skipping"
fi

# --- 5. restart crm-rust ---
echo "[5/5] starting crm-rust.service..."
sudo systemctl start crm-rust.service 2>&1 || echo "  warning: could not start"
sleep 2
systemctl is-active crm-rust.service

echo ""
echo "=== rollback complete ==="
echo "main @ $(git rev-parse HEAD)"
echo ""
echo "Manual checks you should run next:"
echo "  - curl -sf http://127.0.0.1:3000/health"
echo "  - systemctl status crm.service crm-watchdog.service"
echo "  - psql \$DATABASE_URL -c 'SELECT count(*) FROM app_settings'"
echo "  - git log main --oneline -5"
