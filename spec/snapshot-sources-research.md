# Research — источники данных для `_build_snapshot`

> Подготовлено для задачи #5 (переписать `_build_snapshot`). Цель — собрать в одном месте всё что нужно знать, чтобы завтрашняя сессия стартовала сразу с имплементации, не повторяя investigation.
>
> **Источник истины — код.** Если при имплементации что-то расходится с этим документом — верь коду.

## 1. Текущее состояние (что чиним)

**Файл:** `src/bmad_orchestrator/cli/main.py:272-292`

`_build_snapshot()` сейчас — заглушка. Возвращает `DashboardSnapshot` с захардкоженными:
```python
status="idle", progress_done=0, progress_total=0,
budget_spent_usd=0.0, workers=[], events_tail=[], ...
```

Комментарий в коде: `"Best-effort dashboard snapshot (S8: minimal; runtime-fed in pilot)"`. Задумывался временным; pilot прошёл — пора подключить к реальным источникам.

Вызывается из команды `status` (sync) и `status --live` (через `run_live` каждые 2с).

## 2. DashboardSnapshot — что заполнять

Определение: `src/bmad_orchestrator/cli/tui.py:31-57`. Поля:

| Поле | Тип | Источник |
|---|---|---|
| `status` | str: running/paused/halted/idle | state.db `agent_session.status` |
| `wave` | str \| None | state.db `agent_session.wave` |
| `project` | str \| None | state.db `agent_session.target_project` |
| `progress_done` | int | sprint-status.yaml — count stories со status=`done` |
| `progress_total` | int | sprint-status.yaml — общий count stories |
| `budget_spent_usd` | float | state.db `SUM(budget_tracker.spent_usd) WHERE session_id=?` |
| `budget_cap_usd` | float | `settings.budget.daily_limit_usd` (уже работает) |
| `budget_level` | str: ok/alarm/halt | derive: ratio spent/cap → пороги |
| `workers` | list[dict] | events.jsonl walk — workers с последним `worker_spawned` без `worker_completed`/`worker_halt_file` |
| `ready_next` | list[str] | sprint-status — stories со status=`ready-for-dev` |
| `blocked` | list[str] | sprint-status — stories со status=`in-progress` но прерванные (нюанс — можно отложить v1) |
| `done_recent` | list[str] | sprint-status — последние N stories со status=`done` |
| `agent_thinking` | str | `t("agent.idle")` для idle; иначе — описание текущей фазы |
| `events_tail` | list[str] | events.jsonl tail — последние N событий по timestamp |
| `supervisor_*` | — | оставить дефолты для v1 (отдельная инициатива) |

**Worker dict ключи** (что ожидает renderer — нужно проверить в `tui.py` ниже строки 60): минимум `worker`, `story`, `stage`, `state`, `tokens`, `$`. Структуру взять как есть из существующей таблицы в `_render_workers` (не угадывать).

## 3. state.db — реальный статус + бюджет

**Путь:** `settings.state_db` — default `Path("./state.db")` (`config.py:120`). На практике каждый запуск создаёт свою DB в orchestrator_home.

**API:** `bmad_orchestrator.state.db.StateDB` — **async** (`aiosqlite`). Context manager `connect(db_path)` (`state/db.py:110`).

**Схема** (state/db.py:26-70):
```sql
agent_session(
  id, target_project, wave, max_parallel,
  status TEXT CHECK (running|paused|stopped|error),
  started_at, ended_at, notes
)
budget_tracker(
  id, session_id, scope CHECK (story|batch|wave|day|phase),
  scope_target_id, spent_usd, spent_tokens,
  alarm_threshold, halt_threshold,
  breached_alarm, breached_halt, updated_at
)
event_queue(id, session_id, event_type, payload_json, emitted_at, consumed_at)
```

**Готовых sync-helper'ов нет.** В StateDB только async-методы (create_session, resolve_or_create_session, end_session, enforce_and_reserve, claim_next_event, …).

