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

- **id:** S5
- **title:** Audit event schema (4 new types) + sev-5 RED tests (AC6-AC10)
- **surface:** backend-python
- **spec_section:** §6 + §8
- **acceptance:** 4 new event types с schema_version="1"; AC6-AC10 GREEN
- **depends_on:** S4
- **destructive_actions:** []
- **retry_count:** 0

## Pending

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
- **id:** S2
  - **title:** Per-worker overlay preparation (eager copy + concurrent safety)
  - **completed_at:** 2026-05-27
  - **acceptance_met:** `_prepare_overlays_master` создаёт ONE master snapshot под flock на `<source_home>/.batch-snapshot.lock` (idempotent через `.snapshot.sha256` cache, emits `master_snapshot_taken` audit с tree hash для AC5). `_prepare_overlay_for_worker` делает fast `/bin/cp -R` от master в `/tmp/888-bat-<bid>/overlays/<q-id>/`. `--overlay-mode`/`--overlay-source` wired в `_cmd_wrap` (только `copy` mode this session; bind/overlayfs raise per spec §10). M2 GREEN (sibling worktree unreachable via `--tmpfs /tmp` + `--bind {wt_abs}` scope × 3 iter). M4 GREEN (orchestrator state blackouted via `--tmpfs` over `_orchestrator_state` × 3 iter). AC3 GREEN (synthetic source_home → master snapshot → per-worker overlay → worker write `$HOME/.claude/canary` → host untouched + overlay contains × 3 iter).
  - **deferred_items:** cgroup wrap composition (S3 scope); bash `_spawn_worker_isolated` integration (S4); audit event schema-version migrations for remaining 3 event types ` sandbox_violation_blocked / sandbox_fallback_nosandbox / cgroup_tasks_max_hit` (S5); bind / overlayfs modes (deferred per spec §10 defer table); boot-time wiring of `_sweep_stale_overlays` в dispatcher (S4 hook into 888-batch.sh).
  - **notes:** Helpers idempotent: re-invocation на existing batch_dir не re-copies (hash file + dir existence checks). `_compute_overlay_tree_sha256` walks files sorted by relpath, hashes (relpath, content) pairs — symlinks skipped. flock fallback graceful: if lockfile touch fails (read-only home), proceed без lock с warning. `__all__` exports include the 4 underscore-prefixed helpers (callable from worker_spawn / 888-batch.sh integration code in S4). 60 existing sandbox tests pass (no regression).
- **id:** S3
  - **title:** cgroup wrap + NoSandbox fallback policy
  - **completed_at:** 2026-05-27
  - **acceptance_met:** sandbox.py +286 LOC: `_cmd_wrap` composes `systemd-run --user --scope --unit=bmad-worker-<base>-<pid>-<ms>.scope -p MemoryMax/CPUQuota/TasksMax --` prefix in front of prlimit+bwrap pipeline (deterministic unit name so post-exec poller can locate `pids.events`); 3 new env override knobs (`BMAD_SANDBOX_CGROUP_TASKS_MAX/MEMORY_MAX/CPU_QUOTA`) + `BMAD_SANDBOX_CGROUP=0` opt-out + `--no-cgroup` CLI flag; precedence guards `policy_conflict_resolved_to_require` (REQUIRE_SANDBOX+ALLOW + REQUIRE_CGROUP+CGROUP=0) и `sandbox_required_but_disabled` (BMAD_SANDBOX=none без ALLOW) + `cgroup_unavailable_fallback_prlimit` warn + `cgroup_required_but_unavailable` hard-fail; background poller thread polls `/sys/fs/cgroup/.../<unit>.scope/pids.events` @50ms, emits `cgroup_tasks_max_hit` audit on max>0. M1 GREEN (BMAD_SANDBOX_CGROUP_TASKS_MAX=5 + bash forks 30 /bin/true → audit `cgroup_tasks_max_hit{max_events_observed: ~18}` × 3 iter). M5 GREEN (wrap-level 3-path policy verification: bwrap-missing+no-ALLOW → exit 78; bwrap-missing+ALLOW=1 → exit 0 + audit `sandbox_fallback_nosandbox` + stderr "primary safety теряется"; BMAD_SANDBOX=none+no-ALLOW → exit 78 + audit `sandbox_required_but_disabled` × 3 iter each). M0/AC2/M2/M4/AC3 regression GREEN under new cgroup default. 60 existing sandbox unit tests pass.
  - **deferred_items:** Full 4 audit event types schema versioning + tools/audit-migrate.py (S5 scope per spec §6.5); bash `_spawn_worker_isolated` integration (S4); env clearenv allowlist propagation (S4 §3.3); AC4 (NoSandbox fallback handles at batch level) + AC5 (sequential-mode byte-identical events.jsonl) → S4 batch dispatcher level; AC6-AC10 sev-5 RED stubs (test-docker-group-assertion / test-fuse-mount-blocked / test-die-with-parent-orphan / test-uid-namespace-unshare / test-unprivileged-userns-clone) → S5.
  - **notes:** `_allow_nosandbox` deviation от spec §3.4.2 row 6 — dropped `BMAD_SANDBOX=none` alias-for-ALLOW (M5 outcome metric authoritative; silent backend swap must NOT bypass primary safety). §11.1 Open Q #9 (flag precedence enforcement) resolved at sandbox subcommand boundary — single source of truth; future flag additions get explicit row in §5.1 matrix + sandbox.py guard. cgroup post-exec audit emission path is best-effort: pids.events disappears когда systemd reaps scope, poller thread runs daemon @50ms catching the transient window. subprocess.Popen replaces os.execvp ONLY when cgroup_prefix non-empty (zero overhead on hosts без user systemd). Verification debt: spec §3.4.2 row 6 «alias» text should be updated в S6 docs to reflect ALLOW-explicit semantics. `__all__` exports 7 new S3 helpers (callable from S4 bash glue + worker_spawn integration). commit 053df7d (sandbox.py) + 78b3689 (888 tests).
