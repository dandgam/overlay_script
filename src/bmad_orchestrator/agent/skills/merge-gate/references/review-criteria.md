# Code-review & security-review criteria

## bmad-code-review (default, all stories)

Spec §11. Output JSON:

```json
{
  "verdict": "PASS" | "NEEDS-FIX" | "ESCALATE",
  "findings": [
    {
      "severity": "blocker" | "major" | "minor",
      "file": "src/auth/jwt.rs",
      "line": 142,
      "category": "security" | "correctness" | "style" | "test",
      "summary": "...",
      "fix_hint": "..."
    }
  ],
  "review_token_cost_usd": 0.42
}
```

### Severity routing

| Severity | Auto-fix? | Action |
|---|---|---|
| blocker | yes (≤5 total) | spawn_fixer; retry once; else escalate |
| major | yes (≤5 total) | same |
| minor | log only | merge proceeds; lessons captured |

### Finding budget cap (Patch I)

If `len(findings) > 20` → escalate immediately. Don't spawn fixer; story is
likely under-specified or over-scoped → human triage.

## security-review (only when `security_critical: true`)

Spec §15.5 + §9. Output JSON:

```json
{
  "verdict": "PASS" | "FAIL",
  "checks": [
    {"name": "sql_injection", "status": "PASS"},
    {"name": "pii_leak_in_logs", "status": "FAIL", "details": "..."},
    {"name": "auth_bypass", "status": "PASS"},
    {"name": "crypto_strength", "status": "PASS"}
  ]
}
```

### Rule: security findings are NEVER auto-fixed

`verdict == FAIL` → always escalate. Reason: spawning a fixer on security
findings risks regressing the bug under a different name (per spec §11.3).
Human reviews diff manually + decides.

## Merge eligibility matrix

| code-review | security-review (if applicable) | Action |
|---|---|---|
| PASS | PASS / N/A | `git_merge --no-ff` |
| NEEDS-FIX ≤5 | — | spawn_fixer, retry once, then escalate |
| NEEDS-FIX >5 OR >20 findings | — | escalate immediately |
| any | FAIL | escalate immediately, no merge |

## Post-merge

- `git_merge` returns commit sha → write to sprint-status.yaml (`status: done`,
  `merged_at`, `commit_sha`)
- Emit `wave_progress_event` for `wave-coordinator`
- Cleanup worktree (`cleanup_worktree` tool)
