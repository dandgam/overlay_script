---
name: merge-gate
description: Run bmad-code-review and (conditionally) security-review on a worktree, decide merge / reject / escalate. Activates when worker completes successfully.
---

# merge-gate skill

## Когда активируется

- Event `worker_completed` со status=ok
- Worker'у нечего commit'ить → пропуск
- В chat-mode: «давай мерджить 1.8a»

## Pipeline (spec §11)

```
1. run_code_review(worktree)
   ├─ PASS → continue
   ├─ FAIL → spawn_fixer OR escalate (per finding budget)
   └─ TIMEOUT → escalate
2. IF security_critical (per story frontmatter):
   run_security_review(worktree)
   ├─ PASS → continue
   └─ FAIL → ALWAYS escalate (security findings нельзя auto-fix)
3. git_merge(worktree, target=integration/<wave>)
   ├─ MERGED → cleanup_worktree + emit wave_progress_event
   └─ CONFLICT → escalate (rare, mutex должен был предотвратить)
```

## Decision matrix

| code-review | security-review | Action |
|-------------|-----------------|--------|
| PASS | PASS / N/A | merge |
| FAIL (≤5 findings) | — | spawn_fixer, retry 1×, then escalate |
| FAIL (>5 findings) | — | escalate immediately |
| — | FAIL | escalate immediately (no auto-fix) |

## Tools

- `run_code_review`, `run_security_review`, `git_merge`, `spawn_fixer`, `cleanup_worktree`, `update_sprint_status`

## Failure modes

- Reviewer subagent crashes → retry 1×, then mark story FAILED
- Merge conflict from concurrent worker → check mutex was respected, escalate if bug