**Запросы для snapshot:**
```sql
-- latest active session
SELECT id, target_project, wave, status, started_at
  FROM agent_session
 WHERE status IN ('running', 'paused')
 ORDER BY started_at DESC
 LIMIT 1;

-- budget по session (для wave-scope или sum по всем scope'ам)
SELECT COALESCE(SUM(spent_usd), 0.0)    AS total_usd,
       COALESCE(SUM(spent_tokens), 0)   AS total_tokens
  FROM budget_tracker
 WHERE session_id = ?;
```

**Sync-bridge подход:** обернуть в `asyncio.run()`. Образец паттерна — `correct_course_cli` (`main.py:1521-1549`) уже делает `asyncio.run(_go())` внутри sync команды. Альтернатива — открыть raw `sqlite3.connect(db_path, timeout=...)` синхронно (без аsyncio). Это допустимо для read-only снимка, проще и быстрее. **Рекомендация:** использовать sync `sqlite3` для snapshot — `asyncio.run()` тяжелее и может конфликтовать с уже работающим event loop в `run --watch` (через `run_live`).

**Edge case:** если файл `state.db` не существует → graceful degrade на `status="idle"`. Используй `if not settings.state_db.exists(): return idle_snapshot`.

## 4. sprint-status.yaml — прогресс задач

**Готовые helpers** в `src/bmad_orchestrator/agent/tools/_common.py`:

| Функция | Что делает |
|---|---|
| `sprint_status_path(settings) → Path` | resolve по 5 layout'ам (lines 143-160) |
| `read_sprint_status_yaml(settings) → dict` | парсит, `{}` если missing (lines 163-170) |

**Нормализация формата:** sprint-status.yaml бывает в 2 форматах (upstream BMad flat-keyed vs legacy nested) — нормализатор `parse_sprint_status_bmad` в `runtime/bmad_format.py:1-46`. Унифицированная shape:
```python
{
    "epics": {
        "<epic_id>": {
            "status": "<token>",
            "stories": {"<X.Y>": "<token>"}
        }
    }
}
```

**StoryStatus tokens** (`models.py:22-32`): `backlog`, `ready-for-dev`, `in-progress`, `review`, `done`.

**Подсчёт progress** для snapshot:
```python
data = read_sprint_status_yaml(settings)
if data:
    normalized = parse_sprint_status_bmad(data)  # если нужно
    stories = []
    for epic in normalized.get("epics", {}).values():
        stories.extend(epic.get("stories", {}).values())
    progress_total = len(stories)
    progress_done = sum(1 for s in stories if s == "done")
    ready_next = [
        sid for epic in normalized["epics"].values()
        for sid, status in epic.get("stories", {}).items()
        if status == "ready-for-dev"
    ][:10]
    done_recent = [
        sid for epic in normalized["epics"].values()
        for sid, status in epic.get("stories", {}).items()
        if status == "done"
    ][-5:]
```

**Edge case:** `settings.target_project` может указывать на проект где sprint-status.yaml вообще нет (например при `bmad-orchestrator status` без активной сессии). → `read_sprint_status_yaml` уже возвращает `{}` — просто `progress=0/0`.

**Multi-project nuance:** для dashboard через `--project X` — нужно временно перевязать settings.target_project на этот проект. Использовать функцию-обёртку с явным `settings_for(project)` чтобы не мутировать глобал. Если активной сессии в state.db нет — взять проект из CLI-флага `--project`, иначе — `settings.target_project.name`.

## 5. events.jsonl — workers + хвост событий

**Layout:** `<runs_dir>/<wave>/<worktree>.events.jsonl`
- `runs_dir = settings.target_project / settings.artifacts_dir_name / "runs"` (`_common.py:98-103`)
- Каждый worker пишет в свой файл — один файл = один worktree = одна story.

**Helpers** (`_common.py`):
- `worker_jsonl_path(worktree, settings)` — формирует путь (line 318-328)
- `append_jsonl(path, event)` — writer (line 312-315)
- `is_pid_alive(pid)` — проверка живости процесса (line 331-343, psutil или os.kill(pid, 0))

