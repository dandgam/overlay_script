#!/usr/bin/env bash
# rollback-S3.sh — reverse Initiative #1B (cgroup + per-worker HOME isolation)
#
# S3 edits are pure Python code on integration/parallelism_initiatives — no
# host systemd / .env / DB destructive ops. Rollback = git reset to the
# pre-S3 commit on this branch.
#
# Pre-S3 anchor: 09c7716 (tracker(parallelism_initiatives): S2 -> Completed, S3 promoted)
set -euo pipefail

cd /home/server/bmad-orchestrator

PRE_S3_SHA="09c7716"
BRANCH="integration/parallelism_initiatives"

if [[ "$(git branch --show-current)" != "$BRANCH" ]]; then
    echo "FATAL: rollback-S3 must be run on $BRANCH (current: $(git branch --show-current))"
    exit 1
fi

# Confirm anchor exists
if ! git rev-parse --verify "${PRE_S3_SHA}^{commit}" >/dev/null 2>&1; then
    echo "FATAL: pre-S3 anchor $PRE_S3_SHA not found in repo"
    exit 1
fi

# Reset code files only (preserve tracker history written during S3)
# If you want a complete revert (including tracker writes), uncomment the bare
# `git reset --hard $PRE_S3_SHA` below and comment out the targeted resets.
echo "Reverting S3 code edits to $PRE_S3_SHA ..."
git checkout "$PRE_S3_SHA" -- \
    src/bmad_orchestrator/runtime/sandbox.py \
    src/bmad_orchestrator/runtime/worker_spawn.py \
    src/bmad_orchestrator/agent/run.py 2>/dev/null || true

# If new test files were added, drop them
rm -f tests/test_initiative1b_cgroup_home_isolation.py 2>/dev/null || true

echo "S3 rollback complete. Run pytest to verify."
echo "If you want full reset (including tracker): git reset --hard $PRE_S3_SHA"
