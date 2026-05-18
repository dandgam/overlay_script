---
name: merge-gate-spec
description: Stage 1 of two-stage merge gate. Checks acceptance criteria coverage and story completeness only. Activates as the first subagent when a worker completes.
---

# merge-gate-spec skill

## Когда активируется

- Stage 1 of two-stage merge gate (invoked by code_review_subscriber before merge-gate-quality)
- Triggered when WORKER_COMPLETED with status=success

## Scope — ТОЛЬКО AC coverage + story completeness

This stage reviews ONLY whether the implementation satisfies the story's acceptance criteria.
Do NOT evaluate code quality, lints, tests, or security here — that is merge-gate-quality's job.

## Checklist

1. **AC coverage** — every acceptance criterion in the story is addressed by the implementation
2. **Story completeness** — all required deliverables from the story File List are present
3. **Status fields** — story frontmatter can be marked `status: done` (no blockers from AC angle)
4. **No missing requirements** — nothing in the story's "Definition of Done" is left unimplemented

## Output format

```json
{
  "verdict": "approve" | "request_changes",
  "findings_count": 0,
  "findings": [
    {
      "criterion": "AC-3",
      "gap": "Endpoint POST /api/foo not implemented"
    }
  ],
  "stage": "spec"
}
```

## Decision rules

| AC coverage | Action |
|---|---|
| All ACs satisfied | verdict=approve |
| Any AC missing or partially addressed | verdict=request_changes |

## Tools

- `read_story`, `check_file_list`, `read_worktree`

## Notes

- Do NOT run linters, tests, or security scans in this stage
- If the story has no explicit AC section, check implicit requirements from description
- Emit verdict quickly — quality stage runs only if this stage approves