**Формат записи** (одна JSON на строку):
```json
{"event_type": "worker_spawned", "worktree": "...", "story_id": "1.3",
 "branch": "...", "model": "...", "budget_cap_usd": 50.0, "pid": 12345,
 "mock": false, "sandbox_used": true, ...}
{"event_type": "stdout_line", "worktree": "...", "text": "..."}
{"event_type": "worker_completed", "worktree": "...", "story_id": "1.3", ...}
{"event_type": "worker_halt_file", ...}
```

**Lifecycle workers:** `worker_spawned` → ... → `worker_completed` (или `worker_halt_file`).
Эмиссии:
- `worker_spawned` — `worker_spawn.py:794` (real) и `:927` (mock fallback)
- `worker_completed` — `worker_spawn.py:588`, `:810`
- `worker_halt_file` — отдельная ветка (см. `worker_spawn.py:1014`)

**Стратегия для workers list:**
1. Найти текущий wave: из state.db latest session.
2. Скан `runs_dir / <wave>/*.events.jsonl`.
3. Для каждого файла прочитать **последнее** событие worker-lifecycle (`worker_spawned` / `worker_completed` / `worker_halt_file`).
4. Активен если последний lifecycle event = `worker_spawned` (и не `worker_completed`/`worker_halt_file` после него).
5. Опционально валидировать через `is_pid_alive(pid)` — пометить как `state="halted"` если процесс умер а completion-event не пришёл.

**Стратегия для events_tail:**
- Tail последних ~10-20 строк со всех `*.events.jsonl` в текущем wave, отсортировать по timestamp если есть (или по позиции в файле).
- Форматировать в read-friendly строки для дисплея (TUI ожидает `list[str]`).

**Performance:** при больших файлах не читать целиком — использовать `_tail_lines(path, n)` или открывать в reverse. Простой вариант для v1: `Path.read_text().splitlines()[-N:]` — приемлемо для events.jsonl ≤ нескольких MB.

## 6. Живые процессы

**Helper:** `is_pid_alive(pid)` (`_common.py:331-343`). С psutil → `pid_exists`; без psutil → `os.kill(pid, 0)`.

**Где взять PID:** в payload `worker_spawned` event (`pid` field — `worker_spawn.py:794`). PID появляется только в **real-mode** (mock: нет subprocess, PID не пишется).

**Использование в snapshot:**
- Для каждого «активного» worker'а (см. п. 5) — `is_pid_alive(pid)`.
- Если последний event был `worker_spawned` и `pid` присутствует, но процесс мёртв → пометить worker как `state="halted"` или вообще исключить.

**Не критично для v1.** Можно пропустить эту проверку и положиться только на events. PID-check — добавка надёжности.

## 7. Рекомендуемая структура `_build_snapshot`

```python
def _build_snapshot(*, project: str | None = None, wave: str | None = None) -> DashboardSnapshot:
    settings = load_settings()
    snap = DashboardSnapshot(  # все defaults
        project=project or settings.target_project.name,
        wave=wave,
        budget_cap_usd=settings.budget.daily_limit_usd,
    )

    # 1. state.db — status / wave / project / budget
    try:
        session = _read_latest_session(settings.state_db, project=project, wave=wave)
        if session:
            snap.status = session["status"]
            snap.wave = session["wave"]
            snap.project = session["target_project"]
            snap.budget_spent_usd = _read_session_budget(settings.state_db, session["id"])
            snap.budget_level = _derive_budget_level(snap.budget_spent_usd, snap.budget_cap_usd)
    except Exception as e:
        logger.debug("snapshot: state.db read failed: %s", e)

    # 2. sprint-status.yaml — progress
    try:
        progress = _read_sprint_progress(settings)
        snap.progress_done = progress.done
        snap.progress_total = progress.total
        snap.ready_next = progress.ready_next
        snap.done_recent = progress.done_recent
    except Exception as e:
        logger.debug("snapshot: sprint-status read failed: %s", e)

    # 3. events.jsonl — workers + tail
    try:
        if snap.wave:
            snap.workers = _read_active_workers(settings, snap.wave)
            snap.events_tail = _read_events_tail(settings, snap.wave, limit=15)
    except Exception as e:
        logger.debug("snapshot: events.jsonl read failed: %s", e)

    # 4. agent_thinking — derive из status
    snap.agent_thinking = _derive_agent_thinking(snap.status, snap.workers)
    return snap
```

