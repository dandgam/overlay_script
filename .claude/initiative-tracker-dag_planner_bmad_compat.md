# Initiative Tracker — DagPlanner BMad-format compatibility

## Metadata
- **Spec:** spec/spec_dag_planner_bmad_compat.md
- **Parent specs:** spec_orchestrator_agent.md, spec_wave_1a_pilot_wiring.md
- **Integration branch:** integration/dag_planner_bmad_compat
- **Base branch:** main (post-merge aae72de — spec committed; f312e3c — --story flag)
- **Backup branch:** backup/dag_planner_bmad_compat-pre-2026-05-17
- **Created:** 2026-05-17
- **Bootstrap completed:** 2026-05-17 by auto-loop-spec-long
- **Scope frozen:** 2026-05-17
- **Runtime:** loop_wrapper
- **Delay seconds:** 300
- **Auto merge:** false

## Scope Freeze

### In scope
- B1: `src/bmad_orchestrator/runtime/bmad_format.py` — parser tolerant к upstream BMad sprint-status:
  - `parse_sprint_status_bmad(yaml_data) → SprintStatus`
  - `normalize_story_id(raw)` — kebab `3-1-lifecycle-state-machine` ↔ dotted `3.1`
  - `extract_status_token(value)` — strip embedded prose comments
  - Probe-detection BMad vs legacy schema
  - Unknown statuses → `backlog` + warning
  - ~15 unit tests
- B2: Wire DagPlanner._load() к новому parser'у
  - `find_ready()` correctly identifies `ready-for-dev` из BMad format
  - epic_id из story filename если sprint-status не даёт mapping
  - Story metadata из `_bmad/stories/X.Y.md` frontmatter (existing `parse_story_md`)
  - Integration test на real Odyssey: правильные epic-3 stories
  - Backward compat: legacy fixtures продолжают работать
  - ~10 integration tests + Odyssey golden fixture

### Out of scope (deferred)
- Story dependency graph reconstruction из BMad — текущий DAG достаточен для wave-1a-pilot
- Multi-project DAG (один graph для нескольких проектов) — backlog item 4
- Auto-fix sprint-status format normalization — read-only parser
- BMad story dependency analysis из markdown body — за скоупом

### Deferred to follow-up initiative
- Project-agnostic orchestrator (scan/doctor/init/resume) — `project_backlog_orchestrator_project_agnostic.md`
- Story dependency tracking из frontmatter — отдельный analyzer
- MCP tool `find_ready_stories` (agent/tools/dag.py) — оставлен на raw yaml path; B2 фиксил только non-LLM DagPlanner. Wiring MCP tool через parse_sprint_status_bmad — отдельная мелкая follow-up инициатива.

## Sessions

### Pending
(none — initiative complete)

### Current
(none — initiative complete, awaiting manual merge)

### Completed

- **id:** B1
  **title:** BMad-format sprint-status parser (CHECKPOINT)
  **completed:** 2026-05-17 18:50 UTC
  **commit:** bd6ab2a (integration/dag_planner_bmad_compat)
  **files_changed:** 2 (+515 LOC)
  **tests_passed:** 788 (760 baseline + 28 new; spec floor 15)
  **decisions_made:**
    - Probe detection: top-level `epics:` dict → legacy; `development_status:` dict OR bare `epic-N`/`N-M` keys → BMad upstream; else empty.
    - Status whitelist KNOWN_STATUSES={done, in-progress, ready-for-dev, backlog, review, deferred, optional}. Unknown → backlog + WARNING via stdlib `logging` (matches sandbox.py convention; no structlog dep added).
    - Story ID regex `^(?:story-)?(\d+)-(\d+[a-z]*)(?:-.*)?$` — preserves letter suffixes (1.17b, 1.10a) matching real `_bmad/stories/X.Y.md` filenames.
    - `extract_status_token` strips `#` comments first, then takes first whitespace-token — handles both `"done  # override"` and `"in-progress NEEDS-FIX"`.
    - Retrospective entries (`epic-N-retrospective`) silently dropped — they don't drive DAG readiness.
    - Story without prior epic-N entry → creates epic with default `backlog` status; ensures downstream iteration safety.
  **deferred_items:**
    - Story dependency analysis из markdown body — B2 will use existing `parse_story_md` for title/touches_files only; deps remain spec-defined.

