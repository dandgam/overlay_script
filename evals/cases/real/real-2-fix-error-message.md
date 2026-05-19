# Story real-2: Fix typo in worker_spawn error message

**Epic:** orchestrator-cleanup
**Status:** ready-for-dev
**Level:** easy
**Tags:** docs, low-risk

## Acceptance Criteria

1. Error string "worktee path missing" → "worktree path missing" in `runtime/worker_spawn.py`
2. Existing test that asserts on the message updated
3. No behavioural change

## Tasks

- [ ] AC1: grep + replace typo
- [ ] AC2: update assertion in `tests/test_worker_spawn.py`
- [ ] AC3: ruff clean

## File List

- `src/bmad_orchestrator/runtime/worker_spawn.py`
- `tests/test_worker_spawn.py`

## Dev Notes

Tiny mechanical change. Worker should approve on first pass.
