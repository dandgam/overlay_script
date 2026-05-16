# File-level conflict (mutex) detection

## Зачем

Spec §3 cap #2: story-level DAG **без file-level mutex** = merge кошмар.
Две stories могут не зависеть в DAG (`depends_on` пуст), но касаться одного
файла → одновременный spawn → race condition при merge на integration.

## Алгоритм

```python
def can_run_concurrent(story_a, story_b, in_flight: set[str]) -> bool:
    shared = set(story_a.touches_files) & set(story_b.touches_files)
    if shared:
        return False
    # Дополнительно: если кто-то in_flight уже трогает файлы story_a
    for in_flight_id in in_flight:
        s = lookup(in_flight_id)
        if set(s.touches_files) & set(story_a.touches_files):
            return False
    return True
```

## Wildcard / dir-level

`touches_files: ["src/auth/**"]` — расширяется по glob перед сравнением.
`workers/src/*` и `workers/src/dispatcher.rs` — overlap по prefix → conflict.

## Output planner'а

```python
{
  "ready_now": [...],
  "blocked_by_mutex": [
    (story_id, blocked_by_in_flight_or_ready),
    ...
  ],
  "shared_files_map": {file_path: [story_ids]},
}
```

`blocked_by_mutex` — для UI/TUI: показать пользователю «1.7 ждёт пока 1.6
освободит src/auth/jwt.rs».

## Frontmatter helper

Stories обязаны декларировать `touches_files` в frontmatter. Отсутствие =
escalate (fail-fast, без предположений). Author story должен явно указать.
