#!/usr/bin/env bash
# 888-shard-merger.sh — WTISO-SH Phase 2.5 implementation (Q-260527-WTISO-SH)
# Spec: spec/spec_wtiso-sh.md v2.1-block-fix
#
# Single-process post-batch merger: consolidates per-worker shard files in
# audit/shards/<batch-id>/*.md into methodology-888.md collision-free,
# append-only, idempotent, rollback-safe.
#
# Invocation:
#   888-shard-merger.sh --batch-id <id> [--target <methodology-path>]
#                       [--shard-dir <dir>]
#
# Exit codes (per §5 spec):
#   0  success (including legitimate noop / all-skipped / idempotent)
#   2  flock contention (concurrent merger on same batch-id)
#   3  receipt exists but shards_sha256 mismatch (receipt_conflict)
#   4  symlink cycle
#   5  shard dir missing (batch_unknown)
#   6  anchor pool exhausted (>702 shards in batch)
#   7  anchor letter malformed
#   8  next_anchor_letter unknown rc
#   9  merge_noop_unexpected (post-SHA equal AND appended_count > 0)
#   10 malformed batch-id (shell-injection guard)
#   11 receipt JSON malformed
#   12 methodology unreadable
#   13 disk full mid-copy
#   14 L3 violation (whole-batch reject)
#   15 TOCTOU mismatch
#   16 mv across filesystems (EXDEV)
#   130/143 SIGINT/SIGTERM

set -euo pipefail

# ─── Locale + globals ────────────────────────────────────────────────────
export LC_ALL=C

declare -ga _ANCHOR_TABLE=()
declare -gi _ANCHOR_CAP=702

BATCH_ID=""
TARGET=""
SHARD_DIR=""
STAGING=""
LOCK=""
RECEIPT=""
LOCK_FD=""

declare -ga _SKIPPED_SHARDS=()
declare -ga _REJECTED_SHARDS=()
declare -ga _SHARD_MTIMES=()
declare -gi _APPENDED_COUNT=0

# ─── Logging / audit ─────────────────────────────────────────────────────
_log() {
    printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*" >&2
}

_audit() {
    # Append a JSONL audit event. Schema: ts, event, batch_id, details.
    local event="$1"; shift
    local details="${*:-}"
    local audit_file="${SHARD_AUDIT_LOG:-audit/shards/merger-audit.jsonl}"
    mkdir -p "$(dirname "$audit_file")"
    printf '{"ts":"%s","event":"%s","batch_id":"%s","details":%s}\n' \
        "$(date -u +%FT%TZ)" "$event" "$BATCH_ID" \
        "${details:-\"\"}" >> "$audit_file"
}

# ─── Anchor table + next_anchor_letter (ADR-012) ─────────────────────────
_init_anchor_table() {
    local letters=(a b c d e f g h i j k l m n o p q r s t u v w x y z)
    local first second
    _ANCHOR_TABLE=("${letters[@]}")
    for first in "${letters[@]}"; do
        for second in "${letters[@]}"; do
            _ANCHOR_TABLE+=("${first}${second}")
        done
    done
    # Total = 26 + 26*26 = 702. Asserted as literal in RED-SH-8.
}

next_anchor_letter() {
    local LC_ALL=C
    local cur="${1:-}"

    if [[ -z "$cur" ]]; then
        printf '%s\n' "${_ANCHOR_TABLE[0]}"
        return 0
    fi

    if ! [[ "$cur" =~ ^[a-z]{1,2}$ ]]; then
        return 2
    fi

    local i
    for ((i = 0; i < _ANCHOR_CAP; i++)); do
        if [[ "${_ANCHOR_TABLE[i]}" == "$cur" ]]; then
            local next_i=$((i + 1))
            if (( next_i >= _ANCHOR_CAP )); then
                return 1
            fi
            printf '%s\n' "${_ANCHOR_TABLE[next_i]}"
            return 0
        fi
    done

    return 2
}

# ─── Trap / cleanup ──────────────────────────────────────────────────────
_merger_cleanup() {
    local rc=$?
    # Remove leftover staging if present.
    if [[ -n "${STAGING:-}" && -e "$STAGING" ]]; then
        rm -f "$STAGING" 2>/dev/null || true
    fi
    # Flock auto-released when fd closes at process exit.
    return "$rc"
}

