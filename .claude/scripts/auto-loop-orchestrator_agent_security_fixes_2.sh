#!/bin/bash
# Auto-loop wrapper for orchestrator_agent_security_fixes_2 (manual bootstrap, 2026-05-16).
# Spec: spec/spec_orchestrator_agent_security_fixes_2.md
# Slug: orchestrator_agent_security_fixes_2
# Base: integration/orchestrator_agent_security_fixes (cumulative)
# Delay between sessions: 600s

set -u
cd /home/server/bmad-orchestrator || exit 1

TRACKER=".claude/initiative-tracker-orchestrator_agent_security_fixes_2.md"
LOG=".claude/auto-loop-orchestrator_agent_security_fixes_2.log"
DELAY=600
SPEC="spec/spec_orchestrator_agent_security_fixes_2.md"
INTEGRATION="integration/orchestrator_agent_security_fixes_2"
PROMOTE_SCRIPT=".claude/scripts/auto-promote-tracker.py"

while true; do
  if [ ! -f "$TRACKER" ]; then
    echo "=== $(date -u) FATAL: tracker missing, exiting ===" | tee -a "$LOG"
    exit 1
  fi
  FINAL_BODY=$(awk '/^## Final Report/{flag=1;next} flag && /^## /{exit} flag' "$TRACKER" | tr -d '[:space:]')
  FINAL_LEN=${#FINAL_BODY}
  if grep -q '^## Final Report$' "$TRACKER" && \
     ! awk '/^## Final Report/{flag=1;next} flag && /^## /{exit} flag' "$TRACKER" | grep -qE '\(empty|^pending$|^\(pending'; then
    if [ "$FINAL_LEN" -ge 80 ]; then
      echo "=== $(date -u) Final Report populated (${FINAL_LEN} chars body) — initiative done, exiting ===" | tee -a "$LOG"
      break
    fi
  fi
  if tail -120 "$TRACKER" | grep -qE '^[[:space:]]*\*?\*?resolution:\*?\*?[[:space:]]+PENDING[[:space:]]*$'; then
    echo "=== $(date -u) PENDING blocker — exiting for human review ===" | tee -a "$LOG"
    break
  fi

  if [ -x "$PROMOTE_SCRIPT" ]; then
    python3 "$PROMOTE_SCRIPT" "$TRACKER" "$INTEGRATION" 2>&1 | tee -a "$LOG"
    PROMOTE_RC=${PIPESTATUS[0]}
    if [ "$PROMOTE_RC" -eq 0 ]; then
      echo "=== $(date -u) zombie recovered — re-checking ===" | tee -a "$LOG"
      continue
    fi
  fi

  echo "=== $(date -u) starting session (fresh context, runtime=loop_wrapper, base=integration/orchestrator_agent_security_fixes) ===" | tee -a "$LOG"
  timeout 3600 claude -p "/auto-loop-spec $SPEC" \
    --model opus \
    --dangerously-skip-permissions \
    2>&1 | tee -a "$LOG"
  RC=$?
  if [ "$RC" -eq 124 ]; then
    echo "=== $(date -u) WARNING: session hit 1h timeout (rc=124) — wrapper continues ===" | tee -a "$LOG"
  elif [ "$RC" -ne 0 ]; then
    echo "=== $(date -u) NOTE: session exited with rc=$RC ===" | tee -a "$LOG"
  fi
  echo "=== $(date -u) session done, sleeping ${DELAY}s ===" | tee -a "$LOG"
  sleep "$DELAY"
done
