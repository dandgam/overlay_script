# Story real-5: Extract _read_halt_reason into runtime/halt_utils.py

**Epic:** orchestrator-refactor
**Status:** ready-for-dev
**Level:** medium
**Tags:** refactor, runtime

## Acceptance Criteria

1. New module `runtime/halt_utils.py` exports `read_halt_reason(halt_path) -> str`
2. `runtime/worker_spawn.py` imports the helper instead of defining `_read_halt_reason` locally
3. Behaviour unchanged (same 512-char cap, same first-non-empty-line semantics)
4. Existing 5 halt-related unit tests still PASS
5. 2 new unit tests in `tests/test_halt_utils.py` cover empty file + multiline file

## Tasks

- [ ] AC1: Create `runtime/halt_utils.py`
- [ ] AC2: Update import in `runtime/worker_spawn.py`; delete local helper
- [ ] AC3: Add `tests/test_halt_utils.py` with 2 tests
- [ ] AC4: Run full pytest, confirm no regression

## File List

- `src/bmad_orchestrator/runtime/halt_utils.py`
- `src/bmad_orchestrator/runtime/worker_spawn.py`
- `tests/test_halt_utils.py`

## Dev Notes

Refactor + new module + test split. Multi-file, expected 1-2 iterations.
Compliance: none.
