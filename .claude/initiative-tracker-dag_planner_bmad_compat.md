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

## Sessions

### Pending

- **id:** B1
  **title:** BMad-format sprint-status parser (CHECKPOINT)
  **surface:** backend-python
  **spec_section:** 100-160
  **depends_on:** []
  **destructive_actions:** []
  **checkpoint:** true
  **estimated_retries_allowed:** 3
  **acceptance:**
    - Create `src/bmad_orchestrator/runtime/bmad_format.py` с `parse_sprint_status_bmad`, `normalize_story_id`, `extract_status_token`
    - Парсер принимает legacy (epic-nested) И upstream BMad (flat top-level kebab-keys) — probe detection
    - Story ID нормализация: kebab `3-1-title` → dotted `3.1` (first numeric.numeric)
    - Status token extraction strips comments после `#` и whitespace
    - Unknown status → `backlog` + structured warning log
    - `tests/test_b1_bmad_format.py` — 15 unit tests
    - `pytest tests/ -q` — 775 PASS (760 baseline + 15 new); ruff/mypy clean
    - `grep -c "def parse_sprint_status_bmad" src/bmad_orchestrator/runtime/bmad_format.py` == 1
  **safety_gates:**
    - L1: no `--no-verify`, no force
    - L2: deny-list `runtime/sandbox.py`, `runtime/worker_spawn.py`, `agent/safety/*` (security-critical, frozen)
    - L3: branch isolation — integration/dag_planner_bmad_compat only

- **id:** B2
  **title:** Wire DagPlanner к BMad parser + integration tests (FINAL)
  **surface:** backend-python
  **spec_section:** 170-230
  **depends_on:** [B1]
  **destructive_actions:** []
  **checkpoint:** false
  **estimated_retries_allowed:** 3
  **acceptance:**
    - `DagPlanner._load()` использует `parse_sprint_status_bmad()` вместо встроенного yaml parsing
    - `DagPlanner.find_ready()` returns stories with правильным id+epic_id+title для real Odyssey (current epic-3)
    - epic_id derived from filename when missing in sprint-status
    - Story title из frontmatter `_bmad/stories/X.Y.md` via existing `parse_story_md`
    - Backward compat: existing legacy fixture tests continue passing
    - `tests/test_b2_dag_planner_bmad_integration.py` — 10 tests + Odyssey golden fixture (snapshotted)
    - `pytest tests/ -q` — 785 PASS (775 baseline + 10 new); ruff/mypy clean
    - Manual probe success: `python -c "from bmad_orchestrator.runtime.dag_planner import DagPlanner; p = DagPlanner.from_target(); print([(s['id'], s.get('title','')[:40]) for s in p.find_ready(max_n=5)])"` — выдаёт current ready stories из Odyssey (epic-3) с titles, не "no title"
  **safety_gates:**
    - L1: no force/no-verify
    - L2: deny-list freeze
    - L3: branch isolation; final session — manual merge через human review (Auto merge=false)

### Current
(none — next wake promotes B1 from Pending)

### Completed
(none)

## Safety Gates Triggered
(none yet)

## Blockers / Pauses
(none yet)

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

## Journal

[2026-05-17 bootstrap] bootstrap: tracker + backup + integration branch созданы, 2 sessions planned, runtime=loop_wrapper, delay=300s, auto_merge=false
