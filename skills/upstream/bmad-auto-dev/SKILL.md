---
name: bmad-auto-dev
description: Autonomous BMad story pipeline. Orchestrates batches of stories through create-story → Gauntlet → dev-story → code-review with feature-branch isolation, halt-on-fail, and human checkpoints every 10 stories or on Wave-boundary. Auto-detects BMad project via _bmad/ directory. Use when user says "run auto-dev", "start bmad pipeline", "/bmad-auto-dev", or wants to process a batch of stories autonomously.
status: phase-1-ready (Phase 2 worktree parallelism deferred)
phase: Phase 1 — A+C+D (parallelism B deferred)
created: 2026-05-14
audience: all BMad projects (user-level skill, installed to ~/.claude/skills/ in S7)
---

# /bmad-auto-dev — Autonomous BMad story pipeline

> **What:** orchestrates batches of stories through the BMad cycle (create-story → Gauntlet → dev-story → code-review) with feature-branch isolation, halt-on-fail, and human checkpoint every 10 stories or on Wave-boundary.
>
> **Who:** AABIT (solo founder, non-developer) — needs autonomous processing of 326 Odyssey stories with minimal human input on strategic checkpoints. Applies to any BMad project via `_bmad/` auto-detection.

## Invocation

```text
/bmad-auto-dev              # auto-detect project, propose next ready story
/bmad-auto-dev --dry-run    # show what would happen without code generation
/bmad-auto-dev --resume     # continue from halt state (clears halt-reason.txt)
/bmad-auto-dev --max N      # cap iterations to N (debugging / testing)
```

Underneath, the skill invokes `scripts/bmad-auto-dev-runner.sh` with the same flags. The runner is the primary executable; the skill markdown is the docs + entry point.

## Exit code contract

The runner emits structured exit codes so wrappers (cron, systemd, /loop) can distinguish outcomes without log scraping:

| Code | Meaning | Wrapper action |
|------|---------|----------------|
| 0 | Success (all `--max` iterations done OR `all_done`) | continue / mark batch done |
| 1 | Pre-flight failure (dirty tree, missing artifacts, parse error) | halt — AABIT must fix |
| 2 | Halt-on-fail (review FAIL or `claude -p` non-zero) | halt — AABIT inspects feature branch + `halt-reason.txt` |
| 3 | Halt-on-checkpoint (batch boundary reached — N=10, wave-transition, or gate story) | normal pause — AABIT reviews summary + approves squash-merge |
| 4 | Internal error (unknown CLI arg, helper script crash, malformed state) | inspect runner log; usually a bug |

## File structure

```
~/.claude/skills/bmad-auto-dev/                  # installed user-level (after S7)
├── SKILL.md                                     # this file (entry point + docs)
├── customize.toml                               # project-level overrides
├── scripts/
│   ├── dependency_analyzer.py                   # next ready story (--next, --validate, --wave)
│   ├── gauntlet_injector.py                     # 5-lens elicitation (--mode quick|deep|auto)
│   ├── batch_gate.py                            # batch boundary (--check)
│   └── bmad-auto-dev-runner.sh                  # per-story orchestrator (bash)
├── templates/
│   ├── gauntlet-prompts.md                      # 5 elicitation prompts (Failure Mode / Edge Case / Pre-mortem / Devil's Advocate / Security Red Team)
│   └── checkpoint-summary.md                    # batch summary template
├── learnings.md                                 # self-learning notes (post-batch retrospectives)
└── README.md                                    # install + usage + troubleshooting

# Per BMad-project (must exist):
<project>/_bmad/planning-artifacts/epics.md
<project>/_bmad/implementation-artifacts/sprint-status.yaml

# Created by skill per run:
<project>/_bmad/stories/<id>.md                  # populated by Stage 4 create-story
<project>/_bmad/auto-dev-state/
├── state.json                                   # current batch progress (story list + last_story_id)
├── halt-reason.txt                              # written on Stage 6.fail; deleted by --resume
├── gauntlet/<id>/prompts.json                   # Gauntlet --dry-run output (audit trail)
└── checkpoint-log/<batch-N>.md                  # AABIT review notes per batch
```

## 9-Stage workflow (Phase 1 sequential)

### Stage 0 — Pre-flight