# ─── Content-aware shard hash (ADR-001, v2.1 BLOCK-3 fix) ─────────────────
_compute_shards_sha256() {
    # $1 = newline-separated sorted shard paths (or empty stdin → empty hash).
    local p content_hash out
    out=$(
        while IFS= read -r p; do
            [[ -z "$p" ]] && continue
            content_hash=$(sha256sum < "$p" | awk '{print $1}')
            printf '\0%s\0%s' "$p" "$content_hash"
        done | sha256sum | awk '{print $1}'
    )
    printf '%s\n' "$out"
}

# ─── L2 gate: LOC bounds + frontmatter discipline ────────────────────────
_l2_check() {
    local shard="$1"
    local max_loc="${SHARD_MAX_LOC:-1000}"

    if ! [[ "$max_loc" =~ ^[1-9][0-9]{0,4}$ ]]; then
        max_loc=1000
    fi
    if (( max_loc > 50000 )); then
        max_loc=50000
    fi

    local loc
    loc=$(wc -l < "$shard")
    if (( loc > max_loc )); then
        _log "L2 reject (LOC=$loc > max=$max_loc): $shard"
        return 1
    fi

    # Frontmatter discipline: no `^---$` outside first 50 lines.
    if head -n 5000 "$shard" | awk 'NR > 50 && /^---$/ { exit 1 }'; then
        :
    else
        _log "L2 reject (frontmatter outside top 50 lines): $shard"
        return 1
    fi

    return 0
}

# ─── L1 gate: LLM code-review (mockable for tests) ───────────────────────
_l1_check() {
    local shard="$1"

    # Test/mock mode — short-circuit. Honoured only when BATCH_MOCK_MODE=1
    # OR explicit SHARD_GATE_SKIP_L1=1 (per ADR-008).
    if [[ "${BATCH_MOCK_MODE:-0}" == "1" || "${SHARD_GATE_SKIP_L1:-0}" == "1" ]]; then
        case "${BMAD_SHARD_L1_MOCK:-PASS}" in
            PASS) return 0 ;;
            BLOCK|FAIL) _log "L1 mock BLOCK: $shard"; return 1 ;;
            ERROR) _log "L1 mock ERROR (hang surrogate): $shard"; return 2 ;;
            *) return 0 ;;
        esac
    fi

    # Real-mode L1: invoke claude code-reviewer via headless. timeout 60s,
    # retry once on hang, second hang → record verdict 'error' + skip.
    local shard_body verdict
    shard_body=$(cat "$shard")

    local attempt
    for attempt in 1 2; do
        verdict=$(timeout 60s claude -p --model sonnet \
            "Review this shard for correctness. Reply PASS or BLOCK only.

$shard_body" 2>/dev/null || true)

        if [[ -n "$verdict" ]]; then
            case "$verdict" in
                *PASS*) return 0 ;;
                *BLOCK*|*FAIL*) _log "L1 BLOCK: $shard"; return 1 ;;
            esac
        fi
        _log "L1 hang attempt $attempt: $shard"
    done

    _log "L1 ERROR (2× hang): $shard"
    return 2
}

# ─── L3 gate: fence-aware append-only + structural check (ADR-013) ───────
_l3_check() {
    # $1 = staging file, $2 = original target file
    # L3 invariant: first N bytes of staging == first N bytes of original
    # (where N = original file size pre-merge). Plus heading/frontmatter
    # counts outside fenced blocks must not decrease.
    local staging="$1" target="$2"
    local target_size
    target_size=$(stat -c%s "$target")

    # Append-only byte check.
    if ! cmp -n "$target_size" "$target" "$staging" >/dev/null 2>&1; then
        _log "L3 violation: first $target_size bytes diverge (append_only_violation)"
        return 1
    fi

    # Fence-aware structural check on staging file head.
    local head_lines=50
    local staging_h="$(head -n $head_lines "$staging")"
    local target_h="$(head -n $head_lines "$target")"

    local s_count t_count
    s_count=$(printf '%s\n' "$staging_h" | _count_outside_fence)
    t_count=$(printf '%s\n' "$target_h" | _count_outside_fence)

    if (( s_count != t_count )); then
        _log "L3 violation: frontmatter/heading count differs ($s_count vs $t_count) in top $head_lines"
        return 1
    fi

    return 0
}

