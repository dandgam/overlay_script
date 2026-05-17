# bmad-auto-dev — autonomous BMad story pipeline

> Orchestrates batches of BMad stories through `create-story → Gauntlet → dev-story → code-review` with feature-branch isolation, halt-on-fail, and human checkpoints every 10 stories or on Wave-boundary.

## Status

**Phase 1 build — feature complete (S6 of 7).** S7 installs the skill to `~/.claude/skills/`. See `spec/spec_bmad-auto-dev.md` in the Odyssey repo for design rationale.

## Installation

This skill is initially built in `<project>/.claude/skills/bmad-auto-dev/` as a draft (S1-S6) and installed to `~/.claude/skills/bmad-auto-dev/` in S7 for global use across all BMad projects.

**Manual install (run from Odyssey repo root after S7 ships):**

```bash
cp -r .claude/skills/bmad-auto-dev ~/.claude/skills/
ls -la ~/.claude/skills/bmad-auto-dev/    # verify: SKILL.md, customize.toml, scripts/, templates/, learnings.md, README.md
```

After install, add a note to user `~/.claude/projects/<slug>/memory/MEMORY.md` so future sessions know the skill is available globally.

## Prerequisites per BMad project

Skill expects each project to have:

- `_bmad/planning-artifacts/epics.md` — source of truth for stories + dependencies
- `_bmad/implementation-artifacts/sprint-status.yaml` — current state of each story
- Clean git working tree (no uncommitted changes at start of run)
- Python 3.11+ available on PATH (tomllib for customize.toml; 3.9+ works if customize.toml absent)
- `flock` utility (standard on Linux)
- `git` ≥ 2.30

**Zero extra deps** — no PyYAML, no jq, no pytest. Everything ships stdlib.

## Usage

### First run (Odyssey example)

```text
$ /bmad-auto-dev

🔍 Detected BMad project: Odyssey
   Wave: 0a · Total stories: 326 · Done: 0

   Next ready story: 1.1 (Rust workspace scaffold)
   Estimated batch size: 7 (Wave 0a complete) OR 10 (fixed cap)

   Start batch 0a-1?  [Y/n]
```

### Dry-run mode (verified working)

End-to-end smoke output on Story 1.1 (S6 verification):

```text
$ bash .claude/skills/bmad-auto-dev/scripts/bmad-auto-dev-runner.sh --dry-run --max 1

[runner] Stage 0 — pre-flight (project=/home/server/odyssey, dry-run=1, max=1, resume=0)
[runner] Stage 0 — integration branch: integration/bmad-auto-dev
[runner] ===== iteration 1 / 1 =====
[runner] Stage 1 — selecting next ready story
  {"story_id": "1.1", "wave": "0a", "deps_met": true}
[runner] Stage 2 — branch: feature/story-1.1 (from integration/bmad-auto-dev)
[runner] Stage 3 — Gauntlet (--print-mode)
  {"story_id": "1.1", "epic_id": "epic-1", "mode": "deep"}
[runner] Stage 3 — DRY: would run gauntlet_injector --story 1.1 --mode deep (prompts not invoked)
[runner] Stage 4 — DRY: would spawn 'claude -p "bmad-create-story 1.1"'
[runner] Stage 5 — DRY: would spawn 'claude --model sonnet -p "bmad-dev-story 1.1"'
[runner] Stage 6 — DRY: would spawn 'claude -p "bmad-code-review 1.1"'
[runner] Stage 6.pass — DRY: would merge + delete + update sprint-status for 1.1
[runner] Stage 7 — batch gate check
  {"boundary": false, "reason": "continue"}
[runner] iteration 1 complete — continuing
[runner] reached --max=1; exiting cleanly
```

Exit code 0. No git mutations, no `claude -p` spawned, no sprint-status writes.

### Resume after halt

```text
$ /bmad-auto-dev --resume

🛑 Halt state found: _bmad/auto-dev-state/halt-reason.txt
   (skill clears halt-reason.txt and resumes from Stage 1)
```

In dry-run, `--resume` logs "DRY: would clear" but preserves the halt file (no mutations).

## Exit codes

| Code | Meaning | Wrapper action |
|------|---------|----------------|
| 0 | Success (all `--max` iters done OR `all_done`) | continue / mark batch done |
| 1 | Pre-flight failure (dirty tree / missing artifacts) | AABIT must fix → re-invoke |
| 2 | Halt-on-fail (review FAIL or `claude -p` non-zero) | AABIT inspects feature branch + halt-reason.txt |
| 3 | Halt-on-checkpoint (batch boundary) | AABIT reviews summary + decides merge/abort |
| 4 | Internal error | inspect runner log; likely a bug |

