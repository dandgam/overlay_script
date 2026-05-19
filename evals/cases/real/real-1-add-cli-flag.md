# Story real-1: Add --dry-run flag to bmad-orchestrator status command

**Epic:** orchestrator-cli
**Status:** ready-for-dev
**Level:** easy
**Tags:** cli, low-risk

## Acceptance Criteria

1. `bmad-orchestrator status --dry-run` returns exit code 0 without touching the filesystem
2. Flag is documented in `--help` output
3. Existing `status` invocation (no flag) is unchanged

## Tasks

- [ ] AC1: Add `dry_run: bool = typer.Option(False, "--dry-run")` to status command
- [ ] AC2: Short-circuit when dry_run is True; print "dry-run: no side-effects" and return
- [ ] AC3: 1 unit test covering both branches

## File List

- `src/bmad_orchestrator/cli/main.py`
- `tests/test_status_cli.py`

## Dev Notes

Real-mode case mirroring a typical single-file CLI extension in a BMad project.
Expected single-pass approve (no escalation).
