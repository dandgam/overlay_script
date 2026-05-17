# Initiative Tracker — embed_phase45 fixes (round 1)

## Metadata
- **Spec:** spec/spec_embed_phase45_fixes.md
- **Parent specs:** spec_embed_phase45_with_selflearning.md (just-completed initiative)
- **Integration branch:** integration/embed_phase45_fixes
- **Base branch:** integration/embed_phase45_with_selflearning (commit 532f10f — spec committed)
- **Backup branch:** backup/embed_phase45_fixes-pre-2026-05-17
- **Created:** 2026-05-17
- **Bootstrap completed:** 2026-05-17 by auto-loop-spec-long
- **Scope frozen:** 2026-05-17
- **Runtime:** loop_wrapper
- **Delay seconds:** 300
- **Auto merge:** false

## Scope Freeze

### In scope
- **F1 — 4 P0 fixes** (must-fix before merge на main):
  - Wire 3 subscribers (code_review_subscriber, merge_to_integration_subscriber, quarterly_sweep_subscriber) в `_run_real_pilot`
  - Add `completed_stories=len(spawned)` к WAVE_BOUNDARY_REACHED payload (2 emit sites)
  - Wrap `_load_policy_yaml` в try/except `yaml.YAMLError`
  - Validate `field_name in _POLICY_MODELS[policy_file].model_fields` в `load_proposals_yaml`
  - 12 regression tests
- **F2 — Critical P1 fixes:**
  - Lesson parser DoS cap (`_MAX_LINE_LEN=8192`, `_MAX_FILE_BYTES=1MB`)
  - `policy-apply` backup file + `policy-rollback` CLI
  - Bounds guard formula tightening (`max(abs(current), _EPS)`)
  - `save_project_memory` call after wave_boundary in production
  - Threshold-tightening escalation (HUMAN_QUERY вместо silent apply)
  - 18 regression tests
- **F3 — P1 polish + P2:**
  - fcntl flock guards к atomic YAML writes (lesson_parser, live_tuning, project_memory)
  - `BudgetGuard.recent_story_costs()` public accessor
  - `.bmad-version` recompute after swap
  - bool exclusion в `isinstance(int, float)`
  - Narrow broad `except Exception` blocks
  - 10 regression tests

### Out of scope (deferred)
- P2 items не listed выше
- Test coverage gate auto-tune (separate analysis)
- Multi-orchestrator-on-same-project deployment patterns

## Sessions

### Pending
(none — F3 promoted to Current)

### Current

- **id:** F3
  **title:** P1 polish + P2 nice-to-haves (FINAL)
  **surface:** backend-python
  **spec_section:** 140-180
  **depends_on:** [F2]
  **destructive_actions:** []
  **checkpoint:** false
  **estimated_retries_allowed:** 3
  **started:** 2026-05-17 23:25 UTC
  **workflow:** workflows/backend-python.md (adapted — orchestrator-runtime edits)
  **retry_count:** 0
  **worker_branches:** []
  **acceptance:**
    - P1-4: `fcntl.flock` advisory locks к `_atomic_yaml_write` (3 файла)
    - P1-6: `BudgetGuard.recent_story_costs() -> tuple[Decimal, ...]` accessor, replace private `_recent_story_costs` usage в `agent/run.py`
    - P2-1: `skill_update.py:415-426` recompute `.bmad-version` skills count + list after swap
    - P2-2: `budget_guard.py:85` exclude bool из `isinstance` check
    - P2-3: narrow at least one `except Exception` per site (`agent/run.py` 5 sites)
    - 10 regression tests
    - `pytest tests/ -q` — 1034 PASS (1024 + 10); ruff/mypy clean
    - Manual merge через human review (Auto merge=false)

### Completed