## Configuration

Override defaults via `<project>/.bmad/customize.toml`. See `customize.toml` in this skill directory for available keys.

Most common overrides:

| Key | Default | Effect |
|-----|---------|--------|
| `batch.size` | 10 | stories per batch before checkpoint halt |
| `batch.gate_story_ids` | `[]` | force-checkpoint after these story IDs |
| `gauntlet.default_mode` | `quick` | when auto-detect inconclusive |
| `gauntlet.deep_epics` | `["epic-4","epic-5","epic-7","epic-10"]` | always route deep |
| `gauntlet.deep_tags` | auth/rls/pii/crypto/credential/compliance/billing/ai gateway/security-critical | substring match in story body |
| `failure.strategy` | `halt` | Phase 1 — no auto-retry |
| `models.dev_story` | `sonnet` | cost routing (Sonnet 4.6) |

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Exit 1 at Stage 0 | dirty WC | `git stash` or commit/abandon, retry |
| Exit 1 "missing artifact" | no `_bmad/planning-artifacts/epics.md` | not a BMad project — invoke `bmad-create-epics-and-stories` first |
| Exit 2 + halt-reason.txt present | review FAIL or claude -p crash | inspect `feature/story-<id>` branch + halt-reason; `--resume` after fix |
| Exit 3 with checkpoint summary | normal batch boundary | AABIT reviews `_bmad/auto-dev-state/checkpoint-log/<batch-N>.md` |
| Exit 4 unknown CLI arg | typo | run with `--help` |
| `claude -p` hangs > 30 min | network or model rate limit | Ctrl-C, check `~/.claude/logs/`, `--resume` |
| `sprint-status.yaml` parse error | concurrent edit / corruption | `git log -- _bmad/implementation-artifacts/sprint-status.yaml`, `git checkout HEAD --` if safe |
| All stories halt at same Stage | systemic config issue | check `halt-reason.txt`, then `customize.toml` overrides |
| Cost overrun | over-routing to deep Gauntlet | tighten `gauntlet.deep_tags` (remove false-positive substrings) |
| Gauntlet routes Story X.Y deep but expected quick | substring match on identifier (e.g., `crates/auth`) | accept (conservative side preferred) OR override per `gauntlet.deep_epics` exclusion |
| `feature/story-<id>` collision | leftover from prior run | runner auto-retries with `-retry-N` suffix; inspect existing branch if collision persists |

## CLI reference (per-script)

```bash
# Pick next ready story
python3 scripts/dependency_analyzer.py --next [--wave <id>]
python3 scripts/dependency_analyzer.py --validate    # warn on unknown deps refs

# Gauntlet (routing decision)
python3 scripts/gauntlet_injector.py --story <id> --print-mode
python3 scripts/gauntlet_injector.py --story <id> --mode auto --dry-run > prompts.json
python3 scripts/gauntlet_injector.py --story <id> --mode deep --output enriched.md

# Batch gate
python3 scripts/batch_gate.py --check
python3 scripts/batch_gate.py --check --last-story 1.7 --batch-count 2   # test override

# Runner (full pipeline)
bash scripts/bmad-auto-dev-runner.sh                   # production run
bash scripts/bmad-auto-dev-runner.sh --dry-run         # no claude -p, no git mutations
bash scripts/bmad-auto-dev-runner.sh --max 5           # cap iterations
bash scripts/bmad-auto-dev-runner.sh --resume          # clear halt-reason.txt
```

## Architecture

See `SKILL.md` for the 9-stage workflow detail and `spec/spec_bmad-auto-dev.md` (Odyssey repo) for full design.

Key invariants:

1. **One story = one feature branch.** Never accumulate multiple stories on one branch.
2. **Halt-on-fail.** No silent retries in Phase 1 — AABIT must intervene on every code-review fail.
3. **Headless `claude -p` per stage.** Each stage gets fresh context — prevents prompt drift across long batches.
4. **sprint-status.yaml is the only source of truth for done-status.** Branch state is secondary.

## Test suite

```bash
python3 -m unittest discover -s scripts -p "test_*.py" -v
```

92 tests total: dependency_analyzer (20) + gauntlet_injector (38) + batch_gate (34). All green at S6 ship.

## Roadmap

- **Phase 1 (current build):** A (core pipeline) + C (Gauntlet hybrid mode) + D (batch + checkpoint).
- **Phase 2 (future):** B (worktree parallelism + conflict detection), ML re-ordering, auto-rollback on integration test failure.

## License

Internal — part of AABIT/Odyssey toolchain.
