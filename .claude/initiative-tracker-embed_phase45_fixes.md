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

- **id:** F3
  **title:** P1 polish + P2 nice-to-haves (FINAL)
  **surface:** backend-python
  **spec_section:** 140-180
  **depends_on:** [F2]
  **destructive_actions:** []
  **checkpoint:** false
  **estimated_retries_allowed:** 3
  **acceptance:**
    - P1-4: `fcntl.flock` advisory locks к `_atomic_yaml_write` (3 файла)
    - P1-6: `BudgetGuard.recent_story_costs() -> tuple[Decimal, ...]` accessor, replace private `_recent_story_costs` usage в `agent/run.py`
    - P2-1: `skill_update.py:415-426` recompute `.bmad-version` skills count + list after swap
    - P2-2: `budget_guard.py:85` exclude bool из `isinstance` check
    - P2-3: narrow at least one `except Exception` per site (`agent/run.py` 5 sites)
    - 10 regression tests
    - `pytest tests/ -q` — 1033 PASS (1023 + 10); ruff/mypy clean
    - Manual merge через human review (Auto merge=false)

### Current

- **id:** F2
  **title:** Critical P1 fixes — DoS cap, backup, bounds, save_project_memory, threshold escalation (CHECKPOINT)
  **surface:** backend-python
  **spec_section:** 95-135
  **depends_on:** [F1]
  **destructive_actions:**
    - `policy-rollback <project> <proposal-id>` mutates skills/policy/ files (restore from backup)
  **checkpoint:** true
  **estimated_retries_allowed:** 3
  **started:** 2026-05-17 16:02 UTC
  **workflow:** workflows/backend-python.md (adapted — orchestrator-runtime edits)
  **retry_count:** 0
  **worker_branches:** []
  **acceptance:**
    - P1-1: `runtime/lesson_parser.py` `_MAX_LINE_LEN=8192` + `_MAX_FILE_BYTES=1_048_576` + overflow raises `LessonProposalInvalidError`
    - P1-2: `apply_proposal` writes `.yaml.bak-<ts>` (keep last 3), new CLI `policy-rollback`
    - P1-3: `runtime/live_tuning.py:75-86` bounds → `max(abs(current), _EPS)`, update existing tests
    - P1-5: `agent/run.py::_run_real_pilot` after wave_boundary → `save_project_memory(slug, fresh_medians)`
    - P1-7: `_apply_live_tuning` если direction = tighten → emit HUMAN_QUERY + skip silent apply
    - 18 regression tests
    - `pytest tests/ -q` — 1024 PASS (1006 + 18); ruff/mypy clean

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

## Safety Gates Triggered
(none yet)

## Blockers / Pauses
(none yet)

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

## Journal

[2026-05-17 bootstrap] bootstrap: tracker + backup + integration branch созданы, 3 sessions planned (F1=P0, F2=critical P1, F3=polish), runtime=loop_wrapper, delay=300s
[2026-05-17 wake] F1 promoted Pending→Current; starting P0 fixes work
[2026-05-17 16:02 UTC] F1 execution: P0-1 wired 3 subscribers via partial; P0-2 added completed_stories к WAVE_BOUNDARY_REACHED payload (mock + real); P0-3 wrapped _load_policy_yaml в PolicyApplyError; P0-4 validated field_name в load_proposals_yaml
[2026-05-17 16:02 UTC] F1 tests: 13 regression tests passing (12 spec'd + 1 sanity), full suite 1006 PASS, ruff/mypy clean
[2026-05-17 16:02 UTC] F1 completed → Completed, F2 promoted to Current; commit 5dfcfa689eb6a72c5f2607e9260c2c8983477478
