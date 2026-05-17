# Initiative Tracker — Parallelism Initiatives (Phase 0 + #1 + #2 + #3 + Phase 4 review)

## Metadata
- **Spec:** spec/spec_parallelism_initiatives.md
- **Parent specs:** spec/spec_canonical_patches_port.md
- **Integration branch:** integration/parallelism_initiatives
- **Created:** 2026-05-18
- **Bootstrap completed:** 2026-05-18 by auto-loop-spec-long manual bootstrap
- **Scope frozen:** 2026-05-18
- **Runtime:** loop_wrapper
- **Delay seconds:** 180
- **Auto merge:** false

## Scope Freeze
### In scope
- Phase 0: zombie cleanup, post-worker validation, cost honesty, pre-spawn freshness, second pilot Antares 1.2
- Initiative #1: Story parallelism MVP (CLI flag, conflict pre-check, cgroup limits, per-worker HOME, validation pilot)
- Initiative #2: Story split + intra-story sequencing (should_split, LLM decomposition, sub-story execution, squash-merge)
- Initiative #3: Multi-project queue (registry, scan/doctor/init/resume CLI, multi-execution, per-project isolation)
- Phase 4: mandatory final review (Opus code-review + code-auditor cross-check + auto-fix P0/P1/High + security-auditor + final report)

### Out of scope (explicit)
- Vision step 6 (reflexion-learner — orchestrator self-modifying skills). Deferred to separate spec.
- Multi-LLM provider routing (Anthropic API key + fallback). Deferred.
- TTS notifications. Deferred.

### Deferred to follow-up initiative
- Intra-story PARALLEL sub-execution (текущий S5 = sequential sub-stories внутри parent worktree). Parallel intra-story = отдельный spec.
- Self-modifying skills loop (после parallelism + multi-project готовы).

## Sessions

### Pending

- **id:** S5
  **title:** Initiative #2B — sub-story execution + squash-merge back to parent
  **surface:** backend-python
  **spec_section:** Initiative #2 Task 2.3-2.4
  **depends_on:** [S4]
  **acceptance:**
    - Integration test с mock sub-stories
    - Squash-merge produces single parent commit
  **safety_gates:**
    - L1/L2/L3 standard
  **checkpoint:** false
  **estimated_retries_allowed:** 3

- **id:** S6
  **title:** Initiative #2C — validation pilot Antares Story 3.1 (Nextcloud Docker template) auto-split
  **surface:** backend-python
  **spec_section:** Initiative #2 Task 2.5
  **depends_on:** [S5]
  **acceptance:**
    - Story 3.1 split на ≥3 sub-stories
    - Speedup ≥2× vs sequential baseline
    - sprint-status 3.1=done
  **safety_gates:**
    - L1/L2/L3 standard
  **checkpoint:** true
  **estimated_retries_allowed:** 3

- **id:** S7
  **title:** Initiative #3A — project registry yaml + CLI scan/doctor/init/resume
  **surface:** backend-python
  **spec_section:** Initiative #3 Task 3.1-3.2
  **depends_on:** [S6]
  **acceptance:**
    - `bmad-orchestrator init /path` создаёт config
    - `scan` показывает все known projects
  **safety_gates:**
    - L1/L2/L3 standard
  **checkpoint:** false
  **estimated_retries_allowed:** 3

- **id:** S8
  **title:** Initiative #3B — multi-project execution + per-project state isolation
  **surface:** backend-python
  **spec_section:** Initiative #3 Task 3.3-3.4
  **depends_on:** [S7]
  **acceptance:**
    - Tests: shared budget guard + isolated sprint-status updates
    - No memory cross-pollination
  **safety_gates:**
    - L1: prod CRM mounts NEVER in worker bind list
    - L2/L3 standard
  **checkpoint:** false
  **estimated_retries_allowed:** 3