- **id:** B2
  **title:** Wire DagPlanner к BMad parser + integration tests (FINAL)
  **completed:** 2026-05-17 (this session)
  **commit:** b9783ac (integration/dag_planner_bmad_compat)
  **files_changed:** 11 (+374 / -14 LOC)
  **tests_passed:** 798 (788 baseline post-B1 + 10 new B2 integration tests)
  **decisions_made:**
    - **Lookup-side normalization, not key-side** — `_status_by_id` / `filter_wave` / `ready_stories` apply `normalize_story_id` ON THE LOOKUP, не переписывая raw story IDs. Cause: existing tests assert `"1-1-tenant-signup" in graph.nodes` (kebab); если переписать ID на dotted, ломаются worker_spawn branch naming (`feature/1-1-tenant-signup`) и JSONL events. Lookup-bridge сохраняет raw IDs наружу + работает с canonical dotted IDs внутри.
    - **`_enrich_with_epic_and_title` живёт в `runtime/dag_planner.py`, не в `_common.list_stories`** — enrichment требует sprint_status context (для epic mapping override); делать это unconditionally в `list_stories` навредит escalate.py/retro.py которые не передают sprint_status.
    - **Title extraction in `parse_story_md`, не в DagPlanner** — title — общее свойство BMad story md format, не DagPlanner-specific. Добавление в `parse_story_md` aditive (никто `title` field не assert'ил), reusable от любого consumer'а.
    - **Golden fixture в `tests/fixtures/golden-odyssey/`** — snapshot real Odyssey state (8 story files + sprint-status), а не запускать тесты против live `/home/server/odyssey/`. Tests stay hermetic, не ломаются когда Odyssey state эволюционирует.
    - **MCP tool `find_ready_stories` (agent/tools/dag.py) НЕ wire'ил** — out-of-scope; B2 acceptance был "DagPlanner._load() использует parse_sprint_status_bmad". MCP tool — отдельный путь (agent SDK), его wiring — мелкая follow-up.
    - **Ruff isort fix на `__all__` в _common.py** — drive-by, pre-existing issue, blokировал acceptance "ruff clean".
  **deferred_items:**
    - На real Odyssey `find_ready()` возвращает `[]` потому что для backlog stories (3.3-3.13) ещё нет `.md` файлов в `_bmad/stories/`. BMad workflow: `bmad-story-create` материализует story md для каждого backlog item before dev starts. Парсер корректно identifies 3.3-3.13 как backlog в sprint-status. После того как story-create запущен → find_ready вернёт ready stories. Не B2 fix — pilot pre-step.

## Safety Gates Triggered
(none — code-only sessions, no destructive actions)

## Blockers / Pauses

- **date:** 2026-05-17 (B2 completed)
  **session:** B2
  **type:** manual_merge_pending
  **detail:** Initiative dag_planner_bmad_compat завершён на integration/dag_planner_bmad_compat. Auto merge=false — пользователь должен merge'ить вручную:
    `git checkout main && git merge --no-ff integration/dag_planner_bmad_compat -m "merge dag_planner_bmad_compat B1..B2"`
  **resolution:** PENDING (user action)

## Decisions Log

- **date:** 2026-05-17 (bootstrap)
  **session:** bootstrap
  **decision:** Initiative dag_planner_bmad_compat — 2 sessions B1+B2, surface=backend-python, code-only. Починить DagPlanner парсер чтобы понимать реальный upstream BMad sprint-status (kebab-IDs, embedded prose comments, flat top-level keys) — не только legacy orchestrator-scaffold формат (nested epics: {N: {stories: {...}}}).
  **rationale:** При подготовке smoke pilot на /home/server/odyssey обнаружено что `DagPlanner.find_ready()` возвращает stories из epic-1 (давно done) вместо current epic-3 work. Корень — schema mismatch между orchestrator's assumptions и real BMad format. `--story` flag (commit f312e3c) unblock'ит manual pilot, но для production usage нужен auto-pick.
  **impact:** После B2 — orchestrator корректно работает на upstream BMad проектах без зависимости от --story flag. Unblocked: parallel mode, auto-resume, multi-project workflow.

- **date:** 2026-05-17 (bootstrap)
  **session:** bootstrap
  **decision:** loop_wrapper runtime (не schedule_wakeup) несмотря на только 2 сессии.
  **rationale:** Текущий conversation context уже большой; loop_wrapper = fresh context каждый wake (`claude -p` headless), schedule_wakeup продолжает текущий разговор и накопит compaction. Лучше изолировать B work от current session.
  **impact:** Bootstrap создаёт tmux launcher + watchdog. User управляет lifecycle через tmux kill.

- **date:** 2026-05-17 18:50 UTC
  **session:** B1
  **decision:** Не интегрировать новый parser в DagPlanner в B1 — оставить wiring для B2.
  **rationale:** B1 acceptance явно ограничивается созданием `bmad_format.py` + 15 unit tests. Wiring `DagPlanner._load()` и integration tests на real Odyssey — отдельная B2 acceptance. Разделение: B1 = pure parser, B2 = consumer + e2e.
  **impact:** B2 теперь должен переделать `_status_by_id` + `filter_wave` чтобы они принимали unified shape от `parse_sprint_status_bmad`, либо вызвать parser в `DagPlanner.from_target`.

- **date:** 2026-05-17 18:50 UTC
  **session:** B1
  **decision:** Test count 28 (а не 15) — параметризованные cases в `test_normalize_story_id_variants` и `test_extract_status_token_variants` развернулись в 16 + 12 function-level tests = 28.
  **rationale:** Spec пишет "~15 unit tests" как floor effort, не cap. Parametrize дешевле: одна тест-функция × N inputs > 15 копипастных функций.
  **impact:** Tracker acceptance numbers скорректированы: B1 → 788 PASS, B2 → 798 PASS.

- **date:** 2026-05-17 (B2)
  **session:** B2
  **decision:** Lookup-side normalization, не переписывать story `id` на dotted form.
  **rationale:** Существующие тесты (test_s3_runtime, test_s2_tools, test_w1_real_pilot) assert'ят raw kebab `1-1-tenant-signup` в graph.nodes и в `find_ready` output. Переписывать ID на dotted ломало бы worker_spawn branch naming (`feature/1-1-tenant-signup` → `feature/1.1` — конфликтует с git branch validation), JSONL events, mock pilot. Lookup-bridge через `normalize_story_id` в `_status_by_id` / `filter_wave` / `ready_stories` решает совместимость без рефакторинга consumer'ов.
  **impact:** Раздельная canonical form: snapshot sprint_status хранит DOTTED keys (canonical для cross-source matching), graph nodes / `story["id"]` остаются raw kebab/dotted каким был filename. Worker_spawn / branch naming / JSONL events не затронуты.

- **date:** 2026-05-17 (B2)
  **session:** B2
  **decision:** MCP tool `find_ready_stories` (agent/tools/dag.py) НЕ wire'ил в parse_sprint_status_bmad.
  **rationale:** B2 spec acceptance явно targets `DagPlanner._load()` (non-LLM CLI/watchdog path). MCP tool `dag.find_ready_stories` живёт на отдельном code path (agent SDK), wiring там — отдельная мелкая инициатива (≤1 час). Расширять scope сейчас = scope creep.
  **impact:** Agent SDK consumers `find_ready_stories` пока работают только с legacy schema. Follow-up задача: wire MCP tool через `parse_sprint_status_bmad` для упрощения cross-path consistency.

## Journal

[2026-05-17 bootstrap] bootstrap: tracker + backup + integration branch созданы, 2 sessions planned, runtime=loop_wrapper, delay=300s, auto_merge=false
[2026-05-17 18:50 UTC] B1 execution: bmad_format.py + 28 tests written; 788 PASS (760 baseline + 28); ruff/mypy --strict clean; commit bd6ab2a
[2026-05-17 18:50 UTC] B1 completed → Completed; B2 promoted → Current; checkpoint logged locally (Auto merge=false, no main merge)
[2026-05-17 B2] B2 execution: DagPlanner.from_target/reload wired через parse_sprint_status_bmad; lookup-side normalization в filter_wave/_status_by_id/ready_stories; parse_story_md расширен title extraction (H1 `# Story X.Y: <title>`); list_stories epic_id derivation handles both kebab и dotted stems via normalize_story_id; `_enrich_with_epic_and_title` helper в dag_planner для two-source ground truth (sprint-status grouping → fallback на filename); golden fixture tests/fixtures/golden-odyssey/ (8 files, snapshot real Odyssey subset); 10 integration tests в test_b2_dag_planner_bmad_integration.py; 798 PASS (788 baseline + 10 new); mypy --strict clean; ruff clean (drive-by isort на __all__ в _common.py); commit b9783ac
[2026-05-17 B2] Manual probe vs real Odyssey: parser correctly identifies epic-3 stories (3.1/3.2=done, 3.3-3.13=backlog); find_ready() возвращает [] потому что для backlog stories 3.3-3.13 ещё нет .md файлов в _bmad/stories/ (BMad workflow: bmad-story-create материализует story files перед dev). Golden fixture demo показывает корректный find_ready вывод: id='3.3' epic_id='3' title='Hard-block trigger enforcement'
[2026-05-17 B2] B2 → Completed; initiative complete on integration/dag_planner_bmad_compat. Manual merge pending: `git checkout main && git merge --no-ff integration/dag_planner_bmad_compat -m "merge dag_planner_bmad_compat B1..B2"`. Runtime=loop_wrapper, no ScheduleWakeup; wrapper sees Final Report populated → exits.

## Final Report

**Status:** ✅ Complete — awaiting manual merge to main.

**Initiative:** DagPlanner BMad-format compatibility — починить orchestrator парсер чтобы понимать реальный upstream BMad sprint-status (kebab-IDs, embedded prose comments, flat top-level keys) в дополнение к legacy orchestrator-scaffold (nested epics:) формату.

**Integration branch:** `integration/dag_planner_bmad_compat`
**Backup branch:** `backup/dag_planner_bmad_compat-pre-2026-05-17` (don't delete)
**Sessions executed:** 2/2 (B1, B2)

**Commits on integration branch:**
- `aae72de` — spec(dag_planner): B initiative — BMad-format compat for sprint-status parser
- `9af4596` — tracker(dag_planner_bmad_compat): bootstrap via /auto-loop-spec-long, delay=300s
- `bd6ab2a` — feat(dag): BMad-format sprint-status parser (B1)
- `beb1f4a` — tracker: B1 completed; B2 promoted to Current
- `b9783ac` — feat(dag): wire DagPlanner к BMad-upstream parser + Odyssey golden fixture (B2)

**Aggregate stats:**
- Files added: 13 (bmad_format.py + B1 tests + B2 tests + 8 golden fixture files + spec + tracker)
- Files modified: 2 (dag_planner.py, agent/tools/_common.py)
- LOC delta: ≈ +889 / -14 across B1+B2
- Test delta: +38 (28 B1 unit + 10 B2 integration); 760 → 798 PASS total
- mypy --strict: clean on changed modules
- ruff: clean (B2 included drive-by isort fix on `__all__` in `_common.py`)

**What works now:**
- Orchestrator корректно парсит upstream BMad sprint-status (Odyssey-style): `development_status:` wrapper, kebab keys, embedded prose comments (`done  # ...`), multi-token statuses (`in-progress NEEDS-FIX`), retro entries (silently dropped), letter-suffixed IDs (`1.17b`, `1.10a`), bare flat layouts (no wrapper).
- Backward compat: legacy mock-odyssey (nested `epics:` schema) продолжает работать; graph nodes сохраняют raw kebab IDs; worker spawn / branch naming / JSONL events не затронуты.
- `DagPlanner.find_ready()` enrich'ит stories с `epic_id` (из sprint-status grouping или fallback из filename) и `title` (H1 `# Story X.Y: <title>`).
- Cross-form lookup: kebab story files матчатся с dotted sprint-status keys и наоборот через `normalize_story_id` bridge в `_status_by_id` / `filter_wave` / `ready_stories`.

**Known follow-ups (out of scope, не блокируют merge):**
1. MCP tool `find_ready_stories` (`agent/tools/dag.py`) использует raw `read_sprint_status_yaml` — wiring через `parse_sprint_status_bmad` = отдельная мелкая инициатива (~30 минут).
2. Story dependency tracking из markdown body — пока deps берутся из `parse_story_md` `- **depends_on:**` frontmatter (Odyssey-style stories с `**Depends on:**` H2 header не поддерживаются). Отдельный analyzer initiative.
3. На real Odyssey `find_ready()` сейчас возвращает `[]` потому что для backlog stories 3.3-3.13 ещё нет `.md` файлов. Pre-step: `bmad-story-create` материализует story files для backlog. Не B2 fix — BMad workflow gap.

**Manual merge command:**
```
git checkout main
git merge --no-ff integration/dag_planner_bmad_compat -m "merge dag_planner_bmad_compat B1..B2"
```

**Rollback (if needed):**
```
bash .claude/scripts/rollback-to-backup.sh backup/dag_planner_bmad_compat-pre-2026-05-17
```
