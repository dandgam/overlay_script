#!/usr/bin/env bash
# bmad-auto-dev-runner.sh — per-story BMad cycle orchestrator (Phase 1).
#
# Implements workflow Stages 0-8 of SKILL.md:
#   0. Pre-flight (project detection, clean working tree, state dir)
#   1. Select next ready story  (dependency_analyzer.py --next)
#   2. Branch creation          (feature/story-<id>)
#   3. Gauntlet enrichment      (gauntlet_injector.py)
#   4. create-story  (claude -p, Opus)
#   5. dev-story     (claude -p, Sonnet)
#   6. code-review   (claude -p, Opus)  → on PASS merge+cleanup+sprint-status update;
#                                        on FAIL HALT (state preserved)
#   7. Batch gate    (batch_gate.py --check)  → on boundary HALT (checkpoint)
#
# CLI:
#   bmad-auto-dev-runner.sh                              # one full iteration (one story)
#   bmad-auto-dev-runner.sh --dry-run                    # show what would happen; no claude -p, no commits
#   bmad-auto-dev-runner.sh --max N                      # run up to N iterations then exit
#   bmad-auto-dev-runner.sh --project DIR                # operate on DIR (default: $PWD)
#   bmad-auto-dev-runner.sh --resume                     # resume from halt state (clears halt-reason.txt)
#   bmad-auto-dev-runner.sh --batch-name <name>          # auto-create/reuse integration/<name> branch (Patch A 2026-05-14)
#   bmad-auto-dev-runner.sh --no-auto-retry              # disable auto-retry on review fail (default: 1 retry; Patch B 2026-05-14)
#
# Exit codes:
#   0   success — either iteration done OR clean halt at batch boundary
#   1   pre-flight failed
#   2   halt-on-fail (code review fail / claude -p non-zero)
#   3   halt-on-checkpoint (batch boundary reached — normal, awaits human)
#   4   internal error
#
# Phase 1 design choices:
#   - Sequential (one story at a time). Parallelism deferred to Phase 2.
#   - Halt-on-fail: any non-zero from claude -p OR code review verdict != PASS → HALT.
#   - State preserved on halt for AABIT inspection; --resume clears it.
#   - sprint-status.yaml updates protected by flock(1).
#   - Dry-run skips claude -p invocations + git mutations; uses --print-mode where available.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"

# --- argument parsing --------------------------------------------------------
DRY_RUN=0
MAX_ITER=1
PROJECT_DIR=""
RESUME=0
BATCH_NAME=""        # Patch A 2026-05-14: --batch-name → auto integration branch
AUTO_RETRY=1         # Patch B 2026-05-14: 1 = retry once on review fail (default); 0 = no retry

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --max) MAX_ITER="$2"; shift 2 ;;
    --project) PROJECT_DIR="$2"; shift 2 ;;
    --resume) RESUME=1; shift ;;
    --batch-name) BATCH_NAME="$2"; shift 2 ;;
    --no-auto-retry) AUTO_RETRY=0; shift ;;
    -h|--help)
      sed -n '2,33p' "$0"
      exit 0 ;;
    *) echo "[runner] unknown arg: $1" >&2; exit 4 ;;
  esac
done

PROJECT_DIR="${PROJECT_DIR:-$PWD}"
cd "$PROJECT_DIR"