- **id:** F1
  **title:** P0 fixes — subscribers wiring + payload + YAML errors + field validation (CHECKPOINT)
  **completed:** 2026-05-17 16:02 UTC
  **commit:** 5dfcfa689eb6a72c5f2607e9260c2c8983477478
  **files_changed:** 5 (run.py, lesson_parser.py, test_embed_phase45_fixes_f1.py, test_w1_real_pilot.py, test_w4_code_review_gate.py)
  **tests_passed:** 1006 PASS (993 baseline + 13 new F1 tests)
  **decisions_made:**
    - P0-1: Used `functools.partial(sub, bus=bus)` cast to EventCallback to adapt (event, bus) signature to bus.on() contract. Cleaner than introducing local async wrappers; matches how callers test subscribers directly.
    - P0-1: Skipped wiring in `_run_mock_pilot` (spec only required real-mode + test-only path) — mock tests instantiate subscribers manually.
    - P0-3: Wrapped at the lowest-level read site (`_load_policy_yaml`) so all callers (apply_proposal, apply_proposals_batch) benefit uniformly.
    - P0-4: Validation inside `load_proposals_yaml` loop (not on `LessonProposal` constructor) — matches existing style where `policy_file` was already validated there.
    - Updated stale `test_w4_grep_subscribers_defined_exactly_twice` → `test_w4_subscribers_defined_and_wired` (the old test silently sanctioned the P0-1 bug it failed to catch).
    - Pre-existing ruff I001 on test_w1_real_pilot.py:290 fixed inline (sorted imports) to keep ruff clean per spec acceptance.
  **deferred_items:**
    - Symmetric wiring of subscribers in `_run_mock_pilot` — spec marked optional, mock-mode tests don't need it.

- **id:** F2
  **title:** Critical P1 fixes — DoS cap, backup, bounds, save_project_memory, threshold escalation (CHECKPOINT)
  **completed:** 2026-05-17 23:25 UTC
  **commit:** d02e698
  **files_changed:** 6 (lesson_parser.py, live_tuning.py, agent/run.py, cli/main.py, test_e6_live_tuning.py, test_embed_phase45_fixes_f2.py [new])
  **tests_passed:** 1024 PASS (1006 baseline + 18 new F2 tests)
  **decisions_made:**
    - P1-1: Encoded-length check (UTF-8 byte count) for file cap; per-line check against UTF-8 byte length not char count — DoS attacker controls bytes, not unicode runes. `parse_lessons_dir` does `stat()` BEFORE read_text to avoid loading a malicious 100 MiB file into memory just to reject it.
    - P1-2: Backup timestamp suffix doubles as `proposal_id` in audit log + as the `rollback_policy` lookup key. Format `<basename>.bak-YYYYMMDDTHHMMSSZ` is greppable, sortable, idempotent (same `apply_proposal` call twice → 2 distinct backups). Keep-last-3 prune runs after the new snapshot is written so we never accidentally delete the only backup.
    - P1-3: Reading spec literally: «drift measured against current value only». Previous formula `max(abs(current), abs(proposed), _EPS)` rescued proposals that moved the threshold a lot but landed on a larger absolute value — exactly the 0.5→0.8 case the audit flagged. New formula `max(abs(current), _EPS)` rejects that as 60% drift while still tolerating `current=0` via the EPS floor.
    - P1-5: Helper `_persist_project_memory_snapshot` is local to `agent/run.py` (not promoted to `project_memory.py`) because it knows BudgetGuard internals (`_recent_*` deques). Refreshes only fields the orchestrator owns (last_wave + 4 rolling windows + 3 medians) — preserves `success_rate`, `compliance_findings_count`, `lessons_files_count` set by other subsystems. Wrapped in try/except `ProjectMemoryError` with warn-only logging: a missing snapshot delays live-tuning convergence by a few stories at next launch but must NOT abort an otherwise-successful pilot.
    - P1-7: Direction partition done by tuple comprehension on `proposed_value > current_value` — keeps existing `apply_proposals` call path intact for the loosen subset. Each tighten emits its own HUMAN_QUERY with `verdict="live_tuning_tighten"` so user can approve/reject per metric (vs batch). YAML file remains at the pre-proposal value until human applies via the existing escalation flow.
    - 2 pre-existing E6 tests updated to match new semantics: `test_e6_evaluate_clamps_proposed_to_unit_interval` asserts within_bounds=False (was True under old formula); `test_e6_subscriber_persists_yaml_after_min_samples` rewritten to test LOOSEN path (0.8→0.7) instead of TIGHTEN (0.8→0.9) which now escalates.
    - Test infra: `ORCHESTRATOR_HOME` env var → must use double-prefix `ORCHESTRATOR_ORCHESTRATOR_HOME` because pydantic-settings adds env_prefix to field name. Caught by the failing real-pilot test on first run.
  **deferred_items:**
    - Mock-pilot wave-boundary persist not wired (spec only required real-mode); mock-mode tests construct ProjectMemory manually anyway.

## Safety Gates Triggered
(none)

## Blockers / Pauses
(none)

## Decisions Log

