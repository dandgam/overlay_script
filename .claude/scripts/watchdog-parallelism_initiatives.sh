#!/bin/bash
# Smart watchdog companion to auto-loop-parallelism_initiatives.sh.
# Kills earlier than 1h `timeout` when post-commit zombie pattern detected.

LOG=".claude/watchdog-parallelism_initiatives.log"
INTEGRATION="integration/parallelism_initiatives"
MAX_PROCESS_AGE=3000     # 50 min smart kill threshold
MAX_COMMIT_STALE=2100    # 35 min commit staleness
HARD_CEILING_AGE=5400    # 90 min unconditional kill
INTERVAL=60

cd "$(dirname "spec/spec_parallelism_initiatives.md")/../.." || exit 1

while true; do
  PID=$(pgrep -f "claude -p /auto-loop-spec spec/spec_parallelism_initiatives.md" | head -1)
  if [ -z "$PID" ]; then
    sleep "$INTERVAL"; continue
  fi

  PROC_AGE=$(ps -o etimes= -p "$PID" 2>/dev/null | tr -d ' ')
  [ -z "$PROC_AGE" ] && { sleep "$INTERVAL"; continue; }

  LAST_COMMIT=$(git log "$INTEGRATION" -1 --format=%ct 2>/dev/null)
  NOW=$(date +%s)
  COMMIT_AGE=$((NOW - LAST_COMMIT))

  KILL_REASON=""
  if [ "$PROC_AGE" -gt "$HARD_CEILING_AGE" ]; then
    KILL_REASON="HARD CEILING (90min) reached — pre-commit hang or runaway"
  elif [ "$PROC_AGE" -gt "$MAX_PROCESS_AGE" ] && [ "$COMMIT_AGE" -gt "$MAX_COMMIT_STALE" ]; then
    KILL_REASON="post-commit zombie (proc_age>50min AND commit_age>35min)"
  fi

  if [ -n "$KILL_REASON" ]; then
    echo "=== $(date -u) KILL: PID=$PID proc_age=${PROC_AGE}s commit_age=${COMMIT_AGE}s — $KILL_REASON" | tee -a "$LOG"
    pkill -P "$PID" 2>/dev/null
    sleep 2
    kill "$PID" 2>/dev/null
    sleep 5
    if pgrep -f "claude -p /auto-loop-spec spec/spec_parallelism_initiatives.md" >/dev/null; then
      echo "=== $(date -u) escalating to KILL -9" | tee -a "$LOG"
      pkill -9 -f "claude -p /auto-loop-spec spec/spec_parallelism_initiatives.md"
    fi
    echo "=== $(date -u) cleaned, wrapper continues" | tee -a "$LOG"
  fi
  sleep "$INTERVAL"
done
