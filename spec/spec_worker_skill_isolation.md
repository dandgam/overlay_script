---
name: spec_worker_skill_isolation
initiative: worker_skill_isolation
finding: NEW-27
status: done
created: 2026-05-20
completed: 2026-05-20
---

# Spec — Worker Skill Isolation (NEW-27)

## Problem

Virgil spawns workers (`claude -p`) in git worktrees created **inside** the
target project: `<target>/.worktrees/wt-<story>`. When a worker resolves a
slash command, `claude` walks **up the directory tree** from its CWD looking
for `.claude/skills/` — and reaches `<target>/.claude/skills/`, resolving a
skill that belongs to the *target project*, not to Virgil.

Replay 1.5 (NEW-26) proved this: the review worker ran the target's own
**interactive** `/bmad-code-review` instead of Virgil's headless one →
`verdict=error` every run. NEW-26 patched the code-review path (directive
prompt, no slash) but the **structural** cause is unfixed and other paths
remain exposed.

This violates a core invariant: **Virgil must use exclusively its own embedded
skills/agents — never the target project's.** Target skills can be any version,
interactive, broken, or absent (Virgil is project-agnostic).

## Exposed paths

| Path | Skill resolution | State |
|---|---|---|
| code-review spawn | directive prompt (NEW-26) | ✅ fixed |
| dev-worker `bmad-auto-dev` | explicit `bash .claude/skills/.../runner.sh` | ✅ explicit path |
| **security-review spawn** | slash `/bmad-security-review --auto` | ⚠️ exposed |
| nested `/skill` inside embedded skill bodies | slash walk-up | ⚠️ exposed |
| `~/.claude/skills/` via `isolated_home` overlay | copy of host home | ⚠️ uncontrolled |

`bmad-security-review` is **not** in `EMBEDDED_SKILL_NAMES` — the security
review worker depends on the host's `~/.claude/skills/bmad-security-review`,
which is not project-agnostic.

## Root cause & key facts (verified in code)

- `config.py:135` — `worktree_layout: Literal["sibling","nested"] = "sibling"`
  **exists but is not wired.** Code hardcodes the nested layout at three sites:
  `agent/run.py:618`, `agent/run.py:1265`, `agent/tools/_common.py:113`
  (`settings.target_project / ".worktrees"`).
- `runtime/sandbox.py:_resolve_target_dotgit` (Patch DD) already resolves a
  linked worktree's `.git` file regardless of layout depth — **relocating the
  worktree does not break git operations in the sandbox.**
- Sandbox does `--ro-bind / /` (whole FS readable). That is fine: skill
  discovery only walks **up from CWD**, it does not scan the FS. A worktree
  outside the target tree is never an ancestor-of nor ancestor-to
  `<target>/.claude/`, so the target's skills become undiscoverable.
- Any worktree under `/home/server/...` still walks up to `/home/server/.claude`
  (operator home). The only ancestor-clean roots are `/tmp` / `/var/tmp`.

## Goal

A worker's `claude` can discover skills from **exactly two** controlled
sources: (1) skills Virgil injected into `<worktree>/.claude/skills/`,
(2) the `isolated_home` overlay's `~/.claude/skills/`. Never the target
project, never the operator's host home.

## Sessions

### S1 — Relocate worktrees outside the target tree

- New `runtime/worktree.py` helper `worktree_root(settings)` returning the
  worktree root. Default: `/var/tmp/virgil-worktrees/<target-basename>/`
  (`/var/tmp`, not `/tmp` — persistent, not tmpfs; no `.claude` ancestor).
  Env override: `ORCHESTRATOR_WORKTREE_ROOT`.
- Replace the three hardcoded `target_project / ".worktrees"` sites with the
  helper. Per-story worktree = `<root>/wt-<story_id>`.
- Update `cleanup_worktree` callers to pass the new root; the containment
  guard still applies.
- `git worktree add <abs-path> <branch>` works with any absolute path — verify
  the worktree's `.git` file `gitdir:` still resolves (Patch DD path).
- Verify the bwrap sandbox binds: worktree rw, target `.git` ro (via
  `_resolve_target_dotgit`), `_bmad-output/runs/` writable (events.jsonl).
- Tests: worktree root resolution, env override, a worktree path that has no
  `.claude` ancestor; sandbox bind set includes the resolved gitdir.

### S2 — security-review → directive prompt

- Mirror NEW-26: replace `SECURITY_REVIEW_SKILL_INVOCATION =
  "/bmad-security-review --auto"` with `SECURITY_REVIEW_DIRECTIVE` — a
  self-contained headless STRIDE/hunter brief (Injection / Auth Bypass /
  Crypto / Data-leak angles) ending with a parseable verdict line.
- Keep `parse_security_verdict_from_event` working against the directive's
  output shape.
- Tests: directive is not a slash command, forbids halting, mandates a
  verdict line that `parse_security_verdict_from_event` accepts.

### S3 — `isolated_home` skill hygiene

- The `isolated_home` overlay currently snapshots the host `~/.claude/`,
  carrying whatever skills the operator has installed.
- Make the overlay's `~/.claude/skills/` deterministic: either strip it (the
  worktree-injected project skills are enough) or populate it from Virgil's
  embedded set only. Decide in-session; prefer strip if no worker relies on a
  user-level skill.
- Tests: overlay `~/.claude/skills/` contains no skill outside Virgil's
  embedded set.

### S4 — Validation replay

- Recreate the wt-1.5 fixture (`git worktree add` from `feature/1.5`) at the
  **new** root and run `replay --worktree <new>/wt-1.5 --story 1.5
  --integration integration/1a --project antares`.
- Assert: code-review + security-review both yield a real verdict (no
  `runner_log_fallback`, no synthetic verdict); worker logs show no skill
  resolved from `<target>/.claude/skills/`.

## Non-goals

- Rewriting the embedded skills themselves.
- Changing the dev-worker invocation (already explicit-path safe).
- Sandbox network / HOME-writability changes (NEW-21/24 already settled).

## Acceptance

1. `worktree_layout` honored; worktrees created under `/var/tmp/virgil-
   worktrees/` (or env override) — outside every project tree.
2. security-review spawn uses a directive prompt, not a slash command.
3. `isolated_home` overlay carries only Virgil-controlled skills.
4. Replay 1.5 — both review gates produce real verdicts; no target-skill
   resolution in any worker log.
5. Full test suite green; new tests per session.
