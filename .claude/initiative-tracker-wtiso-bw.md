# Initiative Tracker — Q-260527-WTISO-BW (bwrap + prlimit + cgroup + env/net allowlist + HOME overlay)

## Metadata

- **Spec:** spec/spec_wtiso-bw.md
- **Slug:** wtiso-bw
- **Parent Q-NNN:** Q-260527-WTISO (umbrella)
- **Phase:** 2.5 implementer
- **Tier:** M (sub-Q under L-tier umbrella) — severity_classifier verdict L
- **security_critical:** true
- **Backup branch:** backup/wtiso-bw-pre-2026-05-27
- **Integration branch:** integration/wtiso-bw
- **Bootstrap date:** 2026-05-27
- **Runtime:** loop_wrapper
- **Delay seconds:** 300
- **Auto merge:** false
- **Sessions planned:** 6
- **Architect handoff:** §4fx methodology-888.md (gate-passed 2026-05-27T06:30Z, edge-case-hunter v3 PASS-PARTIAL)

## Current

- **id:** S2
- **title:** Per-worker overlay preparation (eager copy + concurrent safety)
- **surface:** backend-python
- **spec_section:** §3.2
- **acceptance:** _prepare_overlays master snapshot under flock; per-worker fast copy; M2 + M4 + AC3 GREEN
- **depends_on:** S1
- **destructive_actions:** []
- **retry_count:** 0

## Pending

- **id:** S3
  - **title:** cgroup wrap + NoSandbox fallback policy
  - **surface:** backend-python
  - **spec_section:** §3.4
  - **acceptance:** systemd-run --user --scope integration; BMAD_REQUIRE_CGROUP + BMAD_ALLOW_NOSANDBOX handling; M1 + M5 GREEN
  - **depends_on:** S2
  - **destructive_actions:** []
- **id:** S4
  - **title:** bash glue _spawn_worker_isolated + env clearenv
  - **surface:** mixed
  - **surface_rationale:** bash _spawn_worker_isolated ~150 LOC + Python sandbox.py integration ~100 LOC + RED test wiring ~80 LOC, each ≥20% of session work
  - **spec_section:** §3.3 + §5
  - **acceptance:** new helper в ~/.claude/skills/888/scripts/888-batch.sh; env allowlist (10 vars from sandbox.py:43-51); M3 + AC4 + AC5 GREEN
  - **depends_on:** S3
  - **destructive_actions:** []
- **id:** S5
  - **title:** Audit event schema (4 new types) + sev-5 RED tests (AC6-AC10)
  - **surface:** backend-python
  - **spec_section:** §6 + §8
  - **acceptance:** 4 new event types с schema_version="1"; AC6-AC10 GREEN
  - **depends_on:** S4
  - **destructive_actions:** []
- **id:** S6
  - **title:** Open Q resolution + bmad-code-review + bmad-security-review + retro
  - **surface:** mixed
  - **surface_rationale:** review skill invocations via Agent ~40% + Open Q decision documentation ~30% + retro markdown ~30%
  - **spec_section:** §11 + §11.1 + §12
  - **acceptance:** 9 Open Q resolved; bmad-code-review 3 hunters PASS; bmad-security-review 4 hunters PASS; methodology §4xx retro entry
  - **depends_on:** S5
  - **destructive_actions:** []

## Completed

- **id:** S1
  - **title:** `wrap` subcommand scaffold + version assertion
  - **completed_at:** 2026-05-27
  - **acceptance_met:** argparse CLI added (subcommand `wrap` with §4.1 signature); `_assert_bwrap_version_floor` + `_read_bwrap_version_floor` + `_parse_bwrap_version` + `_allow_nosandbox` helpers landed; hard-fail exit 78 path + audit event `bwrap_version_floor_failed`; M0 baseline GREEN (`evals/baselines/wtiso-bw-baseline-2026-05-27.json` matching §8.1 schema); AC2 GREEN (mock bwrap 0.5.0 stub → exit 78 + audit verified).
  - **deferred_items:** systemd-run cgroup composition (S3 scope); overlay snapshot prep (S2 scope); env clearenv allowlist (S4 scope); 4 audit event types schema_version="1" beyond bwrap_version_floor_failed (S5 scope); CLI invocation through `_spawn_worker_isolated` bash glue (S4 scope).
  - **notes:** Version floor honours `BMAD_BWRAP_MIN_VERSION` env CAN-ONLY-RAISE (edge-case-hunter HIGH F9 — mirror `_MIN_NPROC` precedent). `--allow-nosandbox=1` / `BMAD_ALLOW_NOSANDBOX=1` / `BMAD_SANDBOX=none` all trigger force-sequential warn + execvp without isolation. Version-pass path delegates to `BwrapSandbox.wrap_command` so S1 leaves the spawn pipeline functional even pre-S4.

## Journal

- [2026-05-27] Bootstrap via /auto-loop-spec-long, delay=300s, runtime=loop_wrapper, auto_merge=false. backup/wtiso-bw-pre-2026-05-27 + integration/wtiso-bw created. Spec §14 Session Plan appended (6 sessions). Sourced from architect handoff §4fx + spec_wtiso-bw.md 507 LOC + threat-model_wtiso-bw.md 236 LOC + 15 RED stubs в ~/.claude/skills/888/scripts/tests/wtiso-bw/.
- [2026-05-27] S1 done. sandbox.py +260 LOC: argparse CLI + version floor (≥0.6.0 default, env override CAN-ONLY-RAISE) + EXIT_SANDBOX_UNAVAILABLE=78 + audit emit. Tests: M0 baseline runner writes evals/baselines/wtiso-bw-baseline-2026-05-27.json; AC2 mock-bwrap-0.5.0 → exit 78 + audit verified. 60 existing sandbox tests pass (no regression). ruff clean. runtime=loop_wrapper → wrapper handles next iteration; no ScheduleWakeup.

## Blockers / Pauses

(none)

## Final Report

(empty)
