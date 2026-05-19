# Story real-6: Add COST_SNAPSHOT_RECORDED event type

**Epic:** orchestrator-events
**Status:** ready-for-dev
**Level:** medium
**Tags:** runtime, events

## Acceptance Criteria

1. New `COST_SNAPSHOT_RECORDED` enum value in `runtime/event_loop.py:EventType`
2. Inventory tests (`test_canonical_patches_p6.py::test_event_type_inventory_count*` and `test_s3_runtime.py::test_event_loop_has_all_spec_types`) updated to reflect new count (+1)
3. Event documented in `spec/spec_master_orchestrator.md` event inventory table
4. No emitter wired yet (event is reserved for future R3 per-turn token snapshot)

## Tasks

- [ ] AC1: Append `COST_SNAPSHOT_RECORDED = "cost_snapshot_recorded"` to EventType StrEnum
- [ ] AC2: Bump count assertions in both inventory tests by 1
- [ ] AC3: Add row to event table in master spec
- [ ] AC4: Run full pytest; only the two inventory tests should change behaviour

## File List

- `src/bmad_orchestrator/runtime/event_loop.py`
- `tests/test_canonical_patches_p6.py`
- `tests/test_s3_runtime.py`
- `spec/spec_master_orchestrator.md`

## Dev Notes

Reservation-only change: no subscriber, no emitter. Future story will wire R3 per-turn
snapshot subscriber that emits this event. Keeping the enum stable before consumers
land lets early integrators reference the name.
Compliance: none.
