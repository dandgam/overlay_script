#!/bin/bash
set -u
cd "$(dirname "$0")/../.." || exit 1
TRACKER=".claude/initiative-tracker-embed_phase45_fixes.md"
LOG=".claude/auto-loop-embed_phase45_fixes.log"
DELAY=300
SPEC="spec/spec_embed_phase45_fixes.md"
INTEGRATION="integration/embed_phase45_fixes"
PROMOTE_SCRIPT=".claude/scripts/auto-promote-tracker.py"

while true; do
  if [ ! -f "$TRACKER" ]; then echo "=== $(date -u) FATAL ===" | tee -a "$LOG"; exit 1; fi
  FINAL_BODY=$(awk '/^## Final Report/{flag=1;next} flag && /^## /{exit} flag' "$TRACKER" | tr -d '[:space:]')
  FINAL_LEN=${#FINAL_BODY}
  if grep -q '^## Final Report$' "$TRACKER" && \
     ! awk '/^## Final Report/{flag=1;next} flag && /^## /{exit} flag' "$TRACKER" | grep -qE '\(empty|^pending$|^\(pending'; then
    if [ "$FINAL_LEN" -ge 80 ]; then
      echo "=== $(date -u) Final Report populated, exiting ===" | tee -a "$LOG"; break
    fi
  fi
  if tail -120 "$TRACKER" | grep -qE '^[[:space:]]*\*?\*?resolution:\*?\*?[[:space:]]+PENDING[[:space:]]*$'; then
    echo "=== $(date -u) PENDING blocker, exiting ===" | tee -a "$LOG"; break
  fi
  if [ -x "$PROMOTE_SCRIPT" ]; then
    python3 "$PROMOTE_SCRIPT" "$TRACKER" "$INTEGRATION" 2>&1 | tee -a "$LOG"
    PROMOTE_RC=${PIPESTATUS[0]}
    if [ "$PROMOTE_RC" -eq 0 ]; then continue; fi
  fi
  echo "=== $(date -u) starting session ===" | tee -a "$LOG"
  timeout 3600 claude -p "/auto-loop-spec $SPEC" --model opus --dangerously-skip-permissions 2>&1 | tee -a "$LOG"
  RC=$?
  if [ "$RC" -eq 124 ]; then echo "=== $(date -u) WARNING: timeout ===" | tee -a "$LOG"; elif [ "$RC" -ne 0 ]; then echo "=== $(date -u) NOTE: rc=$RC ===" | tee -a "$LOG"; fi
  echo "=== $(date -u) session done, sleeping ${DELAY}s ===" | tee -a "$LOG"
  sleep "$DELAY"
done
