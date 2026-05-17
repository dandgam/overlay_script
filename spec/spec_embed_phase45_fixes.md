# Spec — embed_phase45_with_selflearning fixes (round 1)

**Дата:** 2026-05-17
**Версия:** 0.1
**Базовая ветка:** `integration/embed_phase45_with_selflearning`
**Backup branch:** `backup/embed_phase45_fixes-pre-2026-05-17`
**Integration branch:** `integration/embed_phase45_fixes`
**Auto merge:** false

---

## 1. Контекст

После завершения `embed_phase45_with_selflearning` (E1-E9, 19 commits, 993 PASS) запустили 3 параллельных audit'a (security-auditor / code-auditor / code-reviewer). Все verdict: APPROVE с conditions.

**4 P0 (must-fix before merge на main):**
1. Subscribers (E5 gates, W4 merge, sweep) НЕ registered в `run_orchestrator` — production no-op
2. `WAVE_BOUNDARY_REACHED` payload missing `completed_stories` key → quarterly sweep never fires
3. `_load_policy_yaml` raw yaml.YAMLError aborts batch
4. `load_proposals_yaml` no field-name validation (defence-in-depth)

**7 P1 (should-fix):** lesson parser DoS cap, policy-apply backup, bounds guard formula, cross-process flock, save_project_memory unused, leaky private deque access, threshold-tightening escalation.

## 2. Принципы

- **P0 first**: F1 закрывает все 4 P0. Production wiring критичен.
- **No new abstractions**: только targeted fixes к existing files
- **Test coverage**: каждый fix имеет regression test
- **Preserve invariants**: deny-list (sandbox/worker_spawn) frozen; budget_guard mods только additive

## 3. Stack / Constraints

- Python 3.11+, без новых deps
- Сохранить все 993 PASS + добавить ~25-35 regression tests → ~1020-1030 PASS
- ruff + mypy --strict зелёные

## 4. Session Plan

3 сессии, surface=`backend-python`, code-only.

### F1 — P0 fixes (CHECKPOINT)

- **surface:** backend-python
- **depends_on:** []
- **checkpoint:** true
- **acceptance:**
  - **P0-1 wire subscribers:** в `agent/run.py::_run_real_pilot` после `configure_code_review_gate(...)` добавить:
    ```python
    bus.on(code_review_subscriber)
    bus.on(merge_to_integration_subscriber)
    bus.on(quarterly_sweep_subscriber)
    ```
    Symmetric wire в `_run_mock_pilot` если нужно (test-only path).
  - **P0-2 fix WAVE_BOUNDARY_REACHED payload:** оба emit sites (`agent/run.py:546-551` mock + `:794-799` real) добавить `completed_stories=len(spawned)`.
  - **P0-3 wrap yaml.YAMLError:** `runtime/lesson_parser.py:352-358` `_load_policy_yaml` → `try/except yaml.YAMLError as e: raise PolicyApplyError(...) from e`.
  - **P0-4 validate field name:** `runtime/lesson_parser.py:316-340` `load_proposals_yaml` → check `field_name in _POLICY_MODELS[policy_file].model_fields`, raise `LessonProposalInvalidError` если missing.
  - Tests: 12 regression tests
    - subscribers wired in real-mode (assert `len(bus._subs)` increase post-call)
    - `WAVE_BOUNDARY_REACHED` payload contains `completed_stories`
    - quarterly_sweep_subscriber fires когда completed_stories == 50
    - bad YAML on disk raises `PolicyApplyError`, batch continues с error entry
    - field name validation rejects "../etc/passwd"
  - `pytest tests/ -q` — 1005 PASS (993 + 12)

### F2 — P1 critical fixes (CHECKPOINT)

- **surface:** backend-python
- **depends_on:** [F1]
- **checkpoint:** true
- **acceptance:**
  - **P1-1 lesson parser DoS cap:** `runtime/lesson_parser.py` add `_MAX_LINE_LEN = 8192` + `_MAX_FILE_BYTES = 1_048_576` (1 MB); raise `LessonProposalInvalidError` on overflow в `parse_lesson_markdown` + `parse_lessons_dir`.
  - **P1-2 policy-apply backup:** в `runtime/lesson_parser.py::apply_proposal` перед `_atomic_yaml_write` write `path.with_suffix(f".yaml.bak-{ts}")` (keep last 3 backups, rotate older). New CLI `bmad-orchestrator policy-rollback <project> <proposal-id>`.
  - **P1-3 bounds guard formula:** `runtime/live_tuning.py:75-86` → use `max(abs(current), _EPS)` (rejects 0.5→0.8 as 60%); update tests.
  - **P1-5 save_project_memory call:** в `agent/run.py::_run_real_pilot` после wave_boundary emit добавить `save_project_memory(slug, fresh_medians)` (использует `budget.recent_*`).
  - **P1-7 threshold-tightening escalation:** `runtime/live_tuning.py::_apply_live_tuning` → если direction = tighten (P0-threshold ↑) → escalate HUMAN_QUERY вместо silent apply.
  - Tests: ~18 regression tests
  - `pytest tests/ -q` — 1023 PASS (1005 + 18)

### F3 — P1 polish + P2 nice-to-haves (FINAL)

- **surface:** backend-python
- **depends_on:** [F2]
- **checkpoint:** false
- **acceptance:**
  - **P1-4 flock guards:** add `fcntl.flock` advisory locks к `_atomic_yaml_write` в `lesson_parser.py`, `live_tuning.py`, `project_memory.py` (per-file lockfile в parent dir).
  - **P1-6 expose recent_costs accessor:** `BudgetGuard.recent_story_costs() -> tuple[Decimal, ...]`, use в `agent/run.py:786` вместо private `_recent_story_costs`.
  - **P2-1 .bmad-version recompute:** `skill_update.py:415-426` after swap recompute `skills_count` + `skills` list from `current_upstream` directory.
  - **P2-2 bool exclude:** `budget_guard.py:85` → `isinstance(x, (int, float)) and not isinstance(x, bool)`.
  - **P2-3 narrow excepts:** `agent/run.py` lines 1111, 1141, 1207, 1924, 1958 — each broad `except Exception` → narrow at least one explicit class.
  - Tests: ~10 regression tests
  - `pytest tests/ -q` — 1033 PASS (1023 + 10)
  - ruff/mypy clean
  - Manual merge через human review (Auto merge=false)

## 5. Acceptance — initiative level

После F3 merge:
- ✅ Все 4 P0 закрыты (subscribers wired, payload fixed, yaml errors caught, field validated)
- ✅ Все critical P1 закрыты (DoS cap, backup, bounds, flock, save_project_memory, accessor)
- ✅ Most P1+P2 polish landed
- ✅ ~1033 PASS, ruff/mypy clean
- ✅ Embed initiative готова к production pilot

---

**End of spec v0.1.**