**Принципы:**
- **Best-effort per source.** Каждый источник в своём try/except — падение одного не ломает остальные.
- **Sync sqlite3** для state.db (не async — проще, не конфликтует с `run_live`).
- **Helpers выносим** в `cli/snapshot.py` (новый файл) — `_build_snapshot` оставляем в main.py тонким диспетчером.
- **Логирование на debug**, не на warning — failure источника = норма (idle state), не повод спамить.

## 8. Тесты — что покрыть

| Тест | Что проверяет |
|---|---|
| empty state.db (file отсутствует) | status="idle", все списки пусты, не падает |
| state.db есть, session running | status="running", wave/project из БД |
| state.db, session paused | status="paused" |
| budget_tracker пустой | budget_spent_usd=0.0, level="ok" |
| budget_tracker > alarm threshold | level="alarm" |
| sprint-status.yaml missing | progress 0/0, ready_next=[] |
| sprint-status.yaml с 10 stories (3 done) | progress_done=3, progress_total=10 |
| events.jsonl с активным worker | workers непустой, в списке нужный story_id |
| events.jsonl с completed worker | этот worker исчез из workers list |
| broken yaml в sprint-status | не падает, progress 0/0 |
| corrupted events.jsonl line | пропускает битую строку, остальное парсит |

**Fixtures:** `tmp_path` для state.db + sprint-status.yaml + runs_dir. Существующие фикстуры для state.db можно найти в `tests/test_state_db.py` (если есть).

**Не сломать:** `tests/test_s8_cli_tui_pilot.py` и `tests/test_supervisor_tui.py` — они уже используют DashboardSnapshot. Прогнать после правок.

## 9. Файлы которые предстоит править / создать

| Файл | Действие |
|---|---|
| `src/bmad_orchestrator/cli/snapshot.py` | **создать** — sync helpers (state_db readers, sprint_progress, workers, events_tail) |
| `src/bmad_orchestrator/cli/main.py:272-292` | **переписать** `_build_snapshot` — тонкий диспетчер вызывающий helpers |
| `tests/test_snapshot_real_sources.py` | **создать** — unit-тесты на helpers + интеграционный на `_build_snapshot` |
| `src/bmad_orchestrator/cli/tui.py` | **не трогать** — DashboardSnapshot shape + renderer без изменений |

## 10. Open questions для решения по ходу

1. **Multi-project / cross-project статус.** Если в state.db несколько running-сессий разных проектов — показывать все или одну (latest)? **Рекомендация:** для v1 — одна latest, иначе UI становится перегруженным.
2. **events.jsonl size budget.** Если файлы выросли до сотен MB (long-running pilot) — простой `read_text().splitlines()[-N:]` будет тормозить. **Рекомендация:** для v1 ок, если станет проблемой — заменить на `_tail_lines` через seek.
3. **`workers[].state` mapping.** Worker_spawned без completed → `"active"` или `"running"`? Renderer в `tui.py` использует glyph mapping (`_STATE_GLYPH`). **Рекомендация:** проверить какие state'ы там определены, использовать тот же набор.
4. **PID-check включать в v1?** psutil может быть не установлен. **Рекомендация:** оставить опциональным — если PID есть в payload, проверять; если нет — пропускать.

## 11. Стартовый чеклист на завтра

1. ✅ Прочитать этот файл целиком.
2. Прочитать `cli/tui.py` строки 60-180 (renderer — какие ключи ждёт от `workers[]`, как форматирует `events_tail`).
3. Создать `cli/snapshot.py` с helpers (по разделу 7).
4. Переписать `_build_snapshot` (тонкий диспетчер).
5. Написать `tests/test_snapshot_real_sources.py` по разделу 8.
6. `ruff check && mypy && pytest tests/test_snapshot* tests/test_s8_cli_tui_pilot.py tests/test_supervisor_tui.py`
7. Если всё зелёное — обновить `methodology-virgil.md` (упомянуть фикс в Phase 5 priority queue) и закрыть task #5.
