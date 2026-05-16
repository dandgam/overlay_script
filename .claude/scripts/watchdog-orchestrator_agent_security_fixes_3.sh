#!/bin/bash
LOG=".claude/watchdog-orchestrator_agent_security_fixes_3.log"
INTEGRATION="integration/orchestrator_agent_security_fixes_3"
MAX_PROCESS_AGE=3000
MAX_COMMIT_STALE=2100
HARD_CEILING_AGE=5400
INTERVAL=60

cd /home/server/bmad-orchestrator || exit 1

while true; do
  PID=$(pgrep -f "claude -p /auto-loop-spec spec/spec_orchestrator_agent_security_fixes_3.md" | head -1)
  if [ -z "$PID" ]; then sleep "$INTERVAL"; continue; fi
  PROC_AGE=$(ps -o etimes= -p "$PID" 2>/dev/null | tr -d ' ')
  [ -z "$PROC_AGE" ] && { sleep "$INTERVAL"; continue; }
  LAST_COMMIT=$(git log "$INTEGRATION" -1 --format=%ct 2>/dev/null)
  NOW=$(date +%s)
  COMMIT_AGE=$((NOW - LAST_COMMIT))

  KILL_REASON=""
  if [ "$PROC_AGE" -gt "$HARD_CEILING_AGE" ]; then
    KILL_REASON="HARD CEILING (90min)"
  elif [ "$PROC_AGE" -gt "$MAX_PROCESS_AGE" ] && [ "$COMMIT_AGE" -gt "$MAX_COMMIT_STALE" ]; then
    KILL_REASON="post-commit zombie"
  fi

  if [ -n "$KILL_REASON" ]; then
    echo "=== $(date -u) KILL: PID=$PID proc_age=${PROC_AGE}s commit_age=${COMMIT_AGE}s — $KILL_REASON" | tee -a "$LOG"
    pkill -P "$PID" 2>/dev/null; sleep 2
    kill "$PID" 2>/dev/null; sleep 5
    pgrep -f "claude -p /auto-loop-spec spec/spec_orchestrator_agent_security_fixes_3.md" >/dev/null && \
      pkill -9 -f "claude -p /auto-loop-spec spec/spec_orchestrator_agent_security_fixes_3.md"
  fi
  sleep "$INTERVAL"
done
