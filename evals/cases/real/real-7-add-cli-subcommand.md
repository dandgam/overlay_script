# Story real-7: Add `bmad-orchestrator doctor` CLI subcommand

**Epic:** orchestrator-cli
**Status:** ready-for-dev
**Level:** medium
**Tags:** cli, runtime

## Acceptance Criteria

1. `bmad-orchestrator doctor` prints venv path, bwrap availability, anthropic SDK version
2. Exit code 0 when all 3 probes succeed, 1 otherwise
3. `--json` flag emits machine-readable output `{venv, bwrap, anthropic_sdk}`
4. 4 unit tests cover: all-green, missing bwrap (mocked), human + json output, exit code mapping

## Tasks

- [ ] AC1: Add `doctor` typer command in `cli/main.py`
- [ ] AC2: Implement `_probe_venv`, `_probe_bwrap`, `_probe_anthropic_sdk` helpers
- [ ] AC3: Add `tests/test_cli_doctor.py` with 4 tests
- [ ] AC4: ruff + mypy clean on touched files

## File List

- `src/bmad_orchestrator/cli/main.py`
- `tests/test_cli_doctor.py`

## Dev Notes

Single-file CLI extension + new test module. No state mutation, no I/O beyond
subprocess.run for bwrap probe. Expected 1-2 iterations (test-naming nits possible).
Compliance: none.
