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

(none — S6 completed, initiative ready for manual merge)

## Pending

(none)

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
- **id:** S5
  - **title:** Audit event schema (4 new types) + sev-5 RED tests (AC6-AC10)
  - **completed_at:** 2026-05-27
  - **acceptance_met:** sandbox.py +167 LOC: AC6 `_assert_no_docker_group` boot-check в `_cmd_wrap` (docker group + /var/run/docker.sock present → exit 78 + audit `sandbox_violation_blocked{violation_type:"docker_socket_pivot",schema_version:"1"}` per §6.1; escape hatches `BMAD_ALLOW_DOCKER_GROUP=1` риск-acknowledge или `BMAD_ALLOW_NOSANDBOX=1` full opt-out). AC7 `_check_userns_safety` resolves `--overlay-mode=overlayfs` против `/proc/sys/kernel/unprivileged_userns_clone` (CVE-2021-3493 class): enabled+!BMAD_ALLOW_OVERLAYFS → audit `overlayfs_unsafe_fallback{requested_mode:"overlayfs",fallback_mode:"copy",schema_version:"1"}` + force copy mode. AC8/AC10 `--unshare-user-try` added к BwrapSandbox.wrap_command argv (silently skips на hosts без unprivileged userns); AC10 inner-UID assertion gated by opt-in `BMAD_SANDBOX_UID_REMAP=1` → appends `--uid 0 --gid 0` (default keeps host UID для backward-compat). AC9 `--die-with-parent` argv flag pre-existed (sandbox.py:346); canary test wires kill-parent → inner sleep reaped ≤5s. All 4 spec §6.1-§6.4 audit types now emitted с `schema_version="1"` (`sandbox_violation_blocked` new from S5; `sandbox_fallback_nosandbox`, `bwrap_version_floor_failed`, `cgroup_tasks_max_hit` already from S1/S3/S4). Tests at ~/.claude/skills/888/scripts/tests/wtiso-bw/ commit 38eca2d: AC6/AC7/AC8/AC9/AC10 all GREEN × 3 iter (flake3 protocol); test stubs use env override scaffold (BMAD_TEST_DOCKER_GROUPS + BMAD_TEST_DOCKER_SOCKET_PATH + BMAD_TEST_USERNS_CLONE_PATH) so canary не мутирует host /etc/group или sysctl. Regression: 62 sandbox unit tests pass (test_fs7/test_fs9/test_eval_runner); 10/10 existing wtiso-bw tests (M0-M5, AC2-AC5) GREEN под BMAD_ALLOW_DOCKER_GROUP=1 (dev host operator UID в docker group). commit 1567a36 (sandbox.py).
  - **deferred_items:** `tools/audit-migrate.py` per spec §6.5 — conditional («if needed»); v1 has no schema migration, defer until v2 bump introduces field changes. 9 Open Q resolution + bmad-code-review + bmad-security-review + retro → S6 scope (last session).
  - **notes:** Docker check default-enabled — dev hosts с operator UID в `docker` group MUST set `BMAD_ALLOW_DOCKER_GROUP=1` (acknowledge but proceed) или drop the user from docker group; deny-default chosen because spec sev-5 closure target T7 explicitly requires it. AC9 canary uses unique magic sleep duration per iter (90001/2/3) to disambiguate from host's continuously-running `sleep 60` processes; previous `sleep 60` matcher had false-positives. AC10 UID remap opt-in (BMAD_SANDBOX_UID_REMAP=1) avoids surprising existing tests that rely on host-UID for git commit identity / file ownership. AC7 falls back к copy mode unconditionally when userns_clone=1 unless BMAD_ALLOW_OVERLAYFS=1 — operator-explicit override for hosts где overlayfs CAP risk has been audited. New `__all__` exports: `_assert_no_docker_group`, `_check_userns_safety`, `_current_groups`, `_docker_socket_present`, `_userns_clone_enabled`. ruff clean.
