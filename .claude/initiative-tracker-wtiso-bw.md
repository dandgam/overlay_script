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

- **id:** S1
- **title:** `wrap` subcommand scaffold + version assertion
- **surface:** backend-python
- **spec_section:** §3.1 + §4
- **acceptance:** argparse CLI added; bwrap_version_floor check; M0 baseline GREEN; AC2 (version floor) GREEN
- **depends_on:** —
- **destructive_actions:** []
- **retry_count:** 0

## Pending

- **id:** S2
  - **title:** Per-worker overlay preparation (eager copy + concurrent safety)
  - **surface:** backend-python
  - **spec_section:** §3.2
  - **acceptance:** _prepare_overlays master snapshot under flock; per-worker fast copy; M2 + M4 + AC3 GREEN
  - **depends_on:** S1
  - **destructive_actions:** []
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

(empty)

## Journal

- [2026-05-27] Bootstrap via /auto-loop-spec-long, delay=300s, runtime=loop_wrapper, auto_merge=false. backup/wtiso-bw-pre-2026-05-27 + integration/wtiso-bw created. Spec §14 Session Plan appended (6 sessions). Sourced from architect handoff §4fx + spec_wtiso-bw.md 507 LOC + threat-model_wtiso-bw.md 236 LOC + 15 RED stubs в ~/.claude/skills/888/scripts/tests/wtiso-bw/.

## Blockers / Pauses

(none)

## Final Report

(empty)
