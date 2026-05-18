# Story 1.2: Add `--verbose` flag to CLI

**Epic:** epic-1
**Status:** ready-for-dev
**Level:** easy

## Acceptance Criteria

1. CLI accepts `--verbose` boolean flag
2. When set, logger level switches to DEBUG
3. Default behaviour (INFO level) unchanged

## Tasks

- [ ] AC1: Add `--verbose` to typer command signature
- [ ] AC2: When True, call `logging.basicConfig(level=logging.DEBUG)`
- [ ] AC3: 1 unit test asserts logger level changes

## File List

- `src/main.py`
- `tests/test_main_verbose.py`

## Dev Notes

Single-file feature + matching test. Standard pattern.
