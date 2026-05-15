---
name: dag-planner
description: Build DAG of stories for a wave from sprint-status + epics.md, identify parallel-safe sets via shared-file analysis. Activates on wave start, epic boundary, story re-prioritization.
---

# dag-planner skill

## Когда активируется

- Старт wave (event: `wave_start`)
- Epic boundary (event: `epic_boundary_reached`)
- Story re-prioritization (chat: «перестрой план», «1.13 сначала»)

## Контекст загружается при активации

- `_bmad-output/planning-artifacts/epics.md` (frontmatter каждой story: `depends_on`, `touches_files`, `risk`, `security_critical`)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (текущий статус stories)
- `<wave>/lessons.md` если есть (предыдущие patterns)

## Output

```python
{
  "dag": <networkx.DiGraph serialized>,
  "ready_now": [story_id, ...],          # roots + mutex-clean
  "blocked_by_mutex": [(story_id, blocker), ...],
  "estimated_total_tokens": int,
  "estimated_total_usd": float,
  "critical_path_minutes": int
}
```

## Правила

1. **DAG edges** — только `depends_on` (explicit).
2. **Mutex edges** — `touches_files` overlap (implicit, не блокирует DAG, блокирует concurrent spawn).
3. **Security-critical эпики** — flagged для отдельного security_review pass.
4. **`requires_human: true`** — НЕ включается в auto pool, эскалация.

## Tools которые использует

- `build_dag` (читает sources)
- `find_ready_stories` (filter)
- `predict_conflicts` (mutex check)

## Failure modes

- Цикл в DAG → escalate человеку (signal что story frontmatter сломан).
- Нет ready stories но pool пустой → wait/halt с reason.
