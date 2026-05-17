---
name: bmad-auto-dev
description: Autonomous BMad story pipeline. Run scripts/bmad-auto-dev-runner.sh — it handles everything (layout detect, story select, create-story, gauntlet, dev-story, code-review, merge). Use when user says "run auto-dev", "start bmad pipeline", "/bmad-auto-dev".
status: phase-1-ready
audience: all BMad projects (user-level + orchestrator-embedded)
---

# /bmad-auto-dev — Autonomous BMad story pipeline

## What to do (Claude reading this — read ONLY this section)

**Run this one command, then stop:**

```bash
bash scripts/bmad-auto-dev-runner.sh "$@"
```

That's it. Do not analyze the project. Do not check paths. Do not compare layouts. Do not ask the user any questions. The runner is autonomous and handles:

- Layout auto-detect (stock BMM v6 `_bmad/output/planning/` AND Odyssey hybrid `_bmad/planning-artifacts/` AND env-var override `BMAD_EPICS_FILE` etc.).
- Story ID format (both long-slug `1-1-skeleton-repo-uv-structure` and short `1.1`).
- Branch state (accepts current feature branch or creates `feature/story-<id>`).
- Pre-flight checks, story selection (dependency_analyzer), gauntlet enrichment, create-story, dev-story, code-review, merge, sprint-status update.

When the runner exits, report its exit code and a one-line summary. Do not interpret intermediate output — just relay.

## Exit codes (what the runner returns)

| Code | Meaning | What you tell the user |
|------|---------|------------------------|
| 0 | Success — iteration done or batch boundary reached cleanly | "done" |
| 1 | Pre-flight failed (genuinely missing artifact, dirty tree) | "pre-flight failed: <stderr last line>" |
| 2 | Halt-on-fail (review FAIL or `claude -p` non-zero) | "halt-on-fail: see halt-reason.txt" |
| 3 | Halt-on-checkpoint (batch boundary — N=10 or wave transition) | "checkpoint reached — review summary" |
| 4 | Internal error (unknown CLI arg, helper crash) | "internal error: see runner log" |

## Full reference

See `REFERENCE.md` (same directory) for the complete 9-stage workflow, failure modes table, cost-routing per stage, customization knobs, and design rationale. **Do not read REFERENCE.md unless the user explicitly asks** — it's verbose and will distract you from just running the runner.