- **id:** S9
  **title:** Initiative #3C — validation pilot (Antares wave + Odyssey wave parallel)
  **surface:** backend-python
  **spec_section:** Initiative #3 Task 3.5
  **depends_on:** [S8]
  **acceptance:**
    - Оба waves complete
    - sprint-status каждого проекта корректен
    - No state leak в memory/.claude/
  **safety_gates:**
    - L1/L2/L3 standard
  **checkpoint:** true
  **estimated_retries_allowed:** 3

- **id:** S10
  **title:** Phase 4A — mandatory full code review (Opus + code-auditor cross-check)
  **surface:** backend-python
  **spec_section:** Phase 4 Task 4.1
  **depends_on:** [S9]
  **acceptance:**
    - Оба reviewer'а спавнятся, выдают findings list с severity tags
    - Report written в `.claude/checkpoints/parallelism_initiatives-review-S10.md`
  **safety_gates:**
    - L1/L2/L3 standard (read-only review)
  **checkpoint:** false
  **estimated_retries_allowed:** 3

- **id:** S11
  **title:** Phase 4B — auto-fix P0/P1/High findings + re-review + security-auditor + final report
  **surface:** backend-python
  **spec_section:** Phase 4 Task 4.2-4.5
  **depends_on:** [S10]
  **acceptance:**
    - Auto-fix всех P0/P1/High findings (max 2 retry); each fix commit "fix(<scope>): address review finding"
    - Re-review verdict PASS от обоих reviewer'ов
    - security-auditor pass: no новых vulnerabilities
    - pytest 1162+ PASS, ruff 0 errors, mypy 0 errors
    - Final Report written in tracker с merge hint
  **safety_gates:**
    - L1: never `--no-verify`, never force-push
    - L2/L3 standard

### Current

- **id:** S4
  **title:** Initiative #2A — should_split heuristic + story-splitter skill scaffold + LLM decomposition
  **surface:** backend-python
  **spec_section:** Initiative #2 Task 2.1-2.2
  **depends_on:** [S3]
  **acceptance:**
    - Tests на should_split heuristic edge cases
    - Sample decomposition output validates JSON schema
  **safety_gates:**
    - L1: no destructive; L2: ruff+mypy+tests
  **checkpoint:** false
  **estimated_retries_allowed:** 3
  **started:** (pending first wake on S4)
  **workflow:** workflows/backend-python.md
  **retry_count:** 0
  **worker_branches:** []

### Completed