- **id:** S4
  - **title:** bash glue _spawn_worker_isolated + env clearenv
  - **completed_at:** 2026-05-27
  - **acceptance_met:** ~/.claude/skills/888/scripts/888-batch.sh +226 LOC: new `_spawn_worker_isolated` helper wraps `claude -p` через `python -m bmad_orchestrator.runtime.sandbox wrap` (spec §3.3 Option C); new `_spawn_worker_dispatch` branching per spec (`BATCH_PARALLEL_ENABLED=1 && BMAD_SANDBOX != none` → isolated path; иначе → unmodified `_spawn_worker`); `_run_parallel_groups` guard force-sequentials под BMAD_ALLOW_NOSANDBOX=1 OR BMAD_SANDBOX=none (max_parallel=1 + audit `sandbox_fallback_nosandbox{operator_opt_in}`, spec §3.4.2 row 5 + Critical Boundary §5); 3 call-sites switched. Wrap CLI composition: --worktree + --network + --allow-nosandbox + --overlay-mode copy + --overlay-source `<batch>/overlays/<q-id>` + --env BMAD_CURRENT_WAVE + -- claude -p `<prompt>`. M3 GREEN × 3 iter (ANTHROPIC_API_KEY + CLAUDE_OAUTH_REFRESH canary exported → env dump → 0 leaks через --clearenv + 10-var allowlist sandbox.py:44-52). AC4 GREEN trip-path (A hard-fail exit 78 / B opt-in execvp + 'primary safety теряется' stderr warn / C batch dispatcher force-sequential warn). AC5 GREEN × 3 iter (2 mock runs `_spawn_worker_dispatch` под PARALLEL=0 + SANDBOX=none → unmodified `_spawn_worker` → events.jsonl structurally identical после ts+epoch+mock-rand strip; v3 reframe per edge-case-hunter H#11). M0/AC2/M1/M2/M4/M5/AC3 regression GREEN. 62 sandbox unit tests pass.
  - **deferred_items:** AC6-AC10 sev-5 RED stubs (docker-group / fuse-mount / die-with-parent-orphan / uid-namespace-unshare / unprivileged-userns-clone) → S5; full schema_version="1" coverage за пределы emitted-as-needed (4 new event types catalog + tools/audit-migrate.py per spec §6.5) → S5; 9 Open Q resolution + docs sync (§3.4.2 row 6 «alias» text update) → S6.
  - **notes:** Mock-mode bypass в `_spawn_worker_isolated` (early-return → `_spawn_worker`) сохраняет деterministic mock event ordering (AC5 invariant). Sequential AC5 strip includes `mock[0-9a-f]+` regex (RANDOM-derived commit sha) и `.cost?` payload key (mock cost identical, but defensive strip). `_run_parallel_groups` guard fires при ALLOW=1 даже без BMAD_SANDBOX=none — operator opt-in alone forces sequential, не нужно double-flag. AC4 Path A check теперь относится stderr signal ("bwrap not found"), а не jsonl scan — audit emission path в headless invocation идёт в stderr через `_emit_sandbox_audit` стандартный channel. commit 35177ba в ~/.claude/skills/888.

## Journal

- [2026-05-27] Bootstrap via /auto-loop-spec-long, delay=300s, runtime=loop_wrapper, auto_merge=false. backup/wtiso-bw-pre-2026-05-27 + integration/wtiso-bw created. Spec §14 Session Plan appended (6 sessions). Sourced from architect handoff §4fx + spec_wtiso-bw.md 507 LOC + threat-model_wtiso-bw.md 236 LOC + 15 RED stubs в ~/.claude/skills/888/scripts/tests/wtiso-bw/.
- [2026-05-27] S1 done. sandbox.py +260 LOC: argparse CLI + version floor (≥0.6.0 default, env override CAN-ONLY-RAISE) + EXIT_SANDBOX_UNAVAILABLE=78 + audit emit. Tests: M0 baseline runner writes evals/baselines/wtiso-bw-baseline-2026-05-27.json; AC2 mock-bwrap-0.5.0 → exit 78 + audit verified. 60 existing sandbox tests pass (no regression). ruff clean. runtime=loop_wrapper → wrapper handles next iteration; no ScheduleWakeup.
- [2026-05-27] S2 done. sandbox.py +292 LOC: 5 overlay helpers (`_prepare_overlays_master` under flock + `_prepare_overlay_for_worker` fast cp -R + `_cleanup_overlays` + `_sweep_stale_overlays` + `_compute_overlay_tree_sha256`); `--overlay-mode`/`--overlay-source` wired into `_cmd_wrap` (copy-only this session); 4 `__all__` exports added. Tests in `~/.claude/skills/888/scripts/tests/wtiso-bw/` committed at 115c95e: M2 cross-worker FS GREEN, M4 orch state GREEN, AC3 HOME overlay containment GREEN (all × 3 flake3-runs iter). AC2 regression GREEN. 60 existing sandbox tests pass. ruff clean. commit cc1fac6. runtime=loop_wrapper → wrapper handles next iteration; no ScheduleWakeup.
- [2026-05-27] S3 done. sandbox.py +286 LOC: cgroup wrap composition via systemd-run --user --scope (deterministic unit name) + 3 cgroup env overrides + `--no-cgroup` opt-out + §5.1 precedence matrix guards (`policy_conflict_resolved_to_require` + `sandbox_required_but_disabled` + `cgroup_unavailable_fallback_prlimit` + `cgroup_required_but_unavailable`) + background poller thread emitting `cgroup_tasks_max_hit` on pids.events:max>0; `_allow_nosandbox` dropped BMAD_SANDBOX=none alias (M5 authoritative). Tests at ~/.claude/skills/888 78b3689: M1 (fork-bomb cgroup ceiling × 3 GREEN; audit cgroup_tasks_max_hit with max_events_observed=18 typical) + M5 (3-path NoSandbox policy × 3 GREEN; PATH-scrub via symlink farm). S1+S2 regression all GREEN. 60 existing sandbox tests pass. ruff clean. commit 053df7d (sandbox) + 78b3689 (tests). runtime=loop_wrapper → wrapper handles next iteration; no ScheduleWakeup.
- [2026-05-27] S4 done. ~/.claude/skills/888/scripts/888-batch.sh +226 LOC: `_spawn_worker_isolated` (wraps claude -p через python -m sandbox wrap) + `_spawn_worker_dispatch` (parallel+sandbox!=none branch) + `_run_parallel_groups` force-sequential guard (ALLOW=1 OR SANDBOX=none → max_parallel=1, spec §3.4.2 row 5). 3 call-sites switched. M3 GREEN × 3 (env canary exfil 0 leaks через --clearenv). AC4 GREEN trip-path (hard-fail + opt-in execvp + batch dispatcher). AC5 GREEN × 3 (sequential events.jsonl structural identity, v3 reframe). 7 prior wtiso-bw tests + 62 sandbox unit tests no-regression. commit 35177ba (~/.claude/skills/888). runtime=loop_wrapper → wrapper handles next iteration; no ScheduleWakeup.

## Blockers / Pauses

(none)

## Final Report

(empty)

## Post-Merge Handoff к /888 Phase 3

> Заполняется S6 при write Final Report. Содержит готовую команду для user после manual merge.

(заполнится S6)

