# Story real-8: Bump default worker timeout from 1800s → 3600s in Settings

**Epic:** orchestrator-runtime
**Status:** ready-for-dev
**Level:** medium
**Tags:** runtime, config

## Acceptance Criteria

1. `Settings.worker_timeout_sec` default changes 1800 → 3600
2. Env override `ORCHESTRATOR_WORKER_TIMEOUT_SEC` still wins (no regression)
3. Existing tests that hard-code 1800 are updated to assert the new default
4. CHANGELOG entry under "Changed" notes the bump + rationale
5. 2 new unit tests in `tests/test_settings_defaults.py` (default value + env override clamp)

## Tasks

- [ ] AC1: Edit `Field(default=3600, ...)` in `runtime/config.py`
- [ ] AC2: Grep for `1800` occurrences in tests; update those covering this default
- [ ] AC3: Add 2 tests in `tests/test_settings_defaults.py`
- [ ] AC4: Append CHANGELOG line under v-next "Changed"

## File List

- `src/bmad_orchestrator/runtime/config.py`
- `tests/test_settings_defaults.py`
- `CHANGELOG.md`
- (any existing test file pinning 1800 for this setting)

## Dev Notes

Trivial value change + grep sweep. Risk: missing a hard-coded 1800 in tests causes
unrelated failures. Reviewer should grep `1800` once after the change.
Compliance: none.
