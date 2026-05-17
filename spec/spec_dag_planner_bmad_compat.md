# Spec — DagPlanner BMad-format compatibility

**Дата:** 2026-05-17
**Версия:** 0.1
**Базовая ветка:** `main` (post-merge f312e3c — --story flag)
**Backup branch:** `backup/dag_planner_bmad_compat-pre-2026-05-17`
**Integration branch:** `integration/dag_planner_bmad_compat`
**Auto merge:** false

---

## 1. Контекст

`DagPlanner.from_target()` сейчас НЕ парсит реальный BMad-формат sprint-status.yaml (используемый в `/home/server/odyssey/`):

**Что Odyssey пишет в sprint-status:**
```yaml
epic-3: in-progress
3-1-lifecycle-state-machine: done  # 2026-05-17: manual override...
3-2: in-progress NEEDS-FIX via bmad-code-review
3-3: ready-for-dev
```

**Что DagPlanner ожидает (из W1 + scaffold-time предположений):**
```yaml
epics:
  3:
    status: in-progress
    stories:
      3.1: done
      3.2: in-progress
      3.3: ready-for-dev
```

**Разница:**
1. Story IDs — kebab-case (`3-1-lifecycle-state-machine`) vs dotted (`3.1`)
2. Status строки имеют embedded prose comments (`NEEDS-FIX via bmad-code-review`) — не plain enum
3. Epics → stories — flat top-level keys vs nested `epics: {N: {stories: {...}}}`
4. Multiple status terms: `ready-for-dev`, `in-progress`, `in-progress NEEDS-FIX`, `done`, `review`

Проявление: `DagPlanner.find_ready()` на Odyssey возвращает **stories из epic-1** (давно done) и игнорирует current epic-3. Зафиксировано 2026-05-17 при подготовке smoke pilot.

`--story <id>` flag (commit f312e3c) unblock'ит pilot на одной story manually. Но для production использования (auto-pick ready, dependency resolution, parallel safety) нужно починить parser.

---

## 2. Принципы

- **Read upstream BMad как source of truth.** Layout/schema который Odyssey использует — это что real-world BMad maintainers пишут. Orchestrator должен подстраиваться.
- **Tolerant parsing.** Embedded prose comments в status строках — нормальная BMad practice (`done  # 2026-05-17: manual override...`). Parser должен извлекать первое токен-слово (`done`) и игнорировать остальное.
- **Story ID нормализация.** Принимать оба: `3-1-lifecycle-state-machine` ↔ `3.1`. Каноничная форма — dotted (matches stories/X.Y.md filenames).
- **Two-source ground truth.** Story metadata (epic_id, title, depends_on, touches_files) парсится из `_bmad/stories/X.Y.md` frontmatter. Sprint-status даёт только status flag.
- **Defensive defaults.** Unknown status → treated as `backlog` + warning log. Не падать на новых BMad-статусах.

---

## 3. Stack / Constraints

- Python 3.11+, без новых deps (yaml + re уже в pyproject)
- Сохранить все 760 PASS из main + добавить ~20-25 тестов на BMad-format parsing
- ruff + mypy --strict зелёные
- Не модифицировать `runtime/worker_spawn.py`, `runtime/sandbox.py`, `agent/safety/*` (security-critical, frozen)

---

## 4. Session Plan

2 сессии, surface=`backend-python`, code-only.

### B1 — BMad-format sprint-status parser (CHECKPOINT)

- **surface:** backend-python
- **spec_section:** 100-160
- **depends_on:** []
- **destructive_actions:** []
- **checkpoint:** true
- **acceptance:**
  - Создать `src/bmad_orchestrator/runtime/bmad_format.py` с функциями:
    - `parse_sprint_status_bmad(yaml_data: dict) → SprintStatus` где `SprintStatus = {epics: {epic_id: {status, stories: {story_id: status}}}}`
    - `normalize_story_id(raw: str) → str` (`3-1-lifecycle-state-machine` → `3.1`, `3.2` → `3.2`)
    - `extract_status_token(value: str) → str` (`"done  # 2026-05-17: manual"` → `"done"`)
  - Парсер принимает **обе схемы**: legacy (orchestrator scaffold's `epics: {N: {stories: {...}}}`) И upstream BMad (flat top-level `epic-N: status`, `N-M-title: status`)
  - Detection через probe: если top-level keys содержат `^epic-\d+$` или `^\d+-\d+` → BMad format; иначе → legacy
  - Mapping story IDs к stories/X.Y.md filenames — берём first numeric.numeric из key prefix
  - Unknown status (не в whitelist) → `backlog` + structured warning
  - Tests: ~15 unit tests на parser, обе схемы, edge cases (embedded comments, multi-space, kebab vs dotted)
  - Grep: `grep -c "def parse_sprint_status_bmad" src/bmad_orchestrator/runtime/bmad_format.py` == 1
  - `pytest tests/test_b1_bmad_format.py -v` — 15 PASS
  - `pytest tests/ -q` — 775 PASS (760 baseline + 15 new)

- **safety_gates:**
  - L1: no `--no-verify`, no force
  - L2: deny-list freeze (sandbox/worker_spawn/budget_guard)
  - L3: branch isolation

### B2 — Wire DagPlanner к новому parser'у + integration tests (FINAL)

- **surface:** backend-python
- **spec_section:** 170-230
- **depends_on:** [B1]
- **destructive_actions:** []
- **checkpoint:** false
- **acceptance:**
  - `DagPlanner._load()` использует `parse_sprint_status_bmad()` вместо встроенного yaml parsing
  - `DagPlanner.find_ready()` корректно identifies `ready-for-dev` stories из BMad format
  - `epic_id` берётся из story filename (`3.2.md` → epic `3`) если в sprint-status нет explicit epic mapping
  - Story metadata (title, depends_on, touches_files) загружается из `_bmad/stories/X.Y.md` frontmatter (использует `parse_story_md` уже существующий)
  - Integration test на REAL Odyssey: `find_ready()` возвращает stories с правильными `id`, `epic_id`, `title` (не "no title")
  - Backward compat: legacy fixture-based tests (с epic-nested layout) продолжают работать
  - Tests: ~10 integration tests + Odyssey golden fixture
  - `pytest tests/ -q` — 785 PASS (775 baseline + 10 new)
  - Manual probe: `python -c "from bmad_orchestrator.runtime.dag_planner import DagPlanner; p = DagPlanner.from_target(); print([(s['id'], s.get('title','')[:40]) for s in p.find_ready(max_n=5)])"` — выдаёт реальные epic-3 stories для Odyssey

- **safety_gates:**
  - L1: no force/no-verify
  - L2: deny-list freeze
  - L3: branch isolation — manual merge в main через human review

---

## 5. Acceptance — initiative level

После B2 merge'а:
- ✅ `bmad-orchestrator run --project odyssey --wave 1a --real --max-stories 1` (без `--story`) корректно выбирает ready story из current wave
- ✅ DagPlanner понимает обе раскладки sprint-status (legacy + BMad upstream)
- ✅ Story metadata (title, deps, touches) тянется из `_bmad/stories/X.Y.md` frontmatter
- ✅ 785 PASS, ruff/mypy clean
- ✅ Unblock'ит auto-pick path для pilot — `--story` остаётся как escape-hatch для override

---

**End of spec v0.1.**
