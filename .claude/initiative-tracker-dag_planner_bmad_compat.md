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
(none — B2 promoted to Current)

### Current

- **id:** B2
  **title:** Wire DagPlanner к BMad parser + integration tests (FINAL)
  **surface:** backend-python
  **spec_section:** 170-230
  **depends_on:** [B1]
  **destructive_actions:** []
  **checkpoint:** false
  **estimated_retries_allowed:** 3
  **started:** (next wake)
  **workflow:** workflows/backend-python.md
  **retry_count:** 0
  **worker_branches:** []
  **acceptance:**
    - `DagPlanner._load()` использует `parse_sprint_status_bmad()` вместо встроенного yaml parsing
    - `DagPlanner.find_ready()` returns stories with правильным id+epic_id+title для real Odyssey (current epic-3)
    - epic_id derived from filename when missing in sprint-status
    - Story title из frontmatter `_bmad/stories/X.Y.md` via existing `parse_story_md`
    - Backward compat: existing legacy fixture tests continue passing
    - `tests/test_b2_dag_planner_bmad_integration.py` — 10 tests + Odyssey golden fixture (snapshotted)
    - `pytest tests/ -q` — 798 PASS (788 baseline post-B1 + 10 new); ruff/mypy clean
    - Manual probe success: `python -c "from bmad_orchestrator.runtime.dag_planner import DagPlanner; p = DagPlanner.from_target(); print([(s['id'], s.get('title','')[:40]) for s in p.find_ready(max_n=5)])"` — выдаёт current ready stories из Odyssey (epic-3) с titles, не "no title"
  **safety_gates:**
    - L1: no force/no-verify
    - L2: deny-list freeze
    - L3: branch isolation; final session — manual merge через human review (Auto merge=false)

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

## Safety Gates Triggered
(none)

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

## Journal

[2026-05-17 bootstrap] bootstrap: tracker + backup + integration branch созданы, 2 sessions planned, runtime=loop_wrapper, delay=300s, auto_merge=false
[2026-05-17 18:50 UTC] B1 execution: bmad_format.py + 28 tests written; 788 PASS (760 baseline + 28); ruff/mypy --strict clean; commit bd6ab2a
[2026-05-17 18:50 UTC] B1 completed → Completed; B2 promoted → Current; checkpoint logged locally (Auto merge=false, no main merge)