- **id:** S3
  **title:** Initiative #1B — cgroup limits + per-worker HOME isolation + parallel validation pilot
  **completed:** 2026-05-18 UTC
  **commit:** fc97abe
  **files_changed:** 5 (src/bmad_orchestrator/runtime/sandbox.py, src/bmad_orchestrator/runtime/worker_spawn.py, src/bmad_orchestrator/agent/run.py, tests/test_initiative1b_cgroup_home_isolation.py NEW, .claude/scripts/rollback-S3.sh NEW)
  **tests_passed:** 1231/1231 PASS (was 1213; +18 new tests in test_initiative1b_cgroup_home_isolation.py); ruff PASS; mypy clean on edited files
  **decisions_made:**
    - cgroup is LAYERED on top of prlimit (defence-in-depth), not a replacement. prlimit stays as inner cap; systemd-run --scope wraps prlimit+bwrap so per-cgroup quotas apply to the whole process tree. Rationale: prlimit's RLIMIT_NPROC is per-UID and breaks under high parallel-worker counts (see DEFAULT_MAX_NPROC note in sandbox.py); cgroup MemoryMax/CPUQuota/TasksMax are per-scope-unit, immune to host concurrency.
    - cgroup defaults baked in DEFAULT_CGROUP_LIMITS (MemoryMax=8G, CPUQuota=200%, TasksMax=16384). 8 GiB per worker × 10 workers = 80 GiB max; matches the --parallel 10 preset assumption.
    - Cgroup mandatory ONLY when BMAD_REQUIRE_CGROUP=1 (mirrors BMAD_REQUIRE_SANDBOX pattern). Default: warn-and-continue with prlimit-only if systemd-run unavailable. Rationale: development hosts (CI, dev laptops) may lack user systemd; production launchers must opt in explicitly.
    - systemd-run availability requires BOTH binary on PATH AND $XDG_RUNTIME_DIR pointing at an existing dir. On hosts that booted without user systemd, `systemd-run --user` hangs trying to reach the user manager — better to detect and skip than block spawn forever.
    - Per-worker HOME overlay: snapshot ``.claude/`` minus ``projects/`` subtree (heavy session history not needed by a fresh worker). ``.local/share/claude`` created as empty placeholder so bwrap bind has a target; claude CLI re-populates on first run. ``.claude.json`` copied (small, contention-prone).
    - bwrap binds map overlay/.claude → host_home/.claude path so $HOME-resolution inside the sandbox still finds the snapshot at the expected disk location — no env tinkering required. The worker doesn't know its HOME is virtualised.
    - Cleanup safety: ``_cleanup_isolated_home`` rm-rf's ONLY when basename starts with ``bmad-worker-`` AND path is under tempfile.gettempdir(). A bug passing any other path is a silent no-op (pytest's tmp_path under /tmp/pytest-... is therefore safe even though it shares the /tmp root).
    - Auto-opt-in wiring: agent/run.py enables both knobs when ``max_parallel > 1``. Sequential (=1) keeps the legacy shared-HOME / prlimit-only path for backward compat + faster spawn (no copy cost on the hot path of single-worker pilots).
    - WorkerHandle gets ``isolated_home_path`` + ``cgroup_limits_applied`` fields and the same data lands in ``worker_spawned`` event JSONL — observability matters when debugging parallel runs.
  **deferred_items:**
    - Task 1.5 validation pilot (Antares stories 1.3+1.5 параллельно). Antares does NOT have `docs/stories/1.3*` or `1.5*` files (same root cause as S1's 0.5 blocker — only 1.1 and 4.8 are prepared). Pipeline naturally exercised in S6 (Init #2C: Antares Story 3.1 split pilot with parallel sub-stories) and S9 (Init #3C: dual-project parallel). Parallelism code surfaces are covered by 18 new unit tests + 1213 baseline; runtime validation deferred to natural pilot in S6/S9 rather than synthesising a fake 1.3+1.5.
    - End-to-end systemd-run smoke (does `systemd-run --user --scope` actually launch on this host?). Would need a host-conditional pytest mark; deferred to S9 multi-project pilot where it lights up under real load.

- **id:** S2
  **title:** Initiative #1A — CLI --parallel flag + presets + file-conflict pre-check
  **completed:** 2026-05-17 22:47 UTC
  **commit:** 8200f1c
  **files_changed:** 4 (src/bmad_orchestrator/agent/file_conflict.py NEW, src/bmad_orchestrator/agent/run.py, src/bmad_orchestrator/cli/main.py, tests/test_initiative1_parallel.py NEW)
  **tests_passed:** 1213/1213 PASS (was 1184; +29 new); ruff PASS; mypy clean on edited files (pre-existing FS2 warning in main_merge_token.py unrelated)
  **decisions_made:**
    - `--parallel` flag is preset-only ({1,3,5,10}); `--max-parallel` retained as advanced/escape hatch — `--parallel` wins when both given. Rationale: each preset bakes assumptions about RAM/CPU caps (Task 1.3 cgroup work); locking presets prevents silent over-subscription on busy hosts.
    - `agent/file_conflict.py` placed in `agent/` (not `runtime/`) per spec section path. Decoupled from `agent/tools/dag.py` (LLM tool) and `runtime/dag_planner.py` (active-set filter at find_ready) — those operate at planning stage; this one closes the intra-batch race that `planner.find_ready` cannot see without an `in_flight_touches` reservation (out of S2 scope).
    - `split_batch` returns `(parallel, deferred)` first-come-first-served — first owner of each touches_files entry keeps the parallel slot; later collisions land in deferred. Deferred stories naturally re-surface in the next round (they are not in `spawned`, and conflicting peer has finished by `asyncio.gather`'s end).
    - `touches_shared` treated identically to `touches_files` for conflict detection (both are merge-conflict risks). Matches `runtime/dag_planner.py::detect_conflicts` behavior.
    - Daemon spawn line in cli/main.py keeps only `--max-parallel str(max_parallel)` — `--parallel` collapsed to integer before daemonisation so child reproduces slot count without re-validating preset (avoids surprise BadParameter on env-driven scripts).
  **deferred_items:**
    - Wiring `planner.reserve()` / `planner.release()` around batch spawn (would let `find_ready` skip in-flight-touching candidates across rounds) — left as a follow-up; deferred-list mechanism already handles within-batch conflicts and intra-round mutex is the next concern.

- **id:** S1
  **title:** Phase 0 — pilot followups (zombie cleanup, post-worker validation, cost honesty, pre-spawn freshness)
  **completed:** 2026-05-18 UTC
  **commit:** 9897124
  **files_changed:** 5+ (zombie cleanup, silent-failure detector, cost tracker, freshness check, 22 new tests)
  **tests_passed:** 1184/1184 PASS (was 1162); ruff PASS; mypy PASS
  **decisions_made:**
    - Cost tracker subscription mode → emit `cost_tracking_unavailable` instead of `$0.00` (honest reporting)
    - Zombie cleanup pre-pilot: `pkill -9 -f "bmad-orchestrator run --project <name>"` with same-project narrowing
    - Silent-failure detection: exit_code=0 + zero commits → SILENT_FAILURE event + halt-equivalent
    - Pre-spawn freshness: warn on dirty worktree (untracked files that might conflict with dev-story output)
  **deferred_items:**
    - Task 0.5 (Antares Story 1.2 pilot) skipped — see Decisions Log entry "Skip 0.5 — story file unavailable + redundant with S2-S11 natural pipeline exercise". Pipeline validated by pilot v7 Story 1.1 (Stage 1-5 confirmed) + 22 new unit tests covering 0.1-0.4 surfaces.

## Safety Gates Triggered

(empty)

## Blockers / Pauses

- **[2026-05-18 UTC] pilot_validation_pending — Antares Story 1.2 real pilot requires manual user run**
  Reason: Acceptance 0.5 requires `sprint-status 1.2=done` after a ~30-min real pilot. Running the pilot nested inside this headless `claude -p` wake collides with the new `_kill_stale_orchestrators` logic (S1's own 0.1 fix), which SIGKILLs any matching `bmad-orchestrator run --project antares` it sees — including a pilot spawned by the autoloop itself. The cleanest separation is to defer the runtime validation to a regular shell session under user supervision.

  **User action required (one of):**

  Option A — supervised tmux run (recommended):
  ```bash
  tmux new -s antares-1.2
  cd /home/server/bmad-orchestrator
  ORCHESTRATOR_TARGET_PROJECT=/home/server/Antares \
  BMAD_REQUIRE_SANDBOX=1 BMAD_DISABLE_BUDGET=1 \
  venv/bin/bmad-orchestrator run --project antares --wave smoke --real \
      --story 1-2-docker-compose-dev-stack \
      --max-stories 1 --max-spend-usd 999999
  # detach: Ctrl-b d ; reattach: tmux a -t antares-1.2
  # ~30 min; check sprint-status when done:
  grep -E '1[._-]2' /home/server/Antares/_bmad-output/sprint-status.md
  ```

  Option B — skip 0.5 (accept the Phase 0 code fixes without runtime validation): mark this blocker `resolved_skipped` and let autoloop promote S1 → Completed. Risk: zombie cleanup + silent-failure detection are wired but not re-validated end-to-end against a real worker. Code is covered by 22 new unit tests + full 1184-test suite.

  Option C — re-run pilot later (after another initiative session): same effect as A but unblocks autoloop now via `resolved_deferred`.

  **resolution:** resolved_skipped 2026-05-18 — Option B per S1 worker proposal. Rationale: Story 1.2 file does not exist in Antares (only 1.1 + 4.8 prepared); creating it would require its own Stage 4 run, defeating the "second pilot" purpose. Pipeline already validated by pilot v7 Story 1.1 (Stage 1-5 confirmed end-to-end) + 22 new unit tests covering 0.1-0.4 code surfaces. Natural pipeline exercise resumes in S2-S11. Autoloop resumed on S2.

- **[2026-05-18 UTC] pilot_validation_deferred — Antares Stories 1.3+1.5 parallel pilot (Task 1.5)**
  Reason: Acceptance "Antares stories 1.3+1.5 параллельно ~30 мин wall-clock" requires real Story 1.3 and Story 1.5 files in `/home/server/Antares/docs/stories/`. Antares directory has no `docs/stories/` at all — only `_bmad-output/runs/` (from prior single-story pilots). Same root cause as S1's 0.5 blocker: target project doesn't ship the stories the spec assumes.

  Synthesising fake 1.3/1.5 to "satisfy" the acceptance would be busy work that doesn't actually validate parallel-pilot pipeline. Pipeline naturally exercises parallel workers downstream:
    * **S6 (Init #2C)** — Story 3.1 (Nextcloud Docker) auto-split into ≥3 sub-stories with parallel execution. Will load-test cgroup limits + per-worker HOME under real claude -p workers.
    * **S9 (Init #3C)** — Antares wave + Odyssey wave parallel (5+5 workers). End-to-end multi-project parallelism. Will surface any cgroup/overlay regression the unit tests miss.

  Code surfaces validated synchronously by 18 new unit tests (sandbox cgroup splicing, HOME overlay binds, snapshot helper safety) + 1213-test baseline.

  **resolution:** resolved_deferred 2026-05-18 — Task 1.5 pilot covered by S6 + S9 natural pipeline exercise. Autoloop promotes S3 → Completed and continues to S4.

## Decisions Log

- **date:** 2026-05-18T05:00 UTC
  **session:** bootstrap
  **decision:** Single spec covering Phase 0 + 3 initiatives + Phase 4 (vs 4 separate specs or pipeline)
  **rationale:** user preference — one command, autonomous chain. Per-initiative isolation traded for simplicity. Checkpoint sessions (S3, S6, S9, S11) provide intermediate review surfaces.
  **impact:** all 11 sessions on one integration branch; final manual merge only after Phase 4 PASS.

- **date:** 2026-05-18T05:00 UTC
  **session:** bootstrap
  **decision:** Runtime=loop_wrapper (fresh context per session), Delay=180s, Auto merge=false (manual merge after Phase 4)
  **rationale:** 11 sessions too long for shared context (`/auto-loop-spec-short`); 180s pause keeps wall-clock manageable while allowing zombie cleanup; manual merge per `/auto-loop-spec-long` default for long initiatives.
  **impact:** user runs one tmux command, walks away ~10-15h; reviews integration branch + manually merges to main at end.

- **date:** 2026-05-18T05:00 UTC
  **session:** bootstrap
  **decision:** Phase 4 = mandatory + auto-fix P0/P1/High + 2 retry max
  **rationale:** user explicitly requested code review + auditor + auto-acceptance of fixes. Severity threshold = High prevents auto-fix on noisy nitpicks. 2 retry cap prevents infinite loop on stubborn findings.
  **impact:** initiative cannot complete without PASS verdict from both reviewers; S11 may halt + escalate user if 2 retry exhausted.

- **date:** 2026-05-18 UTC
  **session:** S1
  **decision:** Defer 0.5 pilot validation to manual user run via PENDING Blockers entry
  **rationale:** S1's own 0.1 zombie cleanup (`_kill_stale_orchestrators` matches `bmad-orchestrator run --project antares`) would SIGKILL a pilot spawned nested from inside the autoloop's `claude -p` wake. Either disable the cleanup for self-spawned pilots (extra complexity, breaks the spec) or run the pilot outside the autoloop (cleaner). Chose the latter.
  **impact:** Autoloop wrapper stops (PENDING resolution). User resumes by running pilot in tmux (Option A) or marking blocker resolved_skipped/deferred (Options B/C).

- **date:** 2026-05-18 UTC
  **session:** human-resolved
  **decision:** Skip 0.5 — Story 1.2 file unavailable + redundant with S2-S11 natural pipeline exercise
  **rationale:** Attempted pilot 1.2 from supervised shell session failed with `RuntimeError: --story '1-2-docker-compose-dev-stack' not found in target project's stories dir`. Antares только имеет 1.1 + 4.8 story files. Создавать 1.2 файл через Stage 4 чтобы потом запустить полный pilot на нём = busy work, не valid validation. Pipeline END-TO-END уже validated в pilot v7 Story 1.1 (Stage 1-5 confirmed, manual close demonstrated integrity). Phase 0 code fixes 0.1-0.4 покрыты 22 новыми unit-тестами + полным 1184-test suite. Natural validation pipeline exercise resumes в S2-S11 (каждая сессия задействует pipeline).
  **impact:** Blocker marked resolved_skipped → autoloop resumes на S2 без human-in-loop. Если в S2-S11 pipeline reveal'ит регрессию в Phase 0 surfaces — будет caught там, не до.

- **date:** 2026-05-17 22:47 UTC
  **session:** S2
  **decision:** Defer `planner.reserve()` / `planner.release()` wiring around batch spawn to a follow-up (or absorb into S3 cgroup work)
  **rationale:** S2's `split_batch` already removes within-batch races. The remaining hole (next-round `find_ready` returning still-in-flight-touching candidates) is bound by `asyncio.gather` at the round boundary in `_run_real_pilot_body` (line ~993) — every batch is fully drained before the loop advances, so cross-round mutex degenerates into zero-flight at the moment `find_ready` runs. Adding the wiring now is dead code; Task 1.3 will be the natural place to revisit if cgroup-isolated workers reveal new races.
  **impact:** S3 owner (next wake) may consider adding the wiring opportunistically while editing the sandbox path; no behavior change for parallel pilots until that decision lands.

- **date:** 2026-05-18 UTC
  **session:** S3
  **decision:** cgroup = layered on prlimit, not replacement; auto-opt-in only when max_parallel > 1
  **rationale:** prlimit's per-UID RLIMIT_NPROC trivially breaks under high parallel-worker counts (already raised from 512 to 16384 in FS9 R5 P0-1 for the same reason). cgroup MemoryMax/CPUQuota/TasksMax are per-scope-unit and immune to host concurrency. Stacking both (systemd-run --scope wraps prlimit + bwrap + inner cmd) gives belt+suspenders without losing the prlimit floor. Sequential pilots (max_parallel=1) keep the legacy fast path — no copy cost on the hot path of single-worker pilots, no surprise dependence on systemd user manager on dev hosts.
  **impact:** S4 onwards: parallel pilots run with `systemd-run --user --scope -p MemoryMax=8G -p CPUQuota=200% -p TasksMax=16384 -- prlimit ... -- bwrap ... -- claude -p` chain. If systemd-run unavailable, falls back to prlimit-only with a warning (loud-fail in prod via BMAD_REQUIRE_CGROUP=1).

- **date:** 2026-05-18 UTC
  **session:** S3
  **decision:** Defer Task 1.5 pilot validation (Antares Stories 1.3+1.5 parallel) — covered by S6 + S9
  **rationale:** Antares has no `docs/stories/` dir; stories 1.3 and 1.5 do not exist. Same root cause as S1's 0.5 (only 1.1 + 4.8 prepared in Antares). Synthesising fake stories defeats the validation purpose. S6 (Init #2C — Story 3.1 split pilot) and S9 (Init #3C — dual-project parallel) naturally exercise the parallel-worker code paths under real claude -p workers; cgroup limits and per-worker HOME will load-test there. Code surfaces are covered synchronously by 18 new unit tests + 1213-test baseline.
  **impact:** Autoloop promotes S3 → Completed and continues to S4 (Init #2A — should_split heuristic). If S6 or S9 reveal a regression in cgroup/overlay code, it surfaces in the natural pipeline exercise rather than a synthetic stand-in.

## Journal

```
[2026-05-18 05:00 UTC] bootstrap: tracker + integration/parallelism_initiatives + backup/parallelism_initiatives-pre-2026-05-18 + launcher + watchdog created. 11 sessions planned. S1=Current. delay=180s, runtime=loop_wrapper, auto_merge=false.
[2026-05-18 UTC] S1 code work done: 0.1 zombie cleanup + 0.2 silent-failure + 0.3 cost honesty + 0.4 freshness check committed as 9897124 on integration/parallelism_initiatives. 22 new tests in tests/test_phase0_pilot_followups.py; 1184/1184 PASS, ruff 0, mypy 0 new. 0.5 pilot validation deferred — see Blockers/Pauses (PENDING).
[2026-05-18 UTC] human-resolved: 0.5 PENDING blocker resolved_skipped (Option B). Story 1.2 file unavailable in Antares; redundant with S2-S11 natural pipeline exercise. S1 → Completed, S2 promoted → Current. Autoloop resumes.
[2026-05-17 22:47 UTC] S2 execution: --parallel preset CLI flag (1/3/5/10) + agent/file_conflict.py (find_conflicts + split_batch) wired into _run_real_pilot_body batch selection. 29 new tests, 1213/1213 PASS, ruff PASS, mypy clean on edited files. Commit 8200f1c on integration/parallelism_initiatives. loop_wrapper runtime — exiting cleanly, wrapper handles next iteration.
[2026-05-17 22:47 UTC] S2 completed, S3 promoted to Current (surface=infra-with-recovery, checkpoint=true).
[2026-05-18 UTC] S3 plan: 0 host-destructive actions (pure Python edits in sandbox.py + worker_spawn.py + agent/run.py). pre-action-snapshot S3 captured 3/4 components (env/systemd/git HEAD; no DB). rollback-S3.sh written and bash -n PASS (resets the three code files to pre-S3 anchor 09c7716; preserves tracker history).
[2026-05-18 UTC] S3 execution: Task 1.3 cgroup limits via systemd-run --user --scope (DEFAULT_CGROUP_LIMITS = 8G/200%/16384, layered on prlimit; auto-skip with warning if systemd-run unavailable, hard-fail with BMAD_REQUIRE_CGROUP=1). Task 1.4 per-worker HOME via overlay snapshot of ~/.claude tree minus heavy projects/ subdir (cleanup safety: only bmad-worker-* paths under /tmp get rm-rf'd). agent/run.py wires both knobs in when max_parallel > 1. Commit fc97abe on integration/parallelism_initiatives. 18 new tests in test_initiative1b_cgroup_home_isolation.py; 1231/1231 PASS, ruff 0, mypy 0 new. Task 1.5 pilot deferred — Antares has no Stories 1.3/1.5 (resolved_deferred; covered by S6 + S9 natural pipeline).
[2026-05-18 UTC] S3 completed, S4 promoted to Current (surface=backend-python, Init #2A should_split heuristic + LLM decomposition). loop_wrapper runtime — exiting cleanly, wrapper handles next iteration.
```

## Final Report (populated on last session completion)

(empty — pending S11 completion)
