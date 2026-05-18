#!/bin/bash
LOG=".claude/watchdog-canonical_patches_port.log"
INTEGRATION="integration/canonical_patches_port"
MAX_PROCESS_AGE=3000
MAX_COMMIT_STALE=2100
HARD_CEILING_AGE=5400
INTERVAL=60
cd "$(dirname "$0")/../.." || exit 1
while true; do
  PID=$(pgrep -f "claude -p /auto-loop-spec spec/spec_canonical_patches_port.md" | head -1)
  if [ -z "$PID" ]; then sleep "$INTERVAL"; continue; fi
  PROC_AGE=$(ps -o etimes= -p "$PID" 2>/dev/null | tr -d ' ')
  [ -z "$PROC_AGE" ] && { sleep "$INTERVAL"; continue; }
  LAST_COMMIT=$(git log "$INTEGRATION" -1 --format=%ct 2>/dev/null)
  NOW=$(date +%s)
  COMMIT_AGE=$((NOW - LAST_COMMIT))
  KILL_REASON=""
  if [ "$PROC_AGE" -gt "$HARD_CEILING_AGE" ]; then KILL_REASON="HARD CEILING 90min"
  elif [ "$PROC_AGE" -gt "$MAX_PROCESS_AGE" ] && [ "$COMMIT_AGE" -gt "$MAX_COMMIT_STALE" ]; then KILL_REASON="post-commit zombie"; fi
  if [ -n "$KILL_REASON" ]; then
    echo "=== $(date -u) KILL: PID=$PID — $KILL_REASON" | tee -a "$LOG"
    pkill -P "$PID" 2>/dev/null; sleep 2; kill "$PID" 2>/dev/null; sleep 5
    if pgrep -f "claude -p /auto-loop-spec spec/spec_canonical_patches_port.md" >/dev/null; then
      pkill -9 -f "claude -p /auto-loop-spec spec/spec_canonical_patches_port.md"
    fi
  fi
  sleep "$INTERVAL"
done
