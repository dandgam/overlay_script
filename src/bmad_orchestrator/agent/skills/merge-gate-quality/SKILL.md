---
name: merge-gate-quality
description: Stage 2 of two-stage merge gate. Checks code quality only (lints, tests, security, performance). Runs ONLY when merge-gate-spec approved.
---

# merge-gate-quality skill

## Когда активируется

- Stage 2 of two-stage merge gate (invoked ONLY when merge-gate-spec verdict=approve)
- If merge-gate-spec returned request_changes, this stage is skipped (saves cost)

## Scope — ТОЛЬКО code quality

This stage reviews ONLY code quality aspects.
Do NOT re-check acceptance criteria here — that was merge-gate-spec's job.

## Checklist

1. **Lints** — ruff / mypy / eslint pass with no errors (warnings allowed if minor)
2. **Tests** — all new test files execute; no test_todo() placeholders; coverage ratio meets threshold
3. **Security** — no hardcoded secrets, no SQL string concatenation, no curl|bash patterns
4. **Performance** — no obvious N+1 queries, no blocking I/O in async paths
5. **P0 findings** — zero blocker-severity issues (blockers = correctness bugs or data-loss risk)

## Output format

```json
{
  "verdict": "approve" | "request_changes",
  "findings_count": 2,
  "findings": [
    {
      "severity": "blocker" | "major" | "minor",
      "file": "src/foo.py",
      "line": 42,
      "category": "security" | "correctness" | "style" | "test",
      "summary": "...",
      "fix_hint": "..."
    }
  ],
  "stage": "quality"
}
```

## Decision rules

| Findings | Action |
|---|---|
| Zero blockers/majors | verdict=approve |
| Any blocker or major finding | verdict=request_changes |
| Minor findings only | verdict=approve (log only, lessons captured) |

## Tools

- `run_linter`, `run_tests`, `read_worktree`, `check_security`

## Notes

- This stage is cost-saving: only runs when AC coverage is already confirmed
- Security-critical stories (security_critical: true in frontmatter) → always escalate on security findings