- Detect project via `_bmad/` directory presence; abort with exit 1 if missing.
- Verify required artifacts: `_bmad/planning-artifacts/epics.md`, `_bmad/implementation-artifacts/sprint-status.yaml`. Abort exit 1 if missing.
- Verify `git status` is clean; abort exit 1 if dirty (AABIT must commit/stash first).
- Load `<project>/.bmad/customize.toml` (optional) — overrides defaults from this skill's `customize.toml`.
- Read `Metadata` from `_bmad/auto-dev-state/state.json` if it exists (resume path).
- If `halt-reason.txt` exists AND `--resume` not passed → dump halt contents and exit 2.
- If `--resume` passed → `rm halt-reason.txt` (or log "DRY: would clear" in dry-run).

### Stage 1 — Select next ready story

- Invoke `python3 scripts/dependency_analyzer.py --next`.
- Returns `{"story_id": "1.1", "wave": "0a", "deps_met": true}` for ready stories.
- Returns `{"all_done": true}` when no ready story remains → runner exits 0.
- Deferred deps (e.g., Story 0.0) are treated as satisfied — does not block dependents.

### Stage 2 — Branch creation

- `git checkout -b feature/story-<id>` from current integration branch.
- On collision (branch already exists) → retry with `-retry-N` suffix (F2 recovery, up to 3 retries).
- Branch lifecycle persisted via `current-batch.json` for crash recovery.

### Stage 3 — Story enrichment (Gauntlet)

- Routing decision: `python3 scripts/gauntlet_injector.py --story <id> --print-mode` emits `{"mode": "quick"|"deep"}` JSON in <50ms (skips template parse).
- Full prompt construction: `--dry-run` writes `_bmad/auto-dev-state/gauntlet/<id>/prompts.json` (1 combined for quick mode, 5 separate for deep) — does NOT invoke `claude -p` here.
- `quick` mode triggers: routine stories (default). Output ~1000 words.
- `deep` mode triggers: `epic_id ∈ {4, 5, 7, 10}` (IAM/Billing/Compliance/AI Gateway) OR story text contains tag from `gauntlet.deep_tags` (auth/rls/pii/crypto/credential/compliance/billing/ai gateway/security-critical). Output ~1500 words.
- Substring tag-match is intentional (e.g., `crates/auth` triggers deep) — conservative side preferred.

### Stage 4 — create-story (headless `claude -p`, Opus)

- `claude -p "bmad-create-story for story <id> with gauntlet enrichment at <gauntlet-prompts-path>"`.
- Output → `_bmad/stories/<id>.md` (story spec with embedded gauntlet sections).
- On non-zero exit → Stage 6.fail (halt-on-fail, exit 2).

### Stage 5 — dev-story (headless `claude -p`, Sonnet)

- `claude --model sonnet -p "bmad-dev-story <id>"`.
- Agent reads `_bmad/stories/<id>.md`, writes code + commits on `feature/story-<id>`.
- Sonnet per cost-routing (implementation per spec).
- On non-zero exit → Stage 6.fail.

### Stage 6 — code-review (headless `claude -p`, Opus)

- `claude -p "bmad-code-review for story <id>"`.
- Adversarial review (Blind Hunter + Edge Case Hunter + Acceptance Auditor).
- Verdict parsed from last 5 lines of review log: `PASS | NEEDS-FIX | BLOCKED`. `UNKNOWN` (no match) treated as fail.
- On PASS → Stage 6.pass.
- On NEEDS-FIX or BLOCKED → Stage 6.fail (no auto-retry in Phase 1).

#### Stage 6.pass

- `git merge --no-ff feature/story-<id>` into `integration/<branch>` with message `merge story <id>`.
- `git branch -d feature/story-<id>` (safe delete — fails if merge incomplete).
- Update `sprint-status.yaml` with `<id>: done` via `flock -w 30` on `.sprint-status.lock` (concurrency guard).
- Append `<id>` to `state.json.stories` and set `last_story_id`.
- Advance to Stage 7.

#### Stage 6.fail

- Write `_bmad/auto-dev-state/halt-reason.txt` with: story-id, branch name, review report path, last log lines.
- Preserve `feature/story-<id>` for AABIT inspection.
- Runner exits 2.

### Stage 7 — Batch gate