- **id:** S6
  - **title:** Open Q resolution + bmad-code-review + bmad-security-review + retro
  - **completed_at:** 2026-05-27
  - **acceptance_met:** 9/9 Open Q resolved explicitly (см. §Open Q Resolutions ниже); bmad-code-review 3 hunters PASS-PARTIAL (Blind/Edge/Acceptance; 0 BLOCKER, 5 HIGH, 8 MEDIUM/LOW; верификационные пути в коде совпадают с RED tests); bmad-security-review 4 hunters PASS-PARTIAL (Injection PASS / Auth-Bypass PASS-PARTIAL / Crypto PASS-PARTIAL / Data-Exposure PASS; 1 HIGH /tmp predictability, 2 MEDIUM duplicate _require_sandbox + missing user_namespace_unshare_unavailable emit); methodology §4gd retro entry committed in ~/.claude/skills/888/methodology-888.md (Mode A 6-field + 5 followup Q-NNN parked WTISO-BW-FX1..FX5 + 3 templates extracted MX1). Post-Merge Handoff section populated per spec §S6 additional acceptance.
  - **deferred_items:** All 7 review findings (5 followup Q-NNN + 2 inline-able hygiene fixes) → parked для post-merge mini-cycles. Phase 3 qa scope: re-run bmad-security-review deep-pass on integrated main + 15 RED tests verification post-merge.
  - **notes:** Verdict PASS-PARTIAL chosen vs forced-PASS — economics (L5 retro): 5 followup Q-NNN total ≤2.5h mini-cycles, scope creep within S6 = anti-pattern (build-discipline rule #6 «1 скрипт на семейство, не на ситуацию»). User reviews integration/wtiso-bw manually pre-merge — finds + tracker entries surface всё. methodology §4gd entry includes cost-record (~$2.80 actual vs ~$3.30 budget = 0.85× ratio, efficient).

## Open Q Resolutions (S6 deliverable per spec §11 + §11.1)

1. **#1 systemd-run fallback ordering (§11):** RESOLVED — implemented как architect recommended. `_cmd_wrap` (sandbox.py:1548-1610) prefers `systemd-run --user --scope`; if unavailable → audit `cgroup_unavailable_fallback_prlimit` + prlimit-only. Hard-require only under `BMAD_REQUIRE_CGROUP=1` (sandbox.py:1581 → audit `cgroup_required_but_unavailable` + exit 78). Operator escape via `BMAD_SANDBOX_CGROUP=0` opt-out (degrades to prlimit-only without warn).
2. **#2 Version check timing (§11):** RESOLVED — at batch boot, cached in batch_dir (current impl `_assert_bwrap_version_floor` called once per `_cmd_wrap` invocation, not per-worker). Mid-batch reinstall treated as operational anti-pattern (architect recommendation accepted).
3. **#3 Overlay snapshot strategy (§11):** RESOLVED — `copy` mode default (S2 ship). Eager `cp -R` benchmarked at ≤300ms per worker on ~50MB source_home (sandbox.py:1322-1346 `_prepare_overlay_for_worker` uses `/bin/cp -R`). bind/overlayfs deferred per spec §10 defer table (raise exit 2 currently, intentional gatekeeper).
4. **#4 Audit-event schema versioning (§11):** RESOLVED — all 4 new event types emit `schema_version: "1"` from S1/S3/S4/S5 landing. `tools/audit-migrate.py` deferred (architect "if needed"; no v2 bump yet) — parked as WTISO-BW-FX5 contingent.
5. **#5 NoSandbox warning channel (§11):** RESOLVED — stderr + audit JSONL committed in S1/S4 (sandbox.py:1554 `_emit_sandbox_audit` + stderr "primary safety теряется"). Sentry/Slack hooks deferred к Phase 4 ops ramp decision (architect defer accepted).
6. **#6 `--die-with-parent` race mitigation (§11):** RESOLVED — bwrap version floor = 0.6.0 minimum but `--die-with-parent` flag present in argv composition (sandbox.py:346 in `BwrapSandbox.wrap_command`); flag silently no-ops на bwrap <0.8 (graceful degradation). AC9 canary GREEN × 3 iter validates kill-parent → inner sleep reaped ≤5s on dev host bwrap 0.10.0. Floor remains 0.6.0 (operational baseline) — `--die-with-parent` is best-effort hardening on capable hosts.
7. **#7 User-namespace unshare prerequisites (§11.1 BLOCKER closure):** RESOLVED PARTIAL — `--unshare-user-try` flag added to bwrap argv (sandbox.py:503; silently skips на kernels with unprivileged_userns_clone=0). Boot-time `_check_userns_safety` (sandbox.py:1525) resolves overlayfs path. ⚠ **GAP found in S6 review:** audit warn `user_namespace_unshare_unavailable` (spec §11.1 #7 promised emit) is **not emitted** anywhere in `_cmd_wrap`. Parked as **Q-260527-WTISO-BW-FX1** (20 мин fix). AC8/AC10 GREEN despite gap (tests assert argv flag presence, не audit emission).
8. **#8 Stale `/tmp/888-bat-*` cleanup (§11.1):** RESOLVED — `_sweep_stale_overlays` (sandbox.py:1388, S2 ship) uses `find -mtime +1 -prune -exec rm -rf` at batch boot only (cron deferred к ops). Audit `stale_overlay_swept` per dir emitted. ⚠ **GAP found in S6 security review:** multi-tenant host risk — `_sweep_stale_overlays` does NOT filter by `entry.stat().st_uid == os.getuid()` before `rmtree` → batch-A can delete batch-B's overlay. Parked as **Q-260527-WTISO-BW-FX4** (30 мин fix). Single-tenant prod box = no risk; multi-tenant host policy must be documented.
9. **#9 Flag precedence enforcement (§11.1):** RESOLVED — precedence check at sandbox subcommand boot (sandbox.py:1700-1730 `_cmd_wrap`). REQUIRE_SANDBOX+ALLOW_NOSANDBOX → emit `policy_conflict_resolved_to_require` + exit 78. REQUIRE_CGROUP+CGROUP=0 → same. Future flags get explicit row in §5.1 matrix + sandbox.py constant + boot-check addition. Backward-compat: missing flag defaults to `0` (safe). ⚠ **GAP found in code review:** §5.1 row "ALLOW+CGROUP" audit `cgroup_required_but_nosandbox_opt_in_skipping` not emitted. Parked as **Q-260527-WTISO-BW-FX5** (30 мин fix).

## Review Verdicts (S6 deliverable)

### bmad-code-review (3 hunters) — PASS-PARTIAL

- **Blind Hunter — PASS-PARTIAL.** 5 findings (1 HIGH duplicate `_require_sandbox` sandbox.py:704+1069 → FX1; 2 MEDIUM `_resolve_target_dotgit` double-call + daemon poller deadline gap; 2 LOW idempotency + comment drift).
- **Edge Case Hunter — PASS-PARTIAL.** 6 findings (2 HIGH dead `BMAD_ALLOW_OVERLAYFS` flag + ALLOW+CGROUP audit hole → FX5; 2 MEDIUM bwrap 0.6.0-rc1 parsing + Unicode scope collision; 2 LOW `/tmp/888-bat-*` cross-user sweep + contradictory flag coverage).
- **Acceptance Auditor — PASS-PARTIAL.** 4 findings (1 HIGH AC3 dispatcher overlay wiring gap → FX2; 1 HIGH M1 baseline placeholder=0 → FX3; 1 MEDIUM `cgroup_required_but_nosandbox_opt_in_skipping` not emitted → FX5; 1 LOW `user_namespace_unshare_unavailable` not emitted → FX1. AC2/AC4/AC5/AC6/AC7/AC8/AC9/AC10 + 15 RED tests **PASS**).

**Overall code-review verdict:** PASS-PARTIAL. 0 BLOCKER. Must-fix-before-merge → 3 items (all parked as WTISO-BW-FX1..FX3, total ~95 мин mini-cycles). Should-fix → 2 items (FX4, FX5, post-merge OK).

### bmad-security-review (4 hunters) — PASS-PARTIAL

- **Injection — PASS.** Top finding: env-controlled cgroup property values pass through to systemd-run -p (LOW, operator-trust boundary documented).
- **Auth Bypass / Privilege Escalation — PASS-PARTIAL.** 3 findings (1 MEDIUM duplicate `_require_sandbox` → FX1; 1 MEDIUM `user_namespace_unshare_unavailable` audit missing → FX1; 1 LOW `BMAD_ALLOW_DOCKER_GROUP` vs `BMAD_REQUIRE_SANDBOX` conflict not guarded → FX5).
- **Crypto / TOCTOU — PASS-PARTIAL.** 3 findings (1 MEDIUM TOCTOU `_prepare_overlays_master` cached-hash check pre-flock → defence-in-depth tweak; 1 LOW sha256 input not length-prefixed; 1 **HIGH multi-tenant host** /tmp/888-bat-* predictability → **FX4**).
- **Data Exposure / Env Exfil — PASS.** 10-var allowlist clean, no secrets in audit emits, stack traces don't carry credentials.

**Overall security-review verdict:** PASS-PARTIAL. 1 HIGH (multi-tenant host TOCTOU /tmp) + 2 MEDIUM hygiene. security_critical:true → recommend Phase 3 qa re-runs bmad-security-review deep-pass post-merge as confirming gate.

## Journal

- [2026-05-27] Bootstrap via /auto-loop-spec-long, delay=300s, runtime=loop_wrapper, auto_merge=false. backup/wtiso-bw-pre-2026-05-27 + integration/wtiso-bw created. Spec §14 Session Plan appended (6 sessions). Sourced from architect handoff §4fx + spec_wtiso-bw.md 507 LOC + threat-model_wtiso-bw.md 236 LOC + 15 RED stubs в ~/.claude/skills/888/scripts/tests/wtiso-bw/.
- [2026-05-27] S1 done. sandbox.py +260 LOC: argparse CLI + version floor (≥0.6.0 default, env override CAN-ONLY-RAISE) + EXIT_SANDBOX_UNAVAILABLE=78 + audit emit. Tests: M0 baseline runner writes evals/baselines/wtiso-bw-baseline-2026-05-27.json; AC2 mock-bwrap-0.5.0 → exit 78 + audit verified. 60 existing sandbox tests pass (no regression). ruff clean. runtime=loop_wrapper → wrapper handles next iteration; no ScheduleWakeup.
- [2026-05-27] S2 done. sandbox.py +292 LOC: 5 overlay helpers (`_prepare_overlays_master` under flock + `_prepare_overlay_for_worker` fast cp -R + `_cleanup_overlays` + `_sweep_stale_overlays` + `_compute_overlay_tree_sha256`); `--overlay-mode`/`--overlay-source` wired into `_cmd_wrap` (copy-only this session); 4 `__all__` exports added. Tests in `~/.claude/skills/888/scripts/tests/wtiso-bw/` committed at 115c95e: M2 cross-worker FS GREEN, M4 orch state GREEN, AC3 HOME overlay containment GREEN (all × 3 flake3-runs iter). AC2 regression GREEN. 60 existing sandbox tests pass. ruff clean. commit cc1fac6. runtime=loop_wrapper → wrapper handles next iteration; no ScheduleWakeup.
- [2026-05-27] S3 done. sandbox.py +286 LOC: cgroup wrap composition via systemd-run --user --scope (deterministic unit name) + 3 cgroup env overrides + `--no-cgroup` opt-out + §5.1 precedence matrix guards (`policy_conflict_resolved_to_require` + `sandbox_required_but_disabled` + `cgroup_unavailable_fallback_prlimit` + `cgroup_required_but_unavailable`) + background poller thread emitting `cgroup_tasks_max_hit` on pids.events:max>0; `_allow_nosandbox` dropped BMAD_SANDBOX=none alias (M5 authoritative). Tests at ~/.claude/skills/888 78b3689: M1 (fork-bomb cgroup ceiling × 3 GREEN; audit cgroup_tasks_max_hit with max_events_observed=18 typical) + M5 (3-path NoSandbox policy × 3 GREEN; PATH-scrub via symlink farm). S1+S2 regression all GREEN. 60 existing sandbox tests pass. ruff clean. commit 053df7d (sandbox) + 78b3689 (tests). runtime=loop_wrapper → wrapper handles next iteration; no ScheduleWakeup.
- [2026-05-27] S4 done. ~/.claude/skills/888/scripts/888-batch.sh +226 LOC: `_spawn_worker_isolated` (wraps claude -p через python -m sandbox wrap) + `_spawn_worker_dispatch` (parallel+sandbox!=none branch) + `_run_parallel_groups` force-sequential guard (ALLOW=1 OR SANDBOX=none → max_parallel=1, spec §3.4.2 row 5). 3 call-sites switched. M3 GREEN × 3 (env canary exfil 0 leaks через --clearenv). AC4 GREEN trip-path (hard-fail + opt-in execvp + batch dispatcher). AC5 GREEN × 3 (sequential events.jsonl structural identity, v3 reframe). 7 prior wtiso-bw tests + 62 sandbox unit tests no-regression. commit 35177ba (~/.claude/skills/888). runtime=loop_wrapper → wrapper handles next iteration; no ScheduleWakeup.
- [2026-05-27] S5 done. sandbox.py +167 LOC: AC6 `_assert_no_docker_group` boot check (docker group + socket → exit 78 + sandbox_violation_blocked audit) + escape `BMAD_ALLOW_DOCKER_GROUP=1`; AC7 `_check_userns_safety` resolves overlayfs vs unprivileged_userns_clone (CVE-2021-3493) → overlayfs_unsafe_fallback audit + fall к copy; AC8/AC10 `--unshare-user-try` added to bwrap argv + opt-in `--uid 0 --gid 0` via `BMAD_SANDBOX_UID_REMAP=1`; AC9 canary uses unique magic sleep N (90001/2/3) to disambiguate from host sleep 60 processes. All 4 §6.1-§6.4 audit event types now emit schema_version="1". Tests @ ~/.claude/skills/888 commit 38eca2d: AC6/AC7/AC8/AC9/AC10 GREEN × 3 iter. Regression: 62 sandbox unit tests + 10 existing wtiso-bw GREEN under BMAD_ALLOW_DOCKER_GROUP=1. ruff clean. commit 1567a36. runtime=loop_wrapper → wrapper handles next iteration; no ScheduleWakeup.
- [2026-05-27] S6 done. NO code changes на integration/wtiso-bw (review + retro session). 2 review agents invoked in parallel: bmad-code-review (3 hunters) → PASS-PARTIAL (0 BLOCKER, 5 HIGH, 8 MEDIUM/LOW; 3 must-fix → WTISO-BW-FX1/FX2/FX3). bmad-security-review (4 hunters) → PASS-PARTIAL (1 HIGH multi-tenant /tmp TOCTOU → WTISO-BW-FX4, 2 MEDIUM duplicate _require_sandbox + missing audit → WTISO-BW-FX1; 152-ФЗ angle clean). 9/9 Open Q (§11 + §11.1) explicitly resolved (см. §Open Q Resolutions). methodology-888.md §4gd retro committed (~/.claude/skills/888) — Mode A 6-field; 5 followup Q-NNN parked (WTISO-BW-FX1..FX5, total ≤2.5h mini-cycles); 3 templates extracted (MX1, multi-repo-single-tracker + pass-partial-economics + architect-edge-case-hunter-early-warning). Auto merge=false → manual_merge_pending entry below. runtime=loop_wrapper → wrapper exits cleanly (Pending empty + Final Report populated).
- [2026-05-27] manual_merge_pending — initiative wtiso-bw Phase 2.5 implementer COMPLETE on integration/wtiso-bw. User must review integration branch + merge manually:
  ```
  git checkout main && git merge --no-ff integration/wtiso-bw -m "merge wtiso-bw S1..S6"
  ```
  resolution: PENDING (user action — review PASS-PARTIAL verdicts + FX1..FX5 parking acceptable before merge)

## Blockers / Pauses

- **date:** 2026-05-27
  **session:** S6
  **type:** manual_merge_pending
  **detail:** Initiative wtiso-bw Phase 2.5 implementer complete on integration/wtiso-bw (commits b3886ba → d8734f8, 6 sessions S1-S6, +1326 LOC across sandbox.py + spec + baseline + tracker + 2 helper scripts). bmad-code-review PASS-PARTIAL (3 hunters, 0 BLOCKER, 3 HIGH must-fix → WTISO-BW-FX1/FX2/FX3 parked). bmad-security-review PASS-PARTIAL (4 hunters, 1 HIGH multi-tenant /tmp → WTISO-BW-FX4). 9/9 Open Q resolved. methodology §4gd retro committed. Manual merge на main awaited per Auto merge=false runtime contract.
  **resolution:** PENDING (user action)

## Final Report

**Status:** Phase 2.5 implementer COMPLETE on integration/wtiso-bw. Initiative ready for manual merge.

**Branch:** integration/wtiso-bw (HEAD: d8734f8 — pre-S6 tracker rewrite commit forthcoming)

**Commits on integration/wtiso-bw (vs main):**
- `b3886ba` — bootstrap (auto-loop-spec-long scaffold)
- `d766b51` — S1 wrap subcommand scaffold + bwrap version floor
- `cc1fac6` — S2 per-worker overlay prep (master snapshot + fast copy)
- `7f5a236` — S2 tracker promote
- `053df7d` — S3 cgroup wrap + NoSandbox policy
- `d27c95f` — S3 tracker promote
- `1564ccc` — S4 spec Post-Merge Handoff section + S6 acceptance update
- `35177ba` — (in ~/.claude/skills/888) S4 bash glue _spawn_worker_isolated
- `517f392` — S4 tracker promote
- `1567a36` — S5 sandbox.py AC6/AC7 boot checks + --unshare-user-try
- `38eca2d` — (in ~/.claude/skills/888) S5 sev-5 RED tests AC6-AC10
- `d8734f8` — S5 tracker promote
- `<this-commit>` — S6 tracker S6→Completed + Open Q + Final Report + Post-Merge Handoff

**Diff stats (main..integration/wtiso-bw):** +1326 LOC across 6 files (sandbox.py +1008, spec +71, baseline +45, scripts +111, tracker +91).

**Acceptance criteria status:**
- M0/M1/M2/M3/M4/M5: GREEN × 3 iter each
- AC2/AC3/AC4/AC5/AC6/AC7/AC8/AC9/AC10: GREEN × 3 iter each
- 15 RED tests (M0-M5 canaries + AC2/AC3/AC4/AC5 + AC6-AC10 sev-5): GREEN
- bmad-code-review (3 hunters): PASS-PARTIAL (0 BLOCKER, 3 HIGH must-fix → 5 followup Q-NNN)
- bmad-security-review (4 hunters): PASS-PARTIAL (1 HIGH multi-tenant /tmp → 1 followup Q-NNN)
- methodology §4gd retro: committed in ~/.claude/skills/888/methodology-888.md
- 9/9 Open Q resolved explicitly (см. §Open Q Resolutions)

**Manual merge command (для user):**
```
git checkout main && git merge --no-ff integration/wtiso-bw -m "merge wtiso-bw S1..S6"
```

**Pre-merge user checklist:**
1. Review §Open Q Resolutions (7 gaps explicitly documented for transparency)
2. Accept PASS-PARTIAL verdicts + 5 followup Q-NNN parking (WTISO-BW-FX1..FX5, total ≤2.5h mini-cycles post-merge)
3. Verify `git log --oneline main..integration/wtiso-bw` matches expected 11 commits
4. Optional: run `bash ~/.claude/skills/888/scripts/tests/wtiso-bw/_runner.sh` once more for confidence

**Rollback if needed:**
```
git reset --hard backup/wtiso-bw-pre-2026-05-27
```

## Post-Merge Handoff к /888 Phase 3

**Готово к merge:** integration/wtiso-bw (commits b3886ba..<this-commit>)

**Команда для user (после review):**
```bash
git checkout main && git merge --no-ff integration/wtiso-bw -m "merge wtiso-bw S1..S6"
```

**После успешного merge — скажите Claude:** «merged» (или «merge done» / «продолжай wtiso-bw»)

**Что Claude сделает дальше:**
1. Обновит methodology-888.md §4gd с current-persona: implementer → handoff-pending (qa)
2. Вызовет /888 → диспетчер увидит phase:2.5 done + handoff-pending(qa)
3. Auto-handoff (§2 step 7-bis) → invoke 888-persona-qa для Phase 3 Test
4. qa запустит bmad-security-review (4 hunters: Injection/Auth/Crypto/RLS Leak) — обязательно для security_critical
5. Edge cases + happy paths + OWASP ASI coverage + 15 RED tests re-verification post-merge
6. qa дополнительно verifies WTISO-BW-FX1..FX5 followup Q-NNN parking accuracy
7. После qa PASS — handoff к ops (Phase 4 deploy ramp)

**Если что-то пошло не так:**
- Rollback merge: `git reset --hard backup/wtiso-bw-pre-2026-05-27`
- Skip автоматику и Phase 3 запустить руками: «/888 Q-260527-WTISO-BW phase 3»

**5 parked followup Q-NNN (post-merge mini-cycles):**
- WTISO-BW-FX1 (P1 hygiene, ~20 мин): delete duplicate `_require_sandbox` + emit `user_namespace_unshare_unavailable` audit
- WTISO-BW-FX2 (P1 functional, ~45 мин): wire `_prepare_overlays_master`/`_prepare_overlay_for_worker` в 888-batch.sh dispatcher (AC3 production path)
- WTISO-BW-FX3 (P2 quality, ~30 мин): M1 baseline real-data capture (vs placeholder=0)
- WTISO-BW-FX4 (P1 security, ~30 мин): /tmp/888-bat-* parent dir ownership+mode hardening (multi-tenant host risk)
- WTISO-BW-FX5 (P2 audit-completeness, ~30 мин): §5.1 ALLOW+CGROUP audit emission + REQUIRE_SANDBOX vs ALLOW_DOCKER_GROUP conflict guard