# --- helpers ----------------------------------------------------------------
log()  { printf '[runner %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
warn() { printf '[runner %s] WARN: %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
fail() { printf '[runner %s] FAIL: %s\n' "$(date +%H:%M:%S)" "$*" >&2; exit 2; }

# Stage 7 graceful feature-branch cleanup (#2 NEW-2 Layer A, 2026-05-19).
# `git branch -d` refuses with "error: cannot delete branch '<b>' used by
# worktree at '<path>'" when a reused/stale worktree still holds the branch
# checked out. Under `set -e` that aborted the whole runner (exit 1) AFTER the
# Stage 6 merge — losing the orchestrator's chance to record the verdict and
# the work that already passed Stage 4-6 + autofix (real Antares Epic 1: 3/3
# stories halted identically). Now the delete is skipped gracefully:
#   - structured `stage7_skipped reason=... branch=... worktree=...` log line;
#   - when the held branch carries commits past its fork point, a synthetic
#     `verdict=approve` claude_event is printed to stdout so the orchestrator's
#     merge subscriber still recovers the work;
#   - `BMAD_RUNNER_SKIP_STAGE7=1` forces the skip unconditionally.
# Always returns 0 — cleanup failure must never crash the runner.
stage7_cleanup_feature_branch() {
  local branch="$1" integ="$2"
  if [[ "${BMAD_RUNNER_SKIP_STAGE7:-0}" == "1" ]]; then
    log "stage7_skipped reason=env_override branch=$branch"
    return 0
  fi
  local wt_path=""
  wt_path="$(git worktree list --porcelain 2>/dev/null \
    | awk -v b="branch refs/heads/$branch" '
        /^worktree /{p=substr($0,10)}
        $0==b{print p; exit}')"
  if [[ -n "$wt_path" ]]; then
    log "stage7_skipped reason=used_by_worktree branch=$branch worktree=$wt_path"
    local base commits
    base="$(git merge-base "$integ" "$branch" 2>/dev/null || true)"
    if [[ -n "$base" ]]; then
      commits="$(git rev-list --count "${base}..${branch}" 2>/dev/null || echo 0)"
      if [[ "${commits:-0}" -gt 0 ]]; then
        printf '{"event_type":"claude_event","verdict":"approve","source":"runner_stage7_skip","commits":%s,"branch":"%s"}\n' \
          "$commits" "$branch"
        log "stage7 synthetic verdict=approve commits=$commits branch=$branch"
      fi
    fi
    return 0
  fi
  if git branch -d "$branch" 2>&1; then
    log "stage7 feature branch deleted branch=$branch"
  else
    warn "stage7 git branch -d failed branch=$branch — leaving branch in place"
  fi
  return 0
}

STATE_DIR="_bmad/auto-dev-state"
HALT_FILE="$STATE_DIR/halt-reason.txt"
BATCH_FILE="$STATE_DIR/current-batch.json"
CHECKPOINT_DIR="$STATE_DIR/checkpoint-log"

# Patch Y 2026-05-18: BMad layout auto-detect (Antares pilot lesson).
# Stock BMM v6 install: _bmad/output/planning/ (epics, stories, sprint-status).
# Odyssey hybrid: _bmad/planning-artifacts/ + _bmad/implementation-artifacts/ + _bmad/stories/.
# Env vars override > BMM v6 stock > Odyssey hybrid > fail.
if [[ -n "${BMAD_EPICS_FILE:-}" ]]; then
  EPICS_FILE="$BMAD_EPICS_FILE"
  SPRINT_STATUS="${BMAD_SPRINT_STATUS:?BMAD_SPRINT_STATUS required when BMAD_EPICS_FILE set}"
  STORIES_DIR="${BMAD_STORIES_DIR:?BMAD_STORIES_DIR required when BMAD_EPICS_FILE set}"
  PLANNING_DIR="${BMAD_PLANNING_DIR:-$(dirname "$EPICS_FILE")}"
  IMPL_DIR="${BMAD_IMPL_DIR:-$PLANNING_DIR}"
elif [[ -d "_bmad/output/planning" ]]; then
  PLANNING_DIR="_bmad/output/planning"
  STORIES_DIR="_bmad/output/planning/stories"
  EPICS_FILE="_bmad/output/planning/epics.md"
  SPRINT_STATUS="_bmad/output/planning/stories/sprint-status.yaml"
  IMPL_DIR="_bmad/output/implementation"
elif [[ -d "_bmad/planning-artifacts" ]]; then
  PLANNING_DIR="_bmad/planning-artifacts"
  STORIES_DIR="_bmad/stories"
  EPICS_FILE="_bmad/planning-artifacts/epics.md"
  SPRINT_STATUS="_bmad/implementation-artifacts/sprint-status.yaml"
  IMPL_DIR="_bmad/implementation-artifacts"
else
  printf '[runner] FAIL: no BMad layout detected (need _bmad/output/planning OR _bmad/planning-artifacts)\n' >&2
  exit 1
fi

# Export resolved paths so Python helpers (dependency_analyzer.py, batch_gate.py,
# gauntlet_injector.py) inherit them instead of falling back to Odyssey defaults.
export BMAD_EPICS_FILE="$EPICS_FILE"
export BMAD_SPRINT_STATUS="$SPRINT_STATUS"
export BMAD_STORIES_DIR="$STORIES_DIR"
export BMAD_PLANNING_DIR="$PLANNING_DIR"
export BMAD_IMPL_DIR="$IMPL_DIR"

DEP_ANALYZER="$SCRIPT_DIR/dependency_analyzer.py"
GAUNTLET="$SCRIPT_DIR/gauntlet_injector.py"
BATCH_GATE="$SCRIPT_DIR/batch_gate.py"

# Patch G v2 2026-05-15: ∞ auto-retry transient Anthropic API errors (AABIT request:
#   «если ошибка сети — пытаться бесконечно пока не появится связь»). Separate budget
#   from Patch H: API/network errors retry indefinitely with exponential back-off (30s
#   → 60s → 120s → 300s cap). Anthropic probe between retries informational only.
# Patch H 2026-05-15 + S3 #8 (2026-05-19): hard timeout 3600s (60 min)
#   hang ceiling per claude -p call — bounded retries (real hang is BAD,
#   shouldn't loop forever). Default 2 attempts.
# Reasoning (post Story 1.5 24-min hang lesson): stdout silence ≠ hang (Sonnet
# can edit files 15+ min without stdout). Real hang signature is process not
# exiting beyond N min wall clock. timeout(1) gives us hard ceiling without
# false positives.
#
# Default raised from 1800 → 3600 after Antares 1.3 + 1.5 subprocess_timeout
# halts on legitimately heavy stories (12+ AC). Override via
# BMAD_RUNNER_CLAUDE_TIMEOUT_SEC (preferred) or PATCH_H_HARD_CEILING_SECS
# (legacy). Adaptive per-story bump happens later in the story loop via
# `python3 -m bmad_orchestrator.runtime.subprocess_timeout`.
#
# Output streamed via tee to log file (passed as $1). Remaining args = claude command.
# Returns: 0 on success, non-zero on hang-budget exhaustion or non-retryable error.
PATCH_H_HARD_CEILING_SECS="${PATCH_H_HARD_CEILING_SECS:-${BMAD_RUNNER_CLAUDE_TIMEOUT_SEC:-3600}}"
PATCH_G_TIMEOUT_MAX_ATTEMPTS="${PATCH_G_TIMEOUT_MAX_ATTEMPTS:-2}"      # hang ceiling retries (bounded)
PATCH_G_API_BACKOFF_INITIAL="${PATCH_G_API_BACKOFF_INITIAL:-30}"        # API error first back-off (sec)
PATCH_G_API_BACKOFF_MAX="${PATCH_G_API_BACKOFF_MAX:-300}"               # API error back-off cap (sec)
PATCH_G_API_PROBE_URL="${PATCH_G_API_PROBE_URL:-https://api.anthropic.com}"
claude_with_api_retry() {
  local log_path="$1"; shift
  local timeout_attempt=0
  local api_attempt=0
  local backoff probe_rc tmp_out rc
  tmp_out="$(mktemp -t bmad-auto-dev.XXXXX)"
  trap "rm -f '$tmp_out'" RETURN

  while true; do
    # Patch H: wrap in `timeout` for hard wall-clock ceiling.
    if timeout --foreground --kill-after=10s "$PATCH_H_HARD_CEILING_SECS" "$@" > "$tmp_out" 2>&1; then
      cat "$tmp_out" | tee -a "$log_path"
      rm -f "$tmp_out"
      return 0
    fi
    rc=$?
    cat "$tmp_out" | tee -a "$log_path"

    # Patch H: rc=124 = timeout fired (SIGTERM); rc=137 = SIGKILL after --kill-after.
    if (( rc == 124 || rc == 137 )); then
      timeout_attempt=$((timeout_attempt+1))
      if (( timeout_attempt < PATCH_G_TIMEOUT_MAX_ATTEMPTS )); then
        warn "Patch H — hard timeout ${PATCH_H_HARD_CEILING_SECS}s (hang) attempt $timeout_attempt/$PATCH_G_TIMEOUT_MAX_ATTEMPTS; back-off 60s + retry"
        sleep 60
        continue
      else
        warn "Patch H — hard timeout ${PATCH_H_HARD_CEILING_SECS}s FINAL ($timeout_attempt/$PATCH_G_TIMEOUT_MAX_ATTEMPTS); halting"
        rm -f "$tmp_out"
        return $rc
      fi
    elif grep -qE 'API Error|socket connection|socket closed|Connection reset|fetch failed|Network error|ECONNRESET|ETIMEDOUT|HTTP 5[0-9][0-9]|rate.?limit' "$tmp_out"; then
      api_attempt=$((api_attempt+1))
      backoff=$(( PATCH_G_API_BACKOFF_INITIAL * (2 ** (api_attempt - 1)) ))
      (( backoff > PATCH_G_API_BACKOFF_MAX )) && backoff=$PATCH_G_API_BACKOFF_MAX
      warn "Patch G v2 — transient API error (attempt $api_attempt, ∞ budget); back-off ${backoff}s + retry"
      sleep "$backoff"
      probe_rc=$(curl -sS --max-time 8 -o /dev/null -w '%{http_code}' "$PATCH_G_API_PROBE_URL" 2>/dev/null || echo "000")
      log "Patch G v2 — Anthropic probe before retry: HTTP $probe_rc"
      continue
    fi
    rm -f "$tmp_out"
    return $rc
  done
}

# Read field from state via python json (avoids jq dependency).
state_field() {
  local field="$1"
  [[ -f "$BATCH_FILE" ]] || { echo ""; return 0; }
  python3 -c "
import json,sys
try:
    d = json.load(open('$BATCH_FILE'))
    v = d.get('$field')
    print(v if v is not None else '')
except Exception:
    print('')
"
}

state_write() {
  local last_story="$1" integration_branch="$2"
  python3 - "$BATCH_FILE" "$last_story" "$integration_branch" <<'PY'
import json, sys, os
path, last, integ = sys.argv[1:]
os.makedirs(os.path.dirname(path), exist_ok=True)
try:
    d = json.load(open(path)) if os.path.exists(path) else {}
except Exception:
    d = {}
d.setdefault("stories", [])
if last and (not d["stories"] or d["stories"][-1] != last):
    d["stories"].append(last)
if last:
    d["last_story_id"] = last
if integ:
    d["integration_branch"] = integ
json.dump(d, open(path, "w"), indent=2)
PY
}

# Patch B 2026-05-14: track retry count per story in state file.
state_write_retry() {
  local story="$1" count="$2"
  python3 - "$BATCH_FILE" "$story" "$count" <<'PY'
import json, sys, os
path, sid, count = sys.argv[1:]
os.makedirs(os.path.dirname(path), exist_ok=True)
try:
    d = json.load(open(path)) if os.path.exists(path) else {}
except Exception:
    d = {}
d.setdefault("retries", {})
d["retries"][sid] = int(count)
json.dump(d, open(path, "w"), indent=2)
PY
}

state_read_retry() {
  local story="$1"
  [[ -f "$BATCH_FILE" ]] || { echo 0; return 0; }
  python3 -c "
import json
try:
    d = json.load(open('$BATCH_FILE'))
    print(d.get('retries', {}).get('$story', 0))
except Exception:
    print(0)
"
}

# Mark story done in sprint-status.yaml under flock.
sprint_status_mark_done() {
  local story_id="$1"
  local lockfile="${SPRINT_STATUS}.lock"
  (
    flock -x -w 30 9 || { warn "could not acquire flock on $lockfile"; return 1; }
    python3 - "$SPRINT_STATUS" "$story_id" <<'PY'
import re, sys, pathlib
path, sid = sys.argv[1], sys.argv[2]
p = pathlib.Path(path)
text = p.read_text()
# Patch K 2026-05-15: support both short keys ("1.1: backlog") AND long keys
# with slug suffix ("1-1-rust-workspace-scaffold: backlog"). Long keys use
# dash separator instead of dot. Normalize sid "1.6" -> "1-6" for prefix match.
short_pat = re.compile(r"^(\s+)(" + re.escape(sid) + r"):\s*([a-z][a-z-]*)\s*(#.*)?$", re.MULTILINE)
sid_dash = sid.replace(".", "-")
long_pat = re.compile(r"^(\s+)(" + re.escape(sid_dash) + r"-[a-z0-9-]+):\s*([a-z][a-z-]*)\s*(#.*)?$", re.MULTILINE)
def repl(m):
    indent, key, _status, comment = m.groups()
    tail = f" {comment}" if comment else ""
    return f"{indent}{key}: done{tail}"
new, n = short_pat.subn(repl, text)
if n == 0:
    new, n = long_pat.subn(repl, text)
if n == 0:
    sys.stderr.write(f"sprint-status: story {sid} key not found (tried short '{sid}' and long '{sid_dash}-*')\n")
    sys.exit(2)
p.write_text(new)
print(f"sprint-status: {sid} -> done ({n} match)")
PY
  ) 9>"$lockfile"
}

# --- Stage 0: pre-flight ----------------------------------------------------
log "Stage 0 — pre-flight (project=$PROJECT_DIR, dry-run=$DRY_RUN, max=$MAX_ITER, resume=$RESUME)"

[[ -f "$EPICS_FILE" ]]        || { warn "missing $EPICS_FILE"; exit 1; }
[[ -f "$SPRINT_STATUS" ]]     || { warn "missing $SPRINT_STATUS"; exit 1; }
[[ -x "$DEP_ANALYZER" ]] || [[ -f "$DEP_ANALYZER" ]] || { warn "missing $DEP_ANALYZER"; exit 1; }
[[ -f "$GAUNTLET" ]]          || { warn "missing $GAUNTLET"; exit 1; }
[[ -f "$BATCH_GATE" ]]        || { warn "missing $BATCH_GATE"; exit 1; }

mkdir -p "$STATE_DIR" "$CHECKPOINT_DIR" "$STORIES_DIR"

if [[ "$RESUME" -eq 1 && -f "$HALT_FILE" ]]; then
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "Stage 0 — DRY: would clear $HALT_FILE (--resume); not touching it"
  else
    log "Stage 0 — resume: clearing $HALT_FILE"
    rm -f "$HALT_FILE"
  fi
elif [[ -f "$HALT_FILE" ]]; then
  warn "halt state present at $HALT_FILE — use --resume to clear it"
  cat "$HALT_FILE" >&2
  exit 2
fi

if [[ "$DRY_RUN" -eq 0 ]]; then
  if ! git diff --quiet || ! git diff --cached --quiet; then
    warn "git working tree not clean — commit or stash before running"
    exit 1
  fi
fi

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"

# Patch A 2026-05-14: --batch-name auto-creates/reuses integration/<name> branch.
# Without --batch-name fallback: use state file's integration_branch, else current branch.
if [[ -n "$BATCH_NAME" ]]; then
  INTEG_BRANCH="integration/$BATCH_NAME"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    if git show-ref --verify --quiet "refs/heads/$INTEG_BRANCH"; then
      log "Stage 0 — switching to existing $INTEG_BRANCH"
      git checkout "$INTEG_BRANCH"
    else
      log "Stage 0 — creating $INTEG_BRANCH from main"
      git checkout main
      git checkout -b "$INTEG_BRANCH"
    fi
  else
    log "Stage 0 — DRY: would use $INTEG_BRANCH (create from main if not exists)"
  fi
  state_write "" "$INTEG_BRANCH"
else
  INTEG_BRANCH="$(state_field integration_branch)"
  [[ -z "$INTEG_BRANCH" ]] && INTEG_BRANCH="$CURRENT_BRANCH"
fi
log "Stage 0 — integration branch: $INTEG_BRANCH"

# --- main loop --------------------------------------------------------------
iter=0
while (( iter < MAX_ITER )); do
  iter=$((iter+1))
  log "===== iteration $iter / $MAX_ITER ====="

  # Stage 1 — pick story ---------------------------------------------------
  # Patch Z 2026-05-18: respect ORCHESTRATOR_WORKER_STORY_ID when set by the
  # orchestrator. The orchestrator's DAG planner already chose the story; the
  # runner's independent select_next would otherwise override the choice (e.g.
  # pick the next backlog story instead of the marked ready-for-dev spike).
  if [[ -n "${ORCHESTRATOR_WORKER_STORY_ID:-}" ]]; then
    story_id="$ORCHESTRATOR_WORKER_STORY_ID"
    log "Stage 1 — story_id forced by orchestrator: $story_id"
  else
    log "Stage 1 — selecting next ready story"
    next_json="$(python3 "$DEP_ANALYZER" --next)"
    echo "  $next_json"
    story_id="$(echo "$next_json" | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get("story_id",""))')"
    all_done="$(echo "$next_json" | python3 -c 'import json,sys;d=json.load(sys.stdin);print("1" if d.get("all_done") else "")')"
    if [[ -n "$all_done" ]]; then
      log "Stage 1 — all stories done; nothing to do"
      exit 0
    fi
    [[ -n "$story_id" ]] || { warn "dependency_analyzer returned no story_id"; exit 4; }
  fi

  # Stage 2 — branch creation --------------------------------------------
  # Patch Y 2026-05-18: orchestrator-mode bypass. When invoked from
  # bmad-orchestrator, the worktree is ALREADY on a `feature/<id>` branch
  # created by the orchestrator (and main repo `.git/refs/` is sandbox-
  # read-only). Detect and reuse instead of `git checkout -b`.
  current_branch="$(git rev-parse --abbrev-ref HEAD)"
  if [[ "$current_branch" == feature/* ]]; then
    feature_branch="$current_branch"
    log "Stage 2 — already on feature branch: $feature_branch (orchestrator-mode bypass)"
  else
    feature_branch="feature/story-${story_id}"
    log "Stage 2 — branch: $feature_branch (from $INTEG_BRANCH)"
    if [[ "$DRY_RUN" -eq 0 ]]; then
      if git show-ref --verify --quiet "refs/heads/$feature_branch"; then
        # F2: branch exists — suffix retry-N
        n=1
        while git show-ref --verify --quiet "refs/heads/${feature_branch}-retry-${n}"; do
          n=$((n+1))
        done
        feature_branch="${feature_branch}-retry-${n}"
        log "Stage 2 — branch existed; using $feature_branch"
      fi
      git checkout -b "$feature_branch"
    fi
  fi

  # Stage 3 — Gauntlet enrichment ----------------------------------------
  log "Stage 3 — Gauntlet (--print-mode)"
  mode_json="$(python3 "$GAUNTLET" --story "$story_id" --print-mode)"
  echo "  $mode_json"
  mode="$(echo "$mode_json" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("mode",""))')"
  [[ -n "$mode" ]] || { warn "gauntlet returned no mode"; exit 4; }
  # Patch M 2026-05-16: BMAD_GAUNTLET_FORCE_MODE env override (case: edge-proxy 15-min
  #   timeout on Opus deep-thinking; force quick for problematic stories without retag).
  # Format: "story_id:mode,story_id:mode" (e.g. "1.10b:quick,1.11:quick").
  if [[ -n "${BMAD_GAUNTLET_FORCE_MODE:-}" ]]; then
    override="$(echo "$BMAD_GAUNTLET_FORCE_MODE" | tr ',' '\n' | grep "^${story_id}:" | head -1 | cut -d: -f2)"
    if [[ -n "$override" ]]; then
      warn "Patch M — overriding Gauntlet mode '$mode' → '$override' for $story_id (BMAD_GAUNTLET_FORCE_MODE)"
      mode="$override"
    fi
  fi
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "Stage 3 — DRY: would run gauntlet_injector --story $story_id --mode $mode (prompts not invoked)"
  else
    enriched_dir="$STATE_DIR/gauntlet/${story_id}"
    mkdir -p "$enriched_dir"
    # Pure prompt generation (no claude subprocess) — use --dry-run to emit JSON prompts.
    python3 "$GAUNTLET" --story "$story_id" --mode "$mode" --dry-run \
      > "$enriched_dir/prompts.json"
    log "Stage 3 — prompts written: $enriched_dir/prompts.json"

    # Patch J 2026-05-15: enforce all 5 elicitation lenses present BEFORE Stage 4.
    # Quick mode produces 1 combined prompt that must contain all 5 lens sections;
    # Deep mode produces 5 separate prompts. Either way, all 5 must be detectable.
    # Halt early if missing — better than dev-story Sonnet getting broken spec.
    log "Stage 3.5 — Patch J: validating Gauntlet emitted all 5 lenses"
    if ! python3 - "$enriched_dir/prompts.json" <<'PJ'
import json, re, sys
p = sys.argv[1]
d = json.load(open(p))
prompts = d.get("prompts", [])
if not prompts:
    sys.stderr.write(f"Patch J FAIL: no prompts in {p}\n")
    sys.exit(1)
# Scan BOTH name and body — lens markers live in name (e.g. "Pre-mortem"),
# body describes the technique without naming it. 2026-05-15 Patch J v1.1 fix.
body = "\n".join(pr.get("name", "") + "\n" + pr.get("body", "") for pr in prompts)
# Required 5 lenses (from gauntlet-prompts.md template, case-insensitive markers)
required = {
    "Failure Mode": [r"Failure[\s-]?Mode", r"failure[\s-]?mode"],
    "Edge Case": [r"Edge[\s-]?Case"],
    "Pre-mortem": [r"Pre[-\s]?mortem"],
    "Devil's Advocate": [r"Devil['\u2019]?s?[\s-]?Advocate"],
    "Security Red Team": [r"Security[\s-]?Red[\s-]?Team", r"STRIDE"],
}
missing = []
for name, patterns in required.items():
    if not any(re.search(pat, body, re.IGNORECASE) for pat in patterns):
        missing.append(name)
if missing:
    sys.stderr.write(f"Patch J FAIL: missing lenses in {p}: {missing}\n")
    sys.exit(1)
print(f"Patch J OK: all 5 lenses detected in {p}")
PJ
    then
      printf 'stage=3.5 story=%s reason=patch-j-gauntlet-missing-lenses ts=%s\n' \
        "$story_id" "$(date -Is)" > "$HALT_FILE"
      fail "Stage 3.5 — Patch J: Gauntlet prompts incomplete; halt -> $HALT_FILE"
    fi
  fi

  # Stage 4-6 — claude -p chain ------------------------------------------
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "Stage 4 — DRY: would spawn 'claude -p \"bmad-create-story $story_id\"'"
    log "Stage 5 — DRY: would spawn 'claude --model sonnet -p \"bmad-dev-story $story_id\"'"
    log "Stage 6 — DRY: would spawn 'claude -p \"bmad-code-review $story_id\"'"
    review_verdict="DRY-PASS"
  else
    log "Stage 4 — create-story (claude -p, opus) [Patch G: auto-retry on API errors]"
    stage4_log="$STATE_DIR/reviews/${story_id}-stage4.log"
    mkdir -p "$(dirname "$stage4_log")"
    # Patch O 2026-05-16: autonomous-decisions directive — Stage 4 Opus иногда вытягивает
    # «accept/reject» вопросы по Gauntlet findings и halts ждать human. Pipeline = autonomous.
    # Default per memory feedback_default_resolutions: accept all G-findings, write file, continue.
    # Story 1.14 lesson.
    if ! claude_with_api_retry "$stage4_log" claude -p "Execute bmad-create-story for story $story_id. \
Use enriched prompts at $STATE_DIR/gauntlet/${story_id}/prompts.json. \
Output story file to ${STORIES_DIR}/${story_id}.md. \
Working dir: $PROJECT_DIR. \
CRITICAL: This is an autonomous pipeline — NEVER prompt for human approval. If create-story workflow asks 'accept/reject/modify' for Gauntlet G-findings: ACCEPT ALL by default and write the story file. Bake all convergent findings into ACs. Decisions left to caller are documented as deferred risks (R-list) in story file."; then
      printf 'stage=4 (create-story) story=%s branch=%s reason=claude-nonzero-after-retry ts=%s\n' \
        "$story_id" "$feature_branch" "$(date -Is)" > "$HALT_FILE"
      fail "Stage 4 — create-story exited non-zero после 2 attempts; halt state -> $HALT_FILE"
    fi
    [[ -f "${STORIES_DIR}/${story_id}.md" ]] || {
      printf 'stage=4 story=%s reason=missing-story-file ts=%s\n' \
        "$story_id" "$(date -Is)" > "$HALT_FILE"
      fail "Stage 4 — story file ${STORIES_DIR}/${story_id}.md not produced"
    }

    log "Stage 5 — dev-story (claude --model sonnet -p) [Patch G: auto-retry]"
    stage5_log="$STATE_DIR/reviews/${story_id}-stage5.log"
    if ! claude_with_api_retry "$stage5_log" claude --model sonnet -p "Execute bmad-dev-story $story_id. \
Read ${STORIES_DIR}/${story_id}.md. Implement code. Commit on branch $feature_branch. \
CRITICAL: Before declaring done, run \`cargo check --workspace\` (or equivalent for non-Rust stories) and ensure 0 errors. \
If you create a new crate under crates/ or apps/, MUST include: Cargo.toml + src/lib.rs (libs) or src/main.rs (bins) + module mod.rs files. \
Working dir: $PROJECT_DIR."; then
      printf 'stage=5 (dev-story) story=%s branch=%s reason=claude-nonzero-after-retry ts=%s\n' \
        "$story_id" "$feature_branch" "$(date -Is)" > "$HALT_FILE"
      fail "Stage 5 — dev-story exited non-zero после 2 attempts; halt state -> $HALT_FILE"
    fi

    # Patch N 2026-05-16: Stage 5.5 — build check guard. Before sending broken code to
    # expensive Opus Stage 6 review, verify workspace compiles. Catches missing Cargo.toml,
    # unresolved imports, type mismatches (Story 1.11 lesson: 2806-line auto-fix referenced
    # types not written; Opus review missed because reviewer reads code, not build graph).
    # Cost: ~$0.001 (cargo check ≈10s) vs ~$15 wasted Opus review on broken build.
    # If cargo missing or non-Rust story → skip (warn only). If check fails → retry Sonnet
    # with build errors as context (Patch N.retry, ≤2 attempts), then halt.
    if command -v cargo &>/dev/null && [[ -f "$PROJECT_DIR/Cargo.toml" ]]; then
      log "Stage 5.5 — build check (cargo check --workspace) [Patch N]"
      build_log="$STATE_DIR/reviews/${story_id}-stage5.5-build.log"
      build_attempt=1
      PATCH_N_MAX_RETRIES="${PATCH_N_MAX_RETRIES:-2}"
      while (( build_attempt <= PATCH_N_MAX_RETRIES )); do
        if (cd "$PROJECT_DIR" && cargo check --workspace --message-format=short) > "$build_log" 2>&1; then
          log "Stage 5.5 — cargo check clean ✓"
          break
        fi
        warn "Stage 5.5 — cargo check FAILED on attempt $build_attempt/$PATCH_N_MAX_RETRIES; feeding errors back to Sonnet"
        cargo_err_tail="$(tail -50 "$build_log" | head -40)"
        if (( build_attempt < PATCH_N_MAX_RETRIES )); then
          stage5_retry_log="$STATE_DIR/reviews/${story_id}-stage5.5-retry${build_attempt}.log"
          claude_with_api_retry "$stage5_retry_log" claude --model sonnet -p "Build is broken on branch $feature_branch for story $story_id. \
Fix the cargo check errors below — do NOT modify spec/conventions, only minimal code to resolve. \
Commit fix as 'fix(story-${story_id}): build errors (Patch N retry $build_attempt)'. \
Working dir: $PROJECT_DIR. \
\
=== cargo check errors (tail) ===\
$cargo_err_tail" || true
          build_attempt=$((build_attempt+1))
        else
          printf 'stage=5.5 (build-check) story=%s branch=%s reason=cargo-check-fail-after-%d-retries ts=%s\n' \
            "$story_id" "$feature_branch" "$build_attempt" "$(date -Is)" > "$HALT_FILE"
          fail "Stage 5.5 — cargo check failed after $PATCH_N_MAX_RETRIES Sonnet retries; halt state -> $HALT_FILE (log: $build_log)"
        fi
      done
    fi

    log "Stage 6 — code-review (claude -p, opus) [Patch G: auto-retry]"
    review_log="$STATE_DIR/reviews/${story_id}.log"
    mkdir -p "$(dirname "$review_log")"
    if ! claude_with_api_retry "$review_log" claude -p "Execute bmad-code-review for story $story_id on branch $feature_branch. \
Output verdict on last line: PASS, NEEDS-FIX, or BLOCKED. \
Working dir: $PROJECT_DIR."; then
      printf 'stage=6 (code-review) story=%s branch=%s reason=claude-nonzero-after-retry ts=%s\n' \
        "$story_id" "$feature_branch" "$(date -Is)" > "$HALT_FILE"
      fail "Stage 6 — code-review exited non-zero после 2 attempts; halt state -> $HALT_FILE"
    fi
    review_verdict="$(tail -n 5 "$review_log" | grep -Eo 'PASS|NEEDS-FIX|BLOCKED' | tail -n 1 || true)"
    [[ -n "$review_verdict" ]] || review_verdict="UNKNOWN"
    log "Stage 6 — verdict: $review_verdict"
  fi

  # Stage 6.retry — auto-fix (Patch B 2026-05-14) -------------------------
  # If verdict != PASS and AUTO_RETRY=1 and retry_count==0: invoke Sonnet to
  # apply targeted fixes, then re-run code-review. Safety guards prevent the
  # AI from "fixing" by deletion/test-removal/spec-rewrite.
  if [[ "$review_verdict" != "PASS" && "$review_verdict" != "DRY-PASS" \
        && "$AUTO_RETRY" -eq 1 && "$DRY_RUN" -eq 0 ]]; then
    retry_count="$(state_read_retry "$story_id")"
    if (( retry_count == 0 )); then
      log "Stage 6.retry — auto-fix attempt 1/1 (Sonnet); review log: $review_log"

      # Sanity snapshots BEFORE auto-fix
      pre_fix_files="$(git ls-files | wc -l)"
      pre_fix_tests="$(git ls-files | grep -cE '(test|spec|\.test\.|_test\.)' || true)"
      pre_fix_head="$(git rev-parse HEAD)"

      autofix_log="$STATE_DIR/reviews/${story_id}-autofix.log"
      # Patch I 2026-05-15: structured per-finding auto-apply prompt.
      # Lesson from Story 1.5 (23 findings): generic "fix the review" prompt
      # overwhelmed Sonnet and produced partial fixes. Structured per-P-id
      # instructions force Sonnet to enumerate findings explicitly and apply
      # them one by one, with build verification between batches.
      #
      # S3 #3 (2026-05-19): autofix model routing — security-critical stories
      # AND review iteration >= 2 route to Opus instead of default Sonnet.
      # Resolved via `python -m bmad_orchestrator.runtime.autofix_routing`;
      # falls back to `sonnet` on any error so the runner never breaks.
      autofix_iteration=$((retry_count + 1))
      autofix_model="$(python3 -m bmad_orchestrator.runtime.autofix_routing \
        --story-file "${STORIES_DIR}/${story_id}.md" \
        --iteration "$autofix_iteration" \
        --print-cli-name 2>/dev/null || echo sonnet)"
      autofix_model="${autofix_model:-sonnet}"
      log "Stage 6.retry — autofix model: $autofix_model (iteration=$autofix_iteration)"
      if claude --model "$autofix_model" -p "Auto-fix code review findings for BMad story $story_id on branch $feature_branch.

REVIEW LOG: $review_log (read this file first to extract structured findings).

STRUCTURED PROCEDURE — follow EXACTLY:

1. Parse the review log. Identify each finding by its identifier (P1, P2, ..., D1, D2, ...).
   For each finding extract: severity (Critical/High/Medium/Low), file:line location, suggested fix.

2. Produce a numbered checklist of findings to address:
   - Critical → ALWAYS address
   - High → ALWAYS address
   - Medium → address if fix is straightforward (<10 lines)
   - Low → SKIP (informational; defer to backlog)
   - Defer → SKIP (already in deferred-work.md)

3. For each item on the checklist, apply the fix exactly as suggested in the review:
   a. Read the cited file at the cited line range
   b. Apply minimal edit (no unrelated refactor)
   c. Write a one-line commit-summary entry: 'P<N>: <one-line>'
   d. If the fix requires adding a new test, ADD it (do not skip).

4. After all applicable fixes applied, run build verification:
   a. cargo fmt --all
   b. cargo clippy --workspace --all-targets -- -D warnings (or per-crate if workspace too broad)
   c. cargo test (for touched crates only; full workspace if cheap)
   For Rust changes. For TypeScript/JavaScript: pnpm lint + pnpm test in the relevant package.

5. If build fails on any check: fix the regression. Do not silence with allow attrs.

6. Stage changes explicitly (NEVER 'git add -A') — only files you intentionally modified.

7. Commit with message:
   'fix(story-$story_id): audit auto-fix — <comma-separated list of P-ids addressed>'

   Body should list each fix with one-line description.

CRITICAL CONSTRAINTS (violating ANY = safety guard halt):
- NEVER delete files (only modify/add)
- NEVER remove or skip existing tests
- NEVER modify the story spec (${STORIES_DIR}/${story_id}.md) or gauntlet prompts (_bmad/auto-dev-state/gauntlet/)
- NEVER modify Acceptance Criteria text
- TOTAL diff < 300 lines. If review has 20+ Critical+High findings exceeding 300 lines, STOP after first 5 fixes, commit those, and report: 'Auto-fix paused at 300-line budget — Critical+High remaining: <list>. Manual intervention required.'

Working dir: $PROJECT_DIR." 2>&1 | tee "$autofix_log"; then

        # Validate safety guards POST-fix (Patch C 2026-05-14: smart deletion check)
        post_fix_files="$(git ls-files | wc -l)"
        post_fix_tests="$(git ls-files | grep -cE '(test|spec|\.test\.|_test\.)' || true)"
        diff_insertions="$(git diff "$pre_fix_head" HEAD --shortstat 2>/dev/null | grep -oE '[0-9]+ insertion' | head -1 | grep -oE '[0-9]+' || echo 0)"
        diff_deletions="$(git diff "$pre_fix_head" HEAD --shortstat 2>/dev/null | grep -oE '[0-9]+ deletion' | head -1 | grep -oE '[0-9]+' || echo 0)"
        diff_total=$((diff_insertions + diff_deletions))
        files_deleted=$((pre_fix_files - post_fix_files))

        # Patch C: smart deletion check — allow safe deletions
        # Safe patterns: auto-generated (.gen.), build artifacts (dist/build/target),
        # lockfiles (regeneratable), caches (__pycache__, .tsbuildinfo), system junk
        # Logic: if ALL deleted files match safe patterns AND ≤5 deletions → allow
        deletion_guard=""
        if (( files_deleted > 0 )); then
          # Get list of files actually removed
          deleted_paths="$(git diff --name-only --diff-filter=D "$pre_fix_head" HEAD 2>/dev/null || true)"
          unsafe_deletes=""
          while IFS= read -r f; do
            [[ -z "$f" ]] && continue
            # Check if file matches safe-to-delete patterns
            if ! echo "$f" | grep -qE '(\.gen\.|/dist/|/build/|/target/|/node_modules/|__pycache__|\.pyc$|\.pyo$|\.DS_Store$|\.tsbuildinfo$|pnpm-lock\.yaml$|Cargo\.lock$|package-lock\.json$)'; then
              unsafe_deletes="${unsafe_deletes}${f}\n"
            fi
          done <<< "$deleted_paths"

          if [[ -n "$unsafe_deletes" ]]; then
            deletion_guard="unsafe files deleted:\n$(echo -e "$unsafe_deletes" | head -5)"
          elif (( files_deleted > 5 )); then
            deletion_guard="too many deletions (${files_deleted} files, even if safe patterns)"
          else
            log "Stage 6.retry — Patch C: ${files_deleted} safe deletions allowed (all match generated/build/lock patterns)"
          fi
        fi

        guard_failed=""
        if [[ -n "$deletion_guard" ]]; then
          guard_failed="$deletion_guard"
        elif (( post_fix_tests < pre_fix_tests )); then
          guard_failed="tests removed (${pre_fix_tests}→${post_fix_tests})"
        elif (( diff_total > 300 )); then
          guard_failed="diff too large (${diff_total} lines: +${diff_insertions}/-${diff_deletions})"
        fi

        if [[ -n "$guard_failed" ]]; then
          warn "Stage 6.retry — SAFETY GUARD failed: $guard_failed; reverting auto-fix and halting"
          git reset --hard "$pre_fix_head"
          state_write_retry "$story_id" "1"
        else
          # Safety OK — re-run code-review
          log "Stage 6.retry — re-review after auto-fix (Opus)"
          re_review_log="$STATE_DIR/reviews/${story_id}-retry-1.log"
          if claude -p "Re-execute bmad-code-review for story $story_id on branch $feature_branch after auto-fix. Previous review findings: $review_log. Auto-fix log: $autofix_log. Verify the fix addresses HIGH/MEDIUM defects without introducing new issues. Output verdict on last line: PASS, NEEDS-FIX, or BLOCKED. Working dir: $PROJECT_DIR." 2>&1 | tee "$re_review_log"; then
            retry_verdict="$(tail -n 5 "$re_review_log" | grep -Eo 'PASS|NEEDS-FIX|BLOCKED' | tail -n 1 || true)"
            retry_verdict="${retry_verdict:-UNKNOWN}"
            log "Stage 6.retry — re-review verdict: $retry_verdict"
            state_write_retry "$story_id" "1"
            if [[ "$retry_verdict" == "PASS" ]]; then
              log "Stage 6.retry — auto-fix PASS; continuing normal flow"
              review_verdict="PASS"
            else
              warn "Stage 6.retry — re-review still $retry_verdict; halting for AABIT"
            fi
          else
            warn "Stage 6.retry — re-review claude -p failed; halting"
            state_write_retry "$story_id" "1"
          fi
        fi
      else
        warn "Stage 6.retry — auto-fix claude -p failed; halting"
        state_write_retry "$story_id" "1"
      fi
    else
      log "Stage 6.retry — already retried (count=$retry_count); skipping auto-retry, halting"
    fi
  fi

  # Stage 6.fail (after optional auto-retry) ------------------------------
  if [[ "$review_verdict" != "PASS" && "$review_verdict" != "DRY-PASS" ]]; then
    retry_attempted="$(state_read_retry "$story_id")"
    printf 'stage=6 story=%s branch=%s reason=review-%s retry_attempted=%s ts=%s\n' \
      "$story_id" "$feature_branch" "$review_verdict" "$retry_attempted" "$(date -Is)" > "$HALT_FILE"
    warn "Stage 6.fail — verdict=$review_verdict (retry_attempted=$retry_attempted); halt state -> $HALT_FILE; branch preserved"
    exit 2
  fi

  # Stage 6.pass — merge to integration, delete feature, update sprint-status
  log "Stage 6.pass — merge $feature_branch into $INTEG_BRANCH; cleanup"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    # Patch D 2026-05-15 (Patch L 2026-05-15 update): commit review-artifact
    # dirty state BEFORE checkout. Original Patch D used `git add -A` with
    # blacklist exclusions — too greedy, sucked in unrelated untracked files
    # (Wave 0a Story 1.6 e6bb7d9 grabbed .serena/, _bmad/custom/, mockups/, ...).
    # Patch L: switch to WHITELIST — only known review-artifact paths get
    # staged. Untracked unrelated files stay untracked.
    if ! git diff --quiet || ! git diff --cached --quiet; then
      log "Stage 6.pass — committing review artifacts before merge (Patch D + L whitelist)"
      # Whitelist of paths review/dev stages legitimately touch.
      # `git add` silently ignores paths that don't exist — safe to enumerate.
      git add -- \
        "${STORIES_DIR}/${story_id}.md" \
        "${SPRINT_STATUS}" \
        "${IMPL_DIR}/deferred-work.md" \
        2>/dev/null || true
      # Glob patterns for files whose exact name is story-derived
      for glob in \
        "${IMPL_DIR}/extraction-log-*.md" \
        "${IMPL_DIR}/module-*-choice.md" \
        "${IMPL_DIR}/wave-*.md" \
        "${IMPL_DIR}/epic-*.md" \
        "_bmad/retrospectives/*.md" \
        ; do
        # shellcheck disable=SC2086
        git ls-files --others --modified --exclude-standard -- $glob 2>/dev/null | while IFS= read -r f; do
          [[ -n "$f" ]] && git add -- "$f" 2>/dev/null || true
        done
      done
      if git diff --cached --quiet; then
        log "Stage 6.pass — no staged changes after Patch L whitelist filter; proceeding"
      else
        git -c user.email="aabit@server.local" -c user.name="AABIT Server" \
          commit -m "docs(story-$story_id): review artifacts (auto)"
      fi
    fi
    # Patch F 2026-05-15: capture git status в halt-reason при checkout fail для diagnosis.
    if ! git checkout "$INTEG_BRANCH" 2>&1; then
      printf 'stage=6.pass story=%s branch=%s reason=checkout-fail ts=%s\n--- git status ---\n%s\n' \
        "$story_id" "$feature_branch" "$(date -Is)" "$(git status --short)" > "$HALT_FILE"
      fail "Stage 6.pass — checkout $INTEG_BRANCH failed; halt state -> $HALT_FILE"
    fi
    git merge --no-ff -m "merge story $story_id" "$feature_branch"
    stage7_cleanup_feature_branch "$feature_branch" "$INTEG_BRANCH"
    sprint_status_mark_done "$story_id"
    state_write "$story_id" "$INTEG_BRANCH"
  else
    log "Stage 6.pass — DRY: would merge + delete + update sprint-status for $story_id"
  fi

  # Stage 7 — batch gate --------------------------------------------------
  log "Stage 7 — batch gate check"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    gate_json="$(python3 "$BATCH_GATE" --check --last-story "$story_id" --batch-count "$iter")"
  else
    gate_json="$(python3 "$BATCH_GATE" --check)"
  fi
  echo "  $gate_json"
  boundary="$(echo "$gate_json" | python3 -c 'import json,sys;print("1" if json.load(sys.stdin).get("boundary") else "")')"
  reason="$(echo "$gate_json" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("reason",""))')"

  if [[ -n "$boundary" ]]; then
    summary_file="$CHECKPOINT_DIR/batch-$(date +%Y%m%d-%H%M%S).md"
    log "Stage 8 — checkpoint: $reason; summary -> $summary_file"
    if [[ "$DRY_RUN" -eq 0 ]]; then
      {
        echo "# Checkpoint — $(date -Is)"
        echo
        echo "- **reason:** $reason"
        echo "- **last story:** $story_id"
        echo "- **integration branch:** $INTEG_BRANCH"
        echo "- **gate output:** \`$gate_json\`"
        echo
        echo "## Batch contents"
        python3 -c "import json;d=json.load(open('$BATCH_FILE'));[print('-',s) for s in d.get('stories',[])]" || true
        echo
        echo "Next action (AABIT): review integration branch, then:"
        echo '```bash'
        echo "git checkout main && git merge --squash $INTEG_BRANCH && git commit"
        echo "git branch -d $INTEG_BRANCH"
        echo "bmad-auto-dev-runner.sh   # resume new batch"
        echo '```'
      } > "$summary_file"

      # Patch L 2026-05-15 (part 2): auto-invoke bmad-retrospective on wave_transition.
      # Reason: every wave (0a, 0b, 1a, 1b, 1c, 1d) deserves a retro per BMad
      # methodology. Phase 1 sequential — invoke synchronously here (not via
      # orchestrator). Phase 2 orchestrator can hoist this out.
      # Skips epic-* boundaries — those are manual via /bmad-retrospective.
      if [[ "$reason" == "wave_transition" ]]; then
        wave_from="$(echo "$gate_json" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("from",""))')"
        if [[ -n "$wave_from" ]]; then
          log "Stage 8.5 (Patch L) — auto-invoke retrospective for wave $wave_from"
          mkdir -p _bmad/retrospectives
          retro_out="_bmad/retrospectives/wave-${wave_from}.md"
          retro_log="$STATE_DIR/reviews/wave-${wave_from}-retro.log"
          # Use claude_with_api_retry so we get Patch G (API retry) + Patch H (timeout) free.
          if claude_with_api_retry "$retro_log" claude -p "Generate Wave $wave_from retrospective for BMad-auto-dev pipeline. \
Sources to read: \
  - ${SPRINT_STATUS} (find all stories with done status in Wave $wave_from per epics.md frontmatter) \
  - ${IMPL_DIR}/wave-${wave_from}-hours.md if exists \
  - $CHECKPOINT_DIR/batch-*.md (latest batch summary) \
  - $STATE_DIR/reviews/*.log (review verdicts per story) \
  - $SKILL_DIR/learnings.md (cumulative skill lessons) \
Output a focused retrospective (~150-250 lines Markdown): summary stats table, what worked, what did not work + fixes applied, skill patches landed, metrics + cost estimate, action items for next wave, what to keep doing. \
Save to $retro_out. No interactive party-mode dialogue — synthesize directly from artifacts. \
Working dir: $PROJECT_DIR."; then
            log "Stage 8.5 (Patch L) — retrospective written to $retro_out"
          else
            warn "Stage 8.5 (Patch L) — retrospective claude -p failed; will need manual /bmad-retrospective invocation"
          fi
        else
          warn "Stage 8.5 (Patch L) — wave_transition boundary но 'from' wave_id missing in gate output; skipping auto-retro"
        fi
      fi
    fi
    exit 3
  fi

  log "iteration $iter complete — continuing"
done

log "reached --max=$MAX_ITER; exiting cleanly"
exit 0
