# Story real-3: Bump pydantic minimum version in pyproject.toml

**Epic:** orchestrator-deps
**Status:** ready-for-dev
**Level:** easy
**Tags:** deps, low-risk

## Acceptance Criteria

1. `pyproject.toml` `dependencies` entry for `pydantic` reads `>=2.6` (was `>=2.0`)
2. No imports change; existing tests still pass
3. `ruff check` clean

## Tasks

- [ ] AC1: Update version constraint in `pyproject.toml`
- [ ] AC2: Confirm `pip install -e .` resolves
- [ ] AC3: Run pytest once to confirm green

## File List

- `pyproject.toml`

## Dev Notes

Single-line edit; tests should be unaffected. Real-mode dep bump scenario.
