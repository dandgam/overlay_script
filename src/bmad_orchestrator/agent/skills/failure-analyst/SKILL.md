---
name: failure-analyst
description: Diagnose worker exit ≠ 0 — test fail / merge conflict / API error / context drift / skill bug. Suggest fix or escalate. Activates on worker_halt_file or worker_completed(status=fail).
---

# failure-analyst skill

## Когда активируется

- Event `worker_halt_file` (halt-reason.txt появился)
- Event `worker_completed` с non-zero exit

## Diagnosis tree

1. **API error** (rate limit, overloaded, network) — Patch G retry handled it, escalate if persistent
2. **Test fail** в `bmad-auto-dev` Stage 5 → попробовать `spawn_fixer` если ≤5 findings
3. **Merge conflict** → не должно случаться (mutex blocked), bug в DAG planner — escalate как P0
4. **Code-review NEEDS-FIX 20+ findings** (Patch I budget cap) → escalate, story помечается suspicious
5. **claude -p hang** (Patch H 30 min timeout) → liveness check, kill если dead
6. **Stage 4/5/6 non-zero exit** → read halt-reason.txt, classify, escalate с context
7. **Context drift** (worker зациклился, повторяет actions) → kill + restart с расширенным prompt

## Output

```python
{
  "category": "test_fail" | "merge_conflict" | "api_error" | "review_findings" | ...,
  "recoverable": bool,
  "suggested_action": "spawn_fixer" | "retry" | "escalate" | "mark_failed",
  "summary_for_human": "<краткое объяснение>"
}
```

## Tools

- `tail_worker_jsonl` (read events)
- `get_worker_status`
- `spawn_fixer` (если recoverable)
- `escalate_to_human` (если no auto-fix path)
- `update_sprint_status` (mark FAILED)