- `python3 scripts/batch_gate.py --check`.
- Priority order: `batch_size` (hard cap) > `gate_story` (explicit halt) > `wave_transition` (natural pause).
- Boundary detection sources:
  - `batch_size_reached` — `state.json.stories.length >= customize.toml [batch].size` (default 10).
  - `gate_story` — last story matches `[batch].gate_story_ids` (config list) OR has `**Type:** 🚦 GATE story` marker in epics.md OR title contains "gate"/"rollback gate".
  - `wave_transition` — `wave_of(next_ready) != wave_of(last_story)`.
- On `boundary: false` → loop to Stage 1 (next iteration).
- On `boundary: true` → Stage 8 (exit 3 after summary).

### Stage 8 — Checkpoint halt

- Render `templates/checkpoint-summary.md` populated with: batch stories, review iterations, branches, integration branch name, AABIT decision points.
- Save to `_bmad/auto-dev-state/checkpoint-log/<batch-N>.md`.
- Runner exits 3 (signals "ready for AABIT review" to wrapper).

#### AABIT actions at checkpoint (manual, outside runner)

1. **Approve** → `git checkout main && git merge --squash integration/<branch> && git commit && git branch -d integration/<branch>`, then re-invoke `/bmad-auto-dev` to start next batch.
2. **Abort** → preserve integration branch; manual rollback decision.
3. **Inspect** → review individual feature branches before approval.

## Failure modes

| # | Symptom | Stage | Action |
|---|---------|-------|--------|
| F1 | `git status` not clean at Stage 0 | 0 | HALT exit 1; AABIT commits/stashes |
| F2 | `feature/story-<id>` exists from prior run | 2 | Retry with `-retry-N` suffix (up to 3); else HALT exit 4 |
| F3 | `claude -p` exits non-zero with no output | 4/5/6 | Halt-on-fail exit 2; AABIT inspects branch + halt-reason.txt |
| F4 | Code review verdict NEEDS-FIX / BLOCKED | 6 | Stage 6.fail (no auto-retry in Phase 1) — exit 2 |
| F5 | `sprint-status.yaml` parse/lock fail | 6.pass | HALT exit 2; preserve state for AABIT manual fix |
| F6 | Dependency analyzer returns story whose deps file is absent | 1 | Validator (`--validate`) warns; usually means epics.md typo |
| F7 | Gauntlet template parse fail (missing 5 lenses) | 3 | HALT exit 4; AABIT inspects `templates/gauntlet-prompts.md` |
| F8 | `state.json` corrupted JSON | 0 | HALT exit 4; AABIT deletes file → fresh batch |

## Cost-routing per stage

| Stage | Model | Why |
|-------|-------|-----|
| Stage 1 (dependency_analyzer) | n/a (Python) | deterministic |
| Stage 3 (gauntlet routing) | n/a (Python) | rule-based |
| Stage 4 (create-story) | Opus 4.7 | context-heavy, structured story spec output |
| Stage 5 (dev-story) | Sonnet 4.6 | implementation per spec — Sonnet sufficient |
| Stage 6 (code-review) | Opus 4.7 | adversarial review needs depth |

## Customization

Project-level overrides go in `<project>/.bmad/customize.toml` (loaded by all 4 scripts). User-level defaults live in this skill's `customize.toml`. See README for common knobs (`batch.size`, `gauntlet.deep_epics`, `failure.strategy`, `models.dev_story`).

## Invariants (do not violate)

1. **One story = one feature branch.** Never accumulate multiple stories on one branch.
2. **Halt-on-fail.** Phase 1 has no silent retry — AABIT must intervene on every review fail.
3. **Headless `claude -p` per stage.** Fresh context per stage — prevents prompt drift.
4. **sprint-status.yaml is the only source of truth for done-status.** Branch state is secondary.
5. **`flock` on sprint-status writes.** Phase 2 worktree parallelism will share the same lock.
6. **Zero extra deps.** Stdlib python3 + bash + git + flock. No PyYAML / jq / pytest required.

## Roadmap

- **Phase 1 (this build):** A (core pipeline) + C (Gauntlet hybrid) + D (batch + checkpoint).
- **Phase 2 (future):** B (worktree parallelism with conflict detection), ML re-ordering, auto-rollback on integration test failure.

See `spec/spec_bmad-auto-dev.md` in the Odyssey repo for full design rationale and Decision Log.
