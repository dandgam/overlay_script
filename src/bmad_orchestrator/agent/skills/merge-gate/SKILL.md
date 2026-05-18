---
name: merge-gate
description: DEPRECATED. Split into merge-gate-spec (AC coverage) and merge-gate-quality (code quality). See Phase 4 hardening #5.
---

# merge-gate skill (DEPRECATED)

> **WARNING:** This skill has been split into two separate skills per Phase 4 hardening #5.
> Do NOT use this skill directly. The orchestrator now invokes the two-stage pipeline.

## Replacement skills

- `agent/skills/merge-gate-spec/SKILL.md` — Stage 1: AC coverage + story completeness
- `agent/skills/merge-gate-quality/SKILL.md` — Stage 2: code quality (lints, tests, security)

## Two-stage pipeline

Stage 1 (spec) runs first. Stage 2 (quality) runs only if Stage 1 approves.
Final verdict = worst of both stages (approve+approve=approve, approve+request_changes=request_changes).

See `agent/run.py::code_review_subscriber` for the implementation.
