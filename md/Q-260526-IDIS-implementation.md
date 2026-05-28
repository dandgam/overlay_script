# Q-260526-IDIS implementation — manual application to `~/.claude/skills/888/`

**Reason for fallback:** batch worktree at `/tmp/888-bat-batch-…/Q-260526-IDIS/`
is isolated from `~/.claude/skills/888/` and `~/.claude/settings.json`
(both live in user-home, outside the worktree). Per batch rule #7,
worker cannot edit files outside the worktree. Apply payload below
manually from main session.

**Status:** `staged` · `phase:2.5` · `priority:cosmetic` · `effort:~10 min`
**Type:** enhancement · **Security:** non-critical
**Parent:** Q-260526-BATCH · **Source:** Q-260526-ISOL improver F2 §4dv observation

---

## 1. Goal

Wire `scripts/session-isolation.sh` into a non-blocking PostToolUse:Skill
hook so that after **every** persona handoff the dispatcher's advisory
recommendation is auto-logged to `audit/session-isolation.log` — without
requiring the dispatcher to remember to consult manually.

Inverts responsibility (R4: hook does the consultation, prompt does not).
Never blocks chain; failures are silently swallowed (best-effort).

## 2. Files to create

### 2.1 `~/.claude/skills/888/hooks/posttooluse-session-isolation-advisory.sh`

```bash
#!/usr/bin/env bash
# posttooluse-session-isolation-advisory.sh
# Q-260526-IDIS — Auto-consultation of session-isolation.sh (NON-BLOCKING)
#
# Triggered:  PostToolUse:Skill (after any persona handoff via Skill tool)
# Purpose:    Log advisory `fresh-claude-p` vs `current-session` recommendation
#             to audit/session-isolation.log without forcing dispatcher action.
# Contract:   ALWAYS exit 0. NEVER block. NEVER print to stderr on the happy path.
#
# Spec reference: methodology-888.md §4dv (Q-260526-ISOL implementer L5 debt)
#                 + §5 active Q-260526-IDIS

set -u  # deliberately NOT -e: best-effort, swallow all failures

SKILL_DIR="$HOME/.claude/skills/888"
ISO_SCRIPT="$SKILL_DIR/scripts/session-isolation.sh"
PHASE_TRACKER="$SKILL_DIR/PHASE-TRACKER.md"

# Guard 1: script must be present + executable
[ -x "$ISO_SCRIPT" ] || exit 0

# Guard 2: best-effort tier detection from PHASE-TRACKER.md top entry.
#          Format expected: `tier: <X>` on a frontmatter or table line.
#          If unparseable → safe default `M`.
tier="M"
if [ -r "$PHASE_TRACKER" ]; then
  detected=$(grep -m1 -oE 'tier:[[:space:]]*[XSML]+' "$PHASE_TRACKER" 2>/dev/null \
             | head -1 \
             | awk -F: '{print $2}' \
             | tr -d '[:space:]')
  case "$detected" in
    XS|S|M|L|XL) tier="$detected" ;;
  esac
fi

# Run advisory in --apply mode (logs to audit, exits 0 even on degraded paths
# per session-isolation.sh exit-code contract: 0=ok, 2=lock, 3=config-missing, 5=misuse).
# We swallow exit≠0 because this hook is advisory only.
bash "$ISO_SCRIPT" "$tier" --apply >/dev/null 2>&1 || true

exit 0
```

**chmod:** `chmod +x ~/.claude/skills/888/hooks/posttooluse-session-isolation-advisory.sh`

> Note: the `hooks/` subdirectory under `~/.claude/skills/888/` may not yet
> exist. Create it first with `mkdir -p ~/.claude/skills/888/hooks` before
> writing the script.

## 3. `~/.claude/settings.json` patch

Find the existing `PostToolUse` array entry whose `matcher` is `"Skill"`
(currently holds the single `enforce-post-persona-check.sh` hook).
**Append** one entry to its `hooks` array:

```json
{
  "type": "command",
  "command": "bash \"$HOME/.claude/skills/888/hooks/posttooluse-session-isolation-advisory.sh\"",
  "timeout": 5
}
```

Resulting `Skill` matcher block (illustrative — preserve existing first hook):

```json
{
  "matcher": "Skill",
  "hooks": [
    {
      "type": "command",
      "command": "bash \"$HOME/.claude/skills/888/scripts/enforce-post-persona-check.sh\"",
      "timeout": 5
    },
    {
      "type": "command",
      "command": "bash \"$HOME/.claude/skills/888/hooks/posttooluse-session-isolation-advisory.sh\"",
      "timeout": 5
    }
  ]
}
```

**Validate after edit:** `python3 -c "import json; json.load(open('$HOME/.claude/settings.json'))"`
must exit 0 (JSON parseable). Test once manually with any Skill invocation
and confirm `~/.claude/skills/888/audit/session-isolation.log` grew by one line.

## 4. `~/.claude/skills/888/SKILL.md` annotation

Locate the existing **§2 step 7-bis-isol** block (currently lines ~465–469
of `SKILL.md`). **Append** the following paragraph **at the end of that block**,
immediately before the blank line preceding `7-ter`:

```
**Auto-consultation (Q-260526-IDIS):** PostToolUse:Skill hook
`hooks/posttooluse-session-isolation-advisory.sh` авто-вызывается после
каждого `Skill` invoke, читает tier из `PHASE-TRACKER.md` (fallback `M`),
выполняет `session-isolation.sh <tier> --apply` (логирует в
`audit/session-isolation.log`). Hook всегда exit 0 — advisory-only, не
блокирует цепочку. Disable: удалить hook entry из `~/.claude/settings.json`
PostToolUse:Skill matcher.
```

## 5. methodology-888.md §5 active — bucket move

After applying steps 2-4, edit the Q-260526-IDIS row at
`~/.claude/skills/888/methodology-888.md` (~line 7433) and flip
`bucket:active` → `bucket:done` plus `status:open · parked` →
`status:done · wired via PostToolUse:Skill hook`. Append `attachment:`
field pointing to this fallback-md and the new hook script path.

## 6. Verification checklist

- [ ] `~/.claude/skills/888/hooks/posttooluse-session-isolation-advisory.sh` exists, is executable, `bash -n` clean.
- [ ] `~/.claude/settings.json` parses as valid JSON after edit.
- [ ] Manual smoke: invoke any Skill, then `tail -1 ~/.claude/skills/888/audit/session-isolation.log` shows a new entry.
- [ ] Negative test: `chmod -x` the hook script → next Skill invoke does NOT break chain (hook missing → other matchers still run).
- [ ] SKILL.md §2 step 7-bis-isol contains the new "Auto-consultation (Q-260526-IDIS)" paragraph.
- [ ] methodology §5 IDIS row moved to `bucket:done`.

## 7. Rollback

Remove the hook entry from `settings.json` PostToolUse:Skill matcher.
Delete the script file. Revert SKILL.md paragraph. methodology row →
`bucket:active`, `status:open · reverted YYYY-MM-DD`. No data migration needed
(`audit/session-isolation.log` is append-only plain text; leaving stale
entries is harmless).

## 8. Why this is P3 cosmetic (justification trail)

- Advisory only: never gates persona handoff, never blocks chain.
- Failure modes are all silent + best-effort (script missing, config missing, lock contention all return exit 0 from the wrapper).
- Adds at most 5 s to PostToolUse:Skill latency; typical run < 100 ms.
- Inverts R4 responsibility per spec_batch_composition_v1.md §4 Family 4 design intent.
- No new dependencies; reuses existing `session-isolation.sh --apply` path
  that was implementation-tested but production-unwired (L5 debt from §4dv).
