# Story real-4: Add pydantic validator for Settings.required_mcp_tools

**Epic:** orchestrator-runtime
**Status:** ready-for-dev
**Level:** medium
**Tags:** runtime, validation

## Acceptance Criteria

1. `Settings.required_mcp_tools` rejects entries containing whitespace or empty strings via a field_validator
2. ValidationError raised at Settings construction time, not at first poll
3. Valid input (`["analyzer", "postgres-mcp"]`) still accepted
4. 3 unit tests cover: valid input, whitespace rejection, empty-string rejection

## Tasks

- [ ] AC1: Add `@field_validator("required_mcp_tools", mode="after")` in `runtime/config.py`
- [ ] AC2: Raise ValueError with descriptive message
- [ ] AC3: Add 3 tests in `tests/test_settings_validation.py`
- [ ] AC4: ruff + mypy clean

## File List

- `src/bmad_orchestrator/runtime/config.py`
- `tests/test_settings_validation.py`

## Dev Notes

Medium difficulty — touches pydantic-settings module + adds tests. Likely 1-2 review passes.
Compliance: none.