- **date:** 2026-05-17 (bootstrap)
  **session:** bootstrap
  **decision:** Round 1 fixes initiative — closure P0s + critical P1s before merge на main. Discovered through 3 parallel audits (security / code-auditor / code-reviewer) at completion of embed_phase45_with_selflearning.
  **rationale:** Без fixes 4 subscribers (code_review, merge, sweep) физически не работают в production — initiative silently no-op. P0-1 unblock'ит ВСЁ что E5/E6 строили. Прочие P0/P1 — defence-in-depth + edge cases которые могут привести к data corruption.
  **impact:** После F3 — embed initiative production-ready. Merge на main → real pilot готов.

- **date:** 2026-05-17 16:02 UTC
  **session:** F1
  **decision:** F1 закрыта — все 4 P0 имплементированы, 13 regression tests добавлено (1 extra sanity check on top of 12 spec'd), 1006 PASS зелёный, ruff/mypy clean.
  **rationale:** Subscriber wiring через `functools.partial` оказался cleanest path: один import + cast, не требует переписывания subscriber'ов под однопараметровый EventCallback. Bug в test_w4_grep_subscribers_defined_exactly_twice исправлен — старый assert==2 сам по себе и был тем guard'ом, который должен был поймать missing wiring (но проверял только def, не usage).
  **impact:** F2 (critical P1) разблокирован; depends_on=[F1] satisfied. Test baseline на следующий сессии = 1006 PASS.

- **date:** 2026-05-17 23:25 UTC
  **session:** F2
  **decision:** F2 закрыта — все 5 critical P1 имплементированы, 18 regression tests, 1024 PASS зелёный, ruff/mypy strict clean. 2 pre-existing E6 теста обновлены под новую семантику bounds/tighten.
  **rationale:** P1-3 (bounds formula) и P1-7 (tighten escalation) — самые поведенчески-важные: первый блокирует крупные «безопасные на вид» прыжки (0.5→0.8); второй превращает молчаливое ужесточение в человеко-контролируемую операцию. P1-2 (policy backup + rollback CLI) даёт operator выход «откатить последнее изменение политики» без git surgery. P1-1 (DoS cap) — defence-in-depth перед открытием skills/policy на user-uploaded markdown в будущем. P1-5 (memory persist) закрывает обнаруженный gap между E7 (load) и реальной production runtime — без него primed deques пустые.
  **impact:** F3 (polish + P2) разблокирован; depends_on=[F2] satisfied. Test baseline на следующую сессию = 1024 PASS. После F3 → initiative complete → manual merge на main (Auto merge=false).

## Journal

[2026-05-17 bootstrap] bootstrap: tracker + backup + integration branch созданы, 3 sessions planned (F1=P0, F2=critical P1, F3=polish), runtime=loop_wrapper, delay=300s
[2026-05-17 wake] F1 promoted Pending→Current; starting P0 fixes work
[2026-05-17 16:02 UTC] F1 execution: P0-1 wired 3 subscribers via partial; P0-2 added completed_stories к WAVE_BOUNDARY_REACHED payload (mock + real); P0-3 wrapped _load_policy_yaml в PolicyApplyError; P0-4 validated field_name в load_proposals_yaml
[2026-05-17 16:02 UTC] F1 tests: 13 regression tests passing (12 spec'd + 1 sanity), full suite 1006 PASS, ruff/mypy clean
[2026-05-17 16:02 UTC] F1 completed → Completed, F2 promoted to Current; commit 5dfcfa689eb6a72c5f2607e9260c2c8983477478
[2026-05-17 23:25 UTC] F2 execution: P1-1 lesson_parser DoS caps + LessonProposalInvalidError on overflow; P1-2 apply_proposal snapshot .yaml.bak-<ts> + keep-last-3 prune + `bmad-orchestrator policy-rollback` CLI; P1-3 _within_bounds → max(abs(current), _EPS) (rejects 0.5→0.8 as 60% drift); P1-5 _persist_project_memory_snapshot helper called after WAVE_BOUNDARY_REACHED in _run_real_pilot (warn-only on failure); P1-7 tighten direction in _apply_live_tuning escalates via HUMAN_QUERY (loosen still silent-applies)
[2026-05-17 23:25 UTC] F2 tests: 18 regression tests passing, 2 pre-existing E6 tests updated for new semantics, full suite 1024 PASS, ruff clean, mypy --strict clean on 4 modified src files
[2026-05-17 23:25 UTC] F2 completed → Completed, F3 promoted to Current; commit d02e698 on integration/embed_phase45_fixes. Runtime=loop_wrapper → no main merge, no ScheduleWakeup; wrapper handles next iteration.
