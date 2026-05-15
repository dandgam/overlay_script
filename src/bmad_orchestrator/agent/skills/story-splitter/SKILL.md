---
name: story-splitter
description: Stage 3.6 pre-split check — decomposes large stories (AC≥7 OR hours≥4) into atomic sub-stories with explicit deps. Effect +40pp first-try PASS rate, 2-4× wall-clock через parallel sub-stories. См. spec §21.
---

# story-splitter skill

## Когда активируется

- **Stage 3.6** в pipeline (между Gauntlet 5-lens и create-story)
- **Gate condition:** AC count ≥ 7 OR estimated_hours ≥ 4 OR frontmatter `splittable: true`
- **Chat-mode:** «раздели 1.5», «разбей story X на части»

## Source of principles

`/home/server/odyssey/spec/story-splitting-principles-2026-05-16.md` (AABIT, 2026-05-16) — эмпирика из Wave 0a + Wave 0b.

## Phasing (Phase 2 MVP → v1 → v2)

### Phase 2 MVP — **heuristic only** (без LLM, без overhead)

```python
def heuristic_check(story):
    if story.estimated_hours >= 4:
        return "ALERT: estimated_hours=4+, consider split"
    if len(story.acceptance_criteria) >= 7:
        return "ALERT: AC>=7, consider split"
    if count_architectural_layers(story) >= 3:
        return "ALERT: 3+ layers detected, consider split"
    return "OK"
```

Output → Telegram push к AABIT с кнопками `[split вручную] [keep monolith] [подробнее]`. **НЕ автосплит** в MVP — нужны baseline данные.

### Phase 2 v1 — LLM-driven (после 5-10 stories Wave 1a)

Opus 4.7 принимает декомпозицию. Input ~4000 tokens, Output ~500 tokens, ~$0.10 per check.

```json
INPUT:
{
  "story_outline": "<from epics.md>",
  "ac_list": ["AC1", "AC2", ...],
  "gauntlet_findings": {...},
  "estimated_hours": int,
  "touches_files": [...]
}

OUTPUT:
{
  "decision": "split" | "keep",
  "rationale": "<один абзац>",
  "sub_stories": [
    {
      "id": "X.a",
      "scope": "EventBus trait + Event struct only",
      "ac": ["AC1", "AC2"],
      "estimated_hours": 2,
      "deps": [],
      "touches_files": ["src/event_bus/trait.rs"]
    },
    {
      "id": "X.b",
      "scope": "RedisStreamEventBus implementation",
      "ac": ["AC3", "AC4", "AC5"],
      "estimated_hours": 3,
      "deps": ["X.a"],
      "touches_files": ["src/event_bus/redis_impl.rs"]
    }
  ]
}
```

### Phase 2 v2 — tuning + cheaper model

После Epic 1 closed:
- Measure observed first-try PASS rate
- Consider switching pre-split LLM to Haiku 4.5 (10× cheaper) — если простой эвристики хватает
- Tune AC threshold (5? 6? 7? 8?) на основе data

## Atomicity rules для sub-story

| Правило | Проверка |
|---------|----------|
| AC ≤ 5 | `len(sub.ac) <= 5` |
| Один слой | trait OR impl OR migration OR tests OR conventions — не комбинация |
| Self-contained merge | После merge integration branch build green, tests pass |
| Explicit deps | если B нужна A → `deps: ["A"]` |
| Атомарность | НЕ split если разрушает критический invariant (RLS policy + test of policy) |
| Parallel-friendly | Без deps → можно параллельно в разных worktree |

## Anti-signals (НЕ split)

- Story = один артефакт (одна миграция / endpoint / helper)
- AC образуют атомарную единицу
- Split добавит дублирование контекста
- Foundation story (Wave 0a/0b архитектурная база)
- `frontmatter.splittable: false`

## Actions после split decision

| Decision | Action |
|----------|--------|
| `keep` | Continue Stage 4 (create-story) с original |
| `split` | (1) flock + update sprint-status.yaml с sub-stories; (2) mark parent `superseded`; (3) dispatch первая dep-free sub-story в Stage 4; (4) DAG planner получает остальные для parallel dispatch |

## Storage

```
<target>/_bmad-output/runs/<wave>/split-decisions/<story_id>.json
<target>/_bmad-output/runs/<wave>/<story>.events.jsonl   # включает stage_3_6 events
<orchestrator>/state.db                                   # splitting_metrics table
```

## Cache (anti-oscillation)

Split decisions per `story_id` кэшируются. Не пересчитываем для той же story (LLM может выдать разные splits на повторных запусках — fixate первое решение).

## KPIs для go/no-go

```json
{
  "splitting_metrics": {
    "wave": "1a",
    "checks_performed": 50,
    "checks_returned_split": 15,
    "preflight_cost_usd": 5.0,
    "additional_subs_total_cost_usd": 180,
    "saved_retry_cost_usd": 95,
    "net_overhead_usd": 90,
    "first_try_pass_rate_pre_split": 0.32,
    "first_try_pass_rate_post_split_subs": 0.71,
    "wall_clock_speedup_observed": 2.3
  }
}
```

**Disable rules:**
- `net_overhead_usd / saved_retry_cost_usd > 2` AND `speedup < 1.5` → выключить splitting в config
- `pass_rate_post_split > pre_split × 1.5` → продолжать

## Failure modes (§21.7)

1. Malformed JSON → fallback `keep`, log warning, continue Stage 4
2. Sub-story B зависит от A не в текущем batch → orchestrator переносит A первым
3. Split decision oscillates → cache per story_id
4. estimated_hours некорректен → fallback на AC count alone
5. Split разделил атомарность ошибочно (merge tests падают) → red flag: revert split, rerun monolith, lesson в memory
6. Overhead > benefit → отключить через config

## Manual override

- Frontmatter epics.md: `splittable: false` → агент НИКОГДА не split
- Telegram: «не дели 1.5» → сохраняется в session memory + future runs respect

## Tools

- `check_should_split(story_id) -> SplitDecision`
- `split_story(story_id, sub_stories) -> SplitResult`
- `read_memory(path="split-decisions/<story_id>.json")` — cache lookup
- `update_sprint_status` (flock-safe sub-stories injection)
- `escalate_to_human` (Phase 2 MVP — manual decision push)
