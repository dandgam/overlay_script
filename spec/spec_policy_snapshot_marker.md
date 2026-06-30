# Spec — Policy Snapshot Hash + Active Run Marker

**Версия:** 0.1.0
**Дата:** 2026-05-20
**Автор:** AABIT
**Статус:** ready for /auto-loop-spec-short
**Источник:** bmad-automator `runtime_policy.py` (`snapshot_effective_policy`, marker heartbeat)
**Размер:** SHORT-MEDIUM (2-3 сессии)
**Приоритет:** P1 — reproducibility + recovery

---

## 1. Executive Summary

Сейчас в Virgil можно поменять `_bmad/_config/orchestrator-policy.yaml`, judge model, limit_max_review_cycles **в полпрогона** — и оркестратор подхватит новое поведение. Это убивает reproducibility и делает post-mortem на пилотах болезненным.

Также: если оркестратор крашится — у нас нет marker'а «run был активен», stop-hook может тихо завершить worker'ов которые ещё работали, а ресюм пытается восстановить состояние из `events.jsonl` без heartbeat-проверки.

Конкурент решает оба эти вопроса одной парой механизмов:

1. **Policy snapshot** — в начале каждого run сериализуется effective policy → `<output>/policy-snapshots/<timestamp>-<hash>.json`, hash пишется в state. На resume hash проверяется → если файл поменялся, halt with policy_mismatch.
2. **Active run marker** + heartbeat — JSON-файл с `epic / currentStory / stateFile / pid / heartbeat: ISO`, обновляется каждые 30 секунд из event_loop. На startup проверяется `now - heartbeat > 5min → stale marker → cleanup`.

---

## 2. Goals / Non-Goals

### Goals
- G1: при старте run сериализовать effective policy (config.yaml + judge config + skill versions + agent SDK version) в JSON snapshot c md5 hash в имени файла.
- G2: hash + path записывать в `state/db.py` table `runs` (новые колонки `policy_snapshot_file`, `policy_snapshot_hash`).
- G3: на resume — пересчитать hash файла, сравнить с записанным; mismatch → halt.
- G4: ввести `runtime/marker.py` с `create/heartbeat/check/remove` API.
- G5: heartbeat обновляется каждые N секунд (30 default) из event_loop.
- G6: на startup orchestrator-а — `marker.check_stale()` с TTL (5 min default).

### Non-Goals
- НЕ переписываем `event_loop.py` целиком, только добавляем heartbeat tick.
- НЕ меняем формат state DB (только добавляем колонки и migration).
- НЕ блокируем concurrent runs на одном проекте (это backlog).

---

## 3. Архитектура

### Policy snapshot

```python
# runtime/policy_snapshot.py
def snapshot_policy(project_root: Path, run_id: str) -> SnapshotResult:
    effective = compose_effective_policy(project_root)
    # включает: orchestrator-policy.yaml, _bmad/bmm/config.yaml (если есть),
    # judge model name, claude SDK version, virgil version, embedded skills hash
    stable_json = json.dumps(effective, indent=2, sort_keys=True)
    digest = hashlib.md5(stable_json.encode()).hexdigest()[:8]
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    path = project_root / "_bmad-output" / "virgil" / "policy-snapshots" / f"{timestamp}-{digest}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stable_json)
    return SnapshotResult(path=path, hash=digest, json=effective)
```

### Marker

```
~/.bmad-virgil/active-marker.json    # global, не per-project
{
  "run_id": "uuid",
  "project_root": "/path",
  "epic": "1",
  "current_story": "1.3",
  "stories_remaining": 7,
  "state_db": "/path/.bmad/virgil-state.db",
  "pid": 12345,
  "heartbeat": "2026-05-20T18:23:45Z",
  "created_at": "2026-05-20T17:00:00Z"
}
```

Heartbeat tick от `runtime/event_loop.py` каждые 30s.
На startup: если marker exists и heartbeat младше TTL → halt with "another run active". Если старше TTL → cleanup + log audit event.

## 4. Изменения по файлам

| Файл | Что |
|---|---|
| `src/bmad_orchestrator/runtime/policy_snapshot.py` (новый) | `snapshot_policy`, `verify_snapshot_hash`, `compose_effective_policy` |
| `src/bmad_orchestrator/runtime/marker.py` (новый) | API marker create/heartbeat/check/remove |
| `src/bmad_orchestrator/state/db.py` | migration: ALTER TABLE runs ADD COLUMN policy_snapshot_file/hash; ADD EventType `policy_snapshot_created`, `policy_snapshot_mismatch`, `marker_stale_cleanup` |
| `src/bmad_orchestrator/runtime/event_loop.py` | heartbeat tick |
| `src/bmad_orchestrator/cli/main.py` | при `run` команде вызвать snapshot + create marker; при `resume` — verify hash; cleanup на graceful shutdown |
| `src/bmad_orchestrator/runtime/replay.py` | resume через verify_snapshot_hash |
| `tests/runtime/test_policy_snapshot.py` (новый) | 6+ тестов |
| `tests/runtime/test_marker.py` (новый) | 5+ тестов |

## 5. Acceptance Criteria

- AC1: При старте run появляется файл `_bmad-output/virgil/policy-snapshots/<ts>-<hash>.json` с readable JSON.
- AC2: На resume того же run — hash совпадает, продолжаем. На поменянном файле (manual edit) — halt с EventType `policy_snapshot_mismatch`.
- AC3: Marker создаётся на старте, heartbeat обновляется ≥1 раз/минуту в run.
- AC4: Concurrent run на том же проекте → halt "another run active".
- AC5: После crash оркестратора (kill -9) и старта через 6 минут — marker считается stale, cleanup + log.
- AC6: Tests grow ≥11; 0 regressions.

## 6. Test Plan

| Тест | Сценарий |
|---|---|
| `test_snapshot_creates_file_with_hash` | happy path |
| `test_snapshot_hash_stable_on_same_inputs` | determinism |
| `test_verify_hash_fails_on_modified_file` | manual edit |
| `test_compose_includes_judge_and_skill_versions` | coverage |
| `test_marker_create_writes_pid_and_heartbeat` | unit |
| `test_marker_heartbeat_updates_field` | unit |
| `test_marker_stale_detection_with_ttl` | unit |
| `test_marker_concurrent_run_blocked` | integration |
| `test_resume_with_matching_snapshot_continues` | integration |
| `test_resume_with_mismatch_halts` | integration |

## 7. Rollout

- Feature flag: `BMAD_POLICY_SNAPSHOT=1` (default on), `BMAD_MARKER=1` (default on).
- Migration: existing runs without snapshot_hash → legacy mode (warning log, не блокируем).

## 8. Risks

| Риск | Митигация |
|---|---|
| Stale marker блокирует legitimate restart | TTL + force-flag `--force-cleanup-marker` |
| Snapshot hash слишком чувствителен (любое изменение → halt) | hash считается ТОЛЬКО по policy-relevant полям (не по timestamps); +CLI flag `--accept-policy-drift` для аварийной ситуации |
| Concurrent runs на разных проектах фолят | marker per-project (ключ = project_root canonical) |

## 9. Effort

2-3 сессии. (1) policy_snapshot + tests, (2) marker + heartbeat + tests, (3) wiring в CLI + resume + integration.

## 10. Dependencies

- Зависит от: spec_competitor_quickwins (cli_contract).
- Блокирует: spec_operator_first_class_modes (resume mode опирается на snapshot verify).