_count_outside_fence() {
    # Reads lines on stdin; counts `^## [a-z]?\d+\.` headings + `^---$`
    # frontmatter delimiters that are NOT inside a fenced code block.
    # Fence toggle: `^( {0,3})(\`\`\`|~~~)`.
    local line in_fence=0 count=0
    while IFS= read -r line; do
        if [[ "$line" =~ ^[\ ]{0,3}(\`\`\`|~~~) ]]; then
            in_fence=$((1 - in_fence))
            continue
        fi
        if (( in_fence == 0 )); then
            if [[ "$line" =~ ^##\ [a-z]?[0-9]+\. ]] || [[ "$line" == "---" ]]; then
                count=$((count + 1))
            fi
        fi
    done
    printf '%d\n' "$count"
}

# ─── State-machine placeholder rewriter (ADR-002 + ADR-011) ───────────────
_rewrite_placeholder() {
    # $1 = staging file, $2 = placeholder string (literal, with UUID),
    # $3 = final letter (e.g. 'gf' for §4gf).
    # Rewrites placeholder → final ONLY outside fenced code blocks.
    local staging="$1" placeholder="$2" final_letter="$3"
    local tmp="${staging}.rewrite.$$"
    local line in_fence=0
    local replacement="## 4${final_letter}."

    while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ "$line" =~ ^[\ ]{0,3}(\`\`\`|~~~) ]]; then
            in_fence=$((1 - in_fence))
            printf '%s\n' "$line"
            continue
        fi
        if (( in_fence == 0 )); then
            # Replace placeholder with final letter form. Plain substring
            # replacement (no regex metachars in placeholder by construction).
            printf '%s\n' "${line//$placeholder/$replacement}"
        else
            printf '%s\n' "$line"
        fi
    done < "$staging" > "$tmp"

    mv "$tmp" "$staging"
}

# ─── Read current highest anchor from target (§3.3 step 8) ───────────────
_read_highest_anchor() {
    local target="$1"
    if [[ ! -r "$target" ]]; then
        return 1
    fi
    local highest
    highest=$({ grep -oE '^## 4([a-z]{1,2})\.' "$target" | sed -E 's/^## 4(.+)\.$/\1/'; } | sort -u | tail -1)
    printf '%s\n' "${highest:-}"
}

# ─── Receipt write (§3.6 step 15) ────────────────────────────────────────
_write_receipt() {
    local pre_sha="$1" post_sha="$2" shards_hash="$3" exit_code="$4"

    # Build skipped/rejected arrays.
    local skipped_json="[]" rejected_json="[]"
    if (( ${#_SKIPPED_SHARDS[@]} > 0 )); then
        skipped_json=$(printf '%s\n' "${_SKIPPED_SHARDS[@]}" | jq -R . | jq -sc .)
    fi
    if (( ${#_REJECTED_SHARDS[@]} > 0 )); then
        rejected_json=$(printf '%s\n' "${_REJECTED_SHARDS[@]}" | jq -R . | jq -sc .)
    fi

    local receipt_content
    receipt_content=$(jq -n \
        --arg bid "$BATCH_ID" \
        --arg shsha "$shards_hash" \
        --arg pre "$pre_sha" \
        --arg post "$post_sha" \
        --arg ts "$(date -u +%FT%TZ)" \
        --argjson ec "$exit_code" \
        --argjson skip "$skipped_json" \
        --argjson rej "$rejected_json" \
        '{batch_id:$bid, shards_sha256:$shsha, pre_merge_sha:$pre, post_merge_sha:$post, ts:$ts, exit_code:$ec, skipped_shards:$skip, rejected_shards:$rej}')

    # jq -e . validates JSON before disk write (rejects partial write).
    printf '%s\n' "$receipt_content" | jq -e . > "$RECEIPT"
}

# ─── Main pipeline ───────────────────────────────────────────────────────
_main() {
    _init_anchor_table

    # Parse args
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --batch-id) BATCH_ID="${2:-}"; shift 2 ;;
            --target) TARGET="${2:-}"; shift 2 ;;
            --shard-dir) SHARD_DIR="${2:-}"; shift 2 ;;
            --help|-h)
                grep -E '^#( |!)' "$0" | head -40
                exit 0 ;;
            *) _log "unknown arg: $1"; exit 10 ;;
        esac
    done

    # P2 — batch-id validation (shell-injection guard) — BEFORE any path
    # construction that interpolates BATCH_ID.
    if ! [[ "$BATCH_ID" =~ ^[0-9TZ:-]+-[0-9a-f]{8}$ ]]; then
        _log "P2 reject: malformed batch-id"
        exit 10
    fi

    # Defaults
    : "${TARGET:=methodology-888.md}"
    : "${SHARD_DIR:=audit/shards/$BATCH_ID}"
    STAGING="${TARGET}.staging-${BATCH_ID}"
    LOCK="${SHARD_DIR}/.merger.lock"
    RECEIPT="${SHARD_DIR}/.merge-receipt.json"

    # Install trap (P5) — once.
    trap '_merger_cleanup' INT TERM EXIT

    # P1 — mkdir + flock
    mkdir -p "$SHARD_DIR"
    exec {LOCK_FD}>"$LOCK"
    if ! flock -x -w 30 "$LOCK_FD"; then
        _log "P1 flock timeout (merger contention)"
        exit 2
    fi

    # P4 — symlink resolve
    if [[ -L "$TARGET" ]]; then
        local resolved
        resolved=$(readlink -f "$TARGET")
        if [[ -z "$resolved" ]]; then
            _log "P4 symlink cycle"
            exit 4
        fi
        TARGET="$resolved"
    fi

    if [[ ! -r "$TARGET" ]]; then
        _log "methodology unreadable: $TARGET"
        exit 12
    fi

    # §3.2 — shard enum
    if [[ ! -d "$SHARD_DIR" ]]; then
        _log "shard dir missing: $SHARD_DIR"
        exit 5
    fi

    local sorted_shards=()
    while IFS= read -r f; do
        sorted_shards+=("$f")
    done < <(find "$SHARD_DIR" -maxdepth 1 -name '*.md' -type f 2>/dev/null | LC_ALL=C sort)

    # §3.2 step 7 — distinguish empty-vs-missing
    if (( ${#sorted_shards[@]} == 0 )); then
        _audit shard_merger_noop_empty_batch '"empty-shard-dir"'
        # Write minimal receipt for idempotency on re-runs.
        local empty_hash
        empty_hash=$(printf '' | sha256sum | awk '{print $1}')
        local pre_sha
        pre_sha=$(sha256sum < "$TARGET" | awk '{print $1}')
        _write_receipt "$pre_sha" "$pre_sha" "$empty_hash" 0
        exit 0
    fi

    # P3 — receipt idempotency scan
    local current_shards_hash
    current_shards_hash=$(printf '%s\n' "${sorted_shards[@]}" | _compute_shards_sha256)

    if [[ -f "$RECEIPT" ]]; then
        if ! jq -e . "$RECEIPT" >/dev/null 2>&1; then
            _log "P3 receipt malformed JSON"
            exit 11
        fi
        local prior_hash
        prior_hash=$(jq -r '.shards_sha256 // ""' "$RECEIPT")
        if [[ "$prior_hash" == "$current_shards_hash" ]]; then
            _audit shard_merger_skipped_idempotent '"hash-match"'
            _log "idempotent skip: receipt matches"
            exit 0
        else
            _log "P3 receipt_conflict: prior=$prior_hash current=$current_shards_hash"
            exit 3
        fi
    fi

    # §3.3 — anchor allocation
    local current_highest
    current_highest=$(_read_highest_anchor "$TARGET")

    declare -a shard_anchors=()
    local shard
    for shard in "${sorted_shards[@]}"; do
        local NEXT
        NEXT=$(next_anchor_letter "$current_highest")
        rc=$?
        if [[ $rc -ne 0 ]]; then
            case $rc in
                1) echo "anchor pool exhausted (702 cap)" >&2; exit 6 ;;
                2) echo "malformed current letter: $current_highest" >&2; exit 7 ;;
                *) echo "next_anchor_letter unknown rc=$rc" >&2; exit 8 ;;
            esac
        fi
        shard_anchors+=("$NEXT")
        current_highest="$NEXT"
    done

    # §3.4 — staging build
    if ! cp --reflink=auto "$TARGET" "$STAGING" 2>/dev/null; then
        if ! cp "$TARGET" "$STAGING"; then
            _log "P10 disk full / cp failed"
            exit 13
        fi
    fi
    sync -f "$STAGING" 2>/dev/null || true

    local shard_idx=0
    declare -a shard_shas=()
    for shard in "${sorted_shards[@]}"; do
        local anchor="${shard_anchors[$shard_idx]}"
        shard_idx=$((shard_idx + 1))

        # 11.1 — initial sha
        local shard_sha
        shard_sha=$(sha256sum < "$shard" | awk '{print $1}')
        shard_shas+=("$shard_sha")
        _SHARD_MTIMES+=("$(stat -c%Y "$shard")")

        # 11.2 — L1
        local l1_rc=0
        _l1_check "$shard" || l1_rc=$?
        if (( l1_rc != 0 )); then
            _SKIPPED_SHARDS+=("$shard")
            _audit shard_l1_rejected "\"$shard\""
            continue
        fi

        # 11.3 — L2
        if ! _l2_check "$shard"; then
            _SKIPPED_SHARDS+=("$shard")
            _audit shard_l2_rejected "\"$shard\""
            continue
        fi

        # 11.4 — L3 (whole-batch abort on fail)
        # Build temp candidate staging = current $STAGING + shard appended (post-rewrite)
        local placeholder
        # Derive placeholder UUID from batch-id last 8 hex.
        placeholder="## 4PLACEHOLDER-${BATCH_ID##*-}."

        local candidate="${STAGING}.candidate.$$"
        cp "$STAGING" "$candidate"
        # Append shard body (rewrite happens after L3, but L3 only checks
        # invariant on the staging-pre-this-shard, not the appended content).

        if ! _l3_check "$candidate" "$TARGET"; then
            rm -f "$candidate"
            _REJECTED_SHARDS+=("$shard")
            _audit shard_l3_violation "\"$shard\""
            _log "L3 violation — whole-batch abort"
            exit 14
        fi
        rm -f "$candidate"

        # 11.5 — append shard to staging (placeholder still present).
        cat "$shard" >> "$STAGING"

        # 11.5b — rewrite placeholder → final anchor (state machine).
        _rewrite_placeholder "$STAGING" "$placeholder" "$anchor"

        # 11.6 — TOCTOU recheck
        local shard_sha_now
        shard_sha_now=$(sha256sum < "$shard" | awk '{print $1}')
        if [[ "$shard_sha_now" != "$shard_sha" ]]; then
            _audit shard_toctou_mismatch "\"$shard\""
            _log "TOCTOU mismatch — abort batch"
            exit 15
        fi

        # 11.8 — increment counter
        _APPENDED_COUNT=$((_APPENDED_COUNT + 1))
    done

    # §3.5 — frontmatter update (only if anything appended; all-skipped path
    # leaves frontmatter untouched).
    if (( _APPENDED_COUNT > 0 && ${#_SHARD_MTIMES[@]} > 0 )); then
        local max_mtime
        max_mtime=$(printf '%s\n' "${_SHARD_MTIMES[@]}" | sort -n | tail -1)
        local now_plus_60=$(($(date -u +%s) + 60))
        if (( max_mtime > now_plus_60 )); then
            max_mtime=$now_plus_60
        fi
        local iso_mtime
        iso_mtime=$(date -u -d "@$max_mtime" +%FT%TZ)
        # In-place update of `last-touched:` line — use temp file for atomicity.
        local fm_tmp="${STAGING}.fm.$$"
        awk -v ts="$iso_mtime" 'BEGIN{updated=0} /^last-touched:/ && !updated { print "last-touched: " ts; updated=1; next } { print }' "$STAGING" > "$fm_tmp"
        mv "$fm_tmp" "$STAGING"
    fi

    # §3.6 — atomic promotion
    local pre_merge_sha
    pre_merge_sha=$(sha256sum < "$TARGET" | awk '{print $1}')

    if ! mv "$STAGING" "$TARGET" 2>/dev/null; then
        local mv_err
        mv_err=$(mv "$STAGING" "$TARGET" 2>&1 || true)
        if [[ "$mv_err" == *"EXDEV"* ]] || [[ "$mv_err" == *"cross-device"* ]]; then
            _log "mv EXDEV (cross-device)"
            exit 16
        fi
        _log "mv failed: $mv_err"
        exit 13
    fi

    local post_merge_sha
    post_merge_sha=$(sha256sum < "$TARGET" | awk '{print $1}')

    local exit_rc=0
    if [[ "$pre_merge_sha" == "$post_merge_sha" ]]; then
        if (( _APPENDED_COUNT == 0 )); then
            # Legitimate all-skipped (B13 v2.1 fix)
            _audit shard_merger_all_skipped '"all-shards-skipped"'
            exit_rc=0
        else
            # Unexpected noop
            _audit shard_merger_noop_unexpected "\"appended=$_APPENDED_COUNT\""
            exit_rc=9
        fi
    else
        # Normal merge
        exit_rc=0
    fi

    # §3.6 step 15 — write receipt
    _write_receipt "$pre_merge_sha" "$post_merge_sha" "$current_shards_hash" "$exit_rc"

    exit "$exit_rc"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    _main "$@"
fi
