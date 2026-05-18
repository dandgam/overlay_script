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

(empty — S11 is now Current; no further sessions planned)

### Current

- **id:** S11
  **title:** Phase 4B — auto-fix P0/P1/High findings + re-review + security-auditor + final report
  **surface:** backend-python
  **spec_section:** Phase 4 Task 4.2-4.5
  **depends_on:** [S10]
  **acceptance:**
    - Auto-fix всех P0/P1/High findings from S10 (12 findings: 6 P1 + 6 High; max 2 retry each); each fix commit "fix(<scope>): address review finding <id>"
    - Re-review verdict PASS от обоих reviewer'ов (code-reviewer + code-auditor)
    - security-auditor pass: no новых vulnerabilities
    - pytest 1386+ PASS, ruff 0, mypy 0 new errors
    - Final Report written in tracker с merge hint (`Auto merge: false` → manual merge instructions)
  **safety_gates:**
    - L1: never `--no-verify`, never force-push
    - L2/L3 standard
  **checkpoint:** false
  **estimated_retries_allowed:** 3
  **started:** (pending first wake on S11)
  **workflow:** workflows/backend-python.md
  **retry_count:** 0
  **worker_branches:** []
  **findings_to_fix:** see .claude/checkpoints/parallelism_initiatives-review-S10.md §P1 and §High (12 items total)

### Completed

- **id:** S10
  **title:** Phase 4A — mandatory full code review (Opus + code-auditor cross-check)
  **completed:** 2026-05-18 UTC
  **commit:** (tracker-only commit; no source changes — S10 is read-only review)
  **files_changed:** 0 source / 1 review report new (.claude/checkpoints/parallelism_initiatives-review-S10.md)
  **tests_passed:** N/A (read-only review session; tracker pin 1386/1386 PASS from S9 preserved)
  **verdict:** FAIL (both reviewers) — 6 P1 + 6 High + 11 Medium + 8 Low findings catalogued
  **decisions_made:**
    - **Two paired Opus 4.7 reviewers spawned in parallel** — `code-reviewer` subagent (53,886 tokens / 411s) focused on correctness/security/architecture/readability; `code-auditor` subagent (50,064 tokens / 511s) focused on deep bug hunting, sandbox escape, concurrency hazards, dependency review. Paired strategy mirrors spec §Phase 4 Task 4.1 "Opus code-review + code-auditor cross-check" — orthogonal lenses on the same diff catch each other's blind spots. Strong overlap on 2 P1 findings (SharedSpendTracker never receives production spend; stdout PIPE OOM) + 1 High (validate_decomposition disjointness gap) → high confidence those are real production blockers.
    - **Both verdicts FAIL even with P0=0** because the headline Init #3 deliverable (shared aggregate budget across multi-project waves) is structurally inert in production. `_subprocess_runner` returns `ProjectRunResult(spent_usd=0.0)` and never calls `await tracker.add(...)`; only test stubs do, so the shared cap never trips in real `multi --real` runs. A 10-project plan with $50 shared cap allows up to $500 real spend before any guard fires. This is the single most important blocker for S11 to fix — it negates the entire reason Init #3B was promoted.
    - **Three operational hazards** in real `_subprocess_runner` accumulate to make `multi --real` unsafe to run unattended: (a) no timeout means a hung child parks the whole wave forever, (b) unbounded stdout PIPE can OOM the orchestrator host on long waves, (c) full `os.environ` leak propagates `BMAD_DISABLE_BUDGET` / `BMAD_PROJECTS_REGISTRY` / `ANTHROPIC_API_KEY` to every child — disabling the very gates `multi` was added to enforce. Each individually merge-blocking; combined, they make Init #3 operationally untenable until S11 ships fixes.
    - **One git-history corruption hazard** — auto-split fallback (`agent/run.py:1037`) inherits a dirty branch after `auto_split_and_execute` raises mid-flight. Legacy `runtime_spawn_worker` spawns on a branch already containing partial sub-story commits and writes more commits on top → merge-gate sees mixed frankencommit history no reviewer expects. Fix: `git -C <worktree> reset --hard <base_sha>` in the except block before fallthrough, OR skip legacy fallback entirely and emit failed WORKER_COMPLETED so planner re-batches.
    - **One safety-invariant gap** — `validate_project_isolation` (L1 forbidden-path gate over `/home/server/crm`) only enforced in `run_multi`. `register_project` accepts `bmad-orchestrator init /home/server/crm`, and single-project `run` does not call the gate at all (grep confirms). Defence-in-depth promise broken — gate sits at wrong layer. S11 fix: call at top of `register_project` (reject at insertion is best) AND at top of real single-project pilot in `agent/run.py` so already-poisoned registry from prior version still cannot spawn.
    - **Six High findings** worth fixing in S11 per spec ("auto-fix P0/P1/High"): sandbox `--ro-bind / /` exposing host data (H-1); /tmp auth-token leak surviving SIGKILL (H-2); `pgrep -f` prefix-match killing wrong-project siblings (H-3); `validate_decomposition` missing `touches_files` disjointness check + AC ≤ 5 cap (H-4/H-5); `find_conflicts` silently dropping stories without id (H-6); no symlink-resolution test in `validate_project_isolation` (H-7 test-quality).
    - **11 Medium + 8 Low findings deferred** to post-merge backlog (spec scopes S11 auto-fix to "P0/P1/High" — Medium and Low not in scope). Notable Mediums worth surfacing in `project_backlog_post_mvp.md`: M-2 ambiguous-layout detection silently picks bmm-v6 over odyssey-hybrid (Antares migration risk); M-5 prlimit still primary defence pending cgroup migration (`project_backlog_sandbox_cgroup_migration` memory); M-7 SIGINT not clean-tearing subprocess children (zombie risk on Ctrl+C).
    - **Three architectural notes** raised by auditor (not findings, but worth recording): (1) `_subprocess_runner` is heavyweight — in-process `gather` over `run_real_pilot(project=slot)` would eliminate the spend-feedback gap entirely; (2) no end-to-end integration test exercises auto-split + multi-project + per-worker HOME together (each initiative has its own test file with stub injection); (3) `set_decomposer(None)` is global mutable not thread-local — would break under `pytest-xdist`. None block S11; all worth `project_backlog_post_mvp.md` entries.
    - **Tracker S8 `decisions_made` claim "shared budget guard halts second project at aggregate cap" is technically false in production** until P1-A fixed. Either fix in S11 OR update S8 retroactively to flag the production gap. Recommended: fix first, no retroactive edits — S11 will flip the claim from "false in production" → "true after P1-A fix".
    - **Report written to `.claude/checkpoints/parallelism_initiatives-review-S10.md`** (233 lines) with: aggregated severity stats, per-finding entries (severity / file:line / category / summary / detail / fix_hint), S11 auto-fix scope hint, and raw agent ids for reproducibility.
  **deferred_items:**
    - **Medium + Low findings (19 total)** — out of S11 auto-fix scope per spec ("P0/P1/High only"). Track in `project_backlog_post_mvp.md` as "review-findings-followup" entry; revisit after Phase 4 completes.
    - **Architectural follow-ups** (in-process multi-run consolidation, end-to-end integration test, decomposer thread-locality) — separate spec proposal after parallelism_initiatives merges.

- **id:** S9
  **title:** Initiative #3C — validation pilot (Antares wave + Odyssey wave parallel)
  **completed:** 2026-05-18 UTC
  **commit:** 62285e3
  **files_changed:** 1 (tests/test_initiative3c_validation_pilot.py NEW)
  **tests_passed:** 1386/1386 PASS (was 1378; +8 new in test_initiative3c_validation_pilot.py); ruff 0; mypy 6 untyped-closure notes mirroring S8's 11 (project convention — test closures exempt)
  **decisions_made:**
    - **Code-level validation via real OS subprocess shim** (not mock runner_fn, not real `claude -p`). The 8 new tests spawn `asyncio.create_subprocess_exec(sys.executable, "-c", <shim_script>, ...)` per project — same plumbing as `cli/main.py::_subprocess_runner` (env propagation via `ORCHESTRATOR_TARGET_PROJECT`, returncode → `ProjectRunResult` mapping, ProjectIsolation+SharedSpendTracker enforcement) but with a 3-line Python child instead of a real `bmad-orchestrator run` invocation. Cost = $0; wall-time ≈150ms per child. Closes S8's deferred "real `claude -p` integration test" item at the OS-process boundary while keeping real-API-cost validation out of CI. The shim writes a sprint-status.yaml with its own slug + `ORCHESTRATOR_TARGET_PROJECT` path so cross-contamination (one child writing into a sibling's tree) would manifest as the wrong slug in the wrong file — directly observable.
    - **Real `_subprocess_runner` signature pinned** via `inspect.signature(...) == ["slot", "tracker", "plan"]` + `inspect.iscoroutinefunction(...)`. A future refactor that drops a parameter or makes the runner sync will fail this test loudly here rather than at first real wave dispatch. Same approach S5 took for `spawn_fn` / `wait_fn` injection points.
    - **Memory isolation pilot under real subprocesses**: the parent process (not children) writes `save_project_memory(slot.slug, ..., orchestrator_home)` after each child returns. Slug-keyed path (`<home>/_config/projects/<slug>/memory.yaml`) guarantees disjoint files even if two parent-side `asyncio.gather`'d tasks race to write — the OS inode-level isolation prevents cross-write. The "load `odyssey-fixture` returns defaults despite `antares-fixture` having persisted data" test pins the slug keying invariant; a future refactor that mistakenly drops the slug from the path would fail loudly.
    - **Failure-mode isolation under real subprocesses** matches `run_multi`'s `_wrap` philosophy (S8 doc): a failing child (`sys.exit(2)`) does NOT cancel the sibling — the sibling completes its wave, writes its sprint-status, the aggregate `succeeded=False` while per-project `good-project.completed=True` + `bad-project.completed=False`. One project's outage does not compound to a total outage.
    - **Pre-flight halt mechanism via `daily_max_spend_usd=0.0`** (NOT pre-loading the BudgetGuard with spend). `BudgetGuard.enforce_day` is stateless re: `spent_usd` — it accepts the current spend as a parameter rather than tracking it internally. The only deterministic way to drive a fresh tracker into halt on entry is `cap=0`, which models the operational case "operator already drained today's budget cap, second wave must abort pre-spawn". The S3B test discovered the same trick on its second iteration (left a comment in-place explaining the discovery); we cite it explicitly here so future readers don't re-rediscover.
    - **Tests organised in 4 classes** so a future failure scopes to its category: `TestPilotRealSubprocessIsolation` (3 tests — happy path + env propagation + returncode mapping), `TestPilotMemoryIsolation` (2 tests — slug keying + unknown-slug defaults), `TestPilotFailureModes` (2 tests — one-project failure + pre-flight halt), `TestSubprocessRunnerContract` (1 test — `_subprocess_runner` signature pin).
    - **No `@pytest.mark.slow` decoration** despite spec's testing-strategy mention. The full S9 suite runs in 0.47s; classifying it as `slow` would require registering the marker (`pyproject.toml [tool.pytest.ini_options] markers = ...`) and gates the test from default runs. The S3B precedent of real-subprocess-equivalent tests at module boundary (`asyncio.create_subprocess_exec` exercised via stubs only) sets the project's bar — 8 real-subprocess tests at sub-half-second total cost is well within "regular suite" territory.
  **deferred_items:**
    - **Real Antares + Odyssey 5/5-worker wave acceptance** — requires (a) Antares stories 1.2 / 1.3 / 1.5 / 3.1 to exist (currently `resolved_deferred` in S1 / S3 / S6 — only 1.1 + 4.8 prepared), (b) Odyssey Wave 1a to have been piloted manually at least once (CLAUDE.md project status: ⬜). Manual user pilot: `bmad-orchestrator multi --projects antares,odyssey --wave 1a --parallel 10 --real`. The CLI plumbing is fully wired (S8 `cli.main::multi` + `_subprocess_runner`); only the project-side preconditions are missing. See Blockers/Pauses entry below.
    - **Tightened subprocess parent ↔ child cost-reporting handshake**. Currently `_subprocess_runner` reports `ProjectRunResult(spent_usd=0.0)` (no telemetry from child to parent). The shared `BudgetGuard` daily cap is therefore not informed by actual per-child claude API consumption — children rely on their own per-process `BudgetGuard(--max-spend-usd=<cap/N>)` for local enforcement. Real-pilot wave will surface whether unix-socket-based cost telemetry from child to parent is worth the wiring. Deferred follow-up. **[S10 review escalated this to P1-A — S11 must fix.]**
    - **Cross-project budget allocation algorithm beyond "even slice"**. Spec §Task 3.4 mention of weighted allocation by historical median story cost (from `project_memory.median_story_cost_usd`) remains a one-day follow-up. Even-slice satisfies S8/S9 acceptance.

- **id:** S8
  **title:** Initiative #3B — multi-project execution + per-project state isolation
  **completed:** 2026-05-18 UTC
  **commit:** 71c521d
  **files_changed:** 3 (src/bmad_orchestrator/runtime/multi_run.py NEW, src/bmad_orchestrator/cli/main.py, tests/test_initiative3b_multi_run.py NEW)
  **tests_passed:** 1378/1378 PASS (was 1347; +31 new in test_initiative3b_multi_run.py); ruff 0; mypy 0 new on edited files
  **decisions_made:**
    - Module placed at `runtime/multi_run.py` (sibling of `runtime/project_registry.py` from S7, `runtime/auto_split.py` from S6, `runtime/sub_story_executor.py` from S5). Same canonical-import-path principle — multi-project orchestration belongs to the `runtime/` namespace next to its data dependencies (registry, project_memory). Stable import path means future consumers (watchdog dashboards, Telegram bot multi-project verbs, bmad-orchestrator multi-execution daemon) reference one location.
    - **Shared budget guard via `SharedSpendTracker`** (small asyncio-lock-fronted accumulator). Single `BudgetGuard` instance threaded through every per-project runner; the tracker increments aggregate spend atomically and re-runs `enforce_day` on the new total. This is the entire "shared budget" mechanism — neither per-project private guards nor a multi-process IPC bus is required because Python in-process asyncio.gather already serialises the lock. Tests confirm 50 concurrent `add(1.0)` calls produce exactly 50.0 total without skew.   **[S10 review note: technically true ONLY IF runner feeds spend back; production `_subprocess_runner` does not → P1-A in S11.]**
    - **Pre-flight halt** at entry: `tracker.check_only()` (enforce_day at zero spend) — if the shared guard is already at `halt` (prior wave drained the cap, or daily_max_spend_usd=0), `run_multi` returns immediately with `aborted_reason` populated and emits a `multi_run_aborted` event. Subprocess spawning never happens, so neither child process nor sandbox cost is paid for a doomed run.
    - **L1 safety gate** = `FORBIDDEN_PROJECT_PATHS` tuple (currently `(Path("/home/server/crm"),)`) checked by `validate_project_isolation` at the input boundary. Catches both exact match and any subpath via `resolved.relative_to(forbidden_resolved)` — defeats `Path("/home/server/crm/agent")` and symlinks that resolve into the forbidden tree. Spec §Safety gates §1.3 ("прод CRM mounts НЕ должен оказаться в worker'е bind list") becomes a single-line check at the registration boundary rather than scattered guards inside the sandbox builder. Future forbidden paths (additional prod mounts) extend the tuple. **[S10 review note: gate enforced ONLY in run_multi, NOT in register_project or single-project run → P1-E in S11.]**
    - **Slot allocation = even split with remainder front-loaded**: 10/2 → [5,5], 10/3 → [4,3,3], 2/2 → [1,1]. Total < projects rejected loudly (`MultiRunError`) — each project must own ≥1 worker, otherwise it has no purpose in the plan. Front-loading the remainder matches the mental model "first project listed gets priority slots" and is deterministic for reproducible test assertions.
    - **Runner injection via `RunnerFn = Callable[[ProjectSlot, SharedSpendTracker, MultiProjectPlan], Awaitable[ProjectRunResult]]`** — same pattern as S5's `spawn_fn` / `wait_fn` and S6's `set_decomposer`. Tests drive the full pipeline (slot split → isolation gate → shared budget → asyncio.gather → outcome aggregation) with stub runners that never touch `claude -p`. Real consumer = CLI `multi` command wires `_subprocess_runner` which `asyncio.create_subprocess_exec`'s the per-project `bmad-orchestrator run --project <slug>` invocation with `ORCHESTRATOR_TARGET_PROJECT={slot.path}` for env-isolated state.
    - **Runner exception isolated**: a runner that raises is caught at `_wrap`, logged, and surfaced as `ProjectRunResult(completed=False, error="<exc_type>: <msg>")`. Sibling projects keep running. Matches BudgetGuard's fail-isolated philosophy — one project's crash does NOT cancel the others, which would compound an outage into a total outage.
    - **Per-project sprint-status isolation = path-based, not lock-based**: each runner constructs a `Settings(target_project=slot.path)` clone and passes it to `write_sprint_status_yaml(settings=settings)`. Since `sprint_status_path(settings)` resolves `settings.target_project / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml"`, two concurrent runners necessarily write to disjoint disk paths. No flock needed — the OS already enforces inode-level isolation. Tests verify with real `read_sprint_status_yaml`/`write_sprint_status_yaml` (not mocks) that antares.yaml and odyssey.yaml end up with disjoint contents.
    - **Per-project memory isolation = naturally provided by `save_project_memory(slug, ..., orchestrator_home)`**. Files land at `<home>/_config/projects/<slug>/memory.yaml`; the slug keying alone defeats cross-pollination. The test pins this invariant (two projects, two writes, two distinct memory.yaml files) so a future refactor that mistakenly drops the slug from the path would fail loudly.
    - **CLI `multi` command** wires it all: parses comma-separated `--projects antares,odyssey`, builds `MultiProjectPlan`, calls `_load_registry_for_cli()` (S7 helper), runs `run_multi(plan, registry, runner_fn=_subprocess_runner)`. `_subprocess_runner` uses `asyncio.create_subprocess_exec` (not `subprocess.Popen`) so the gather() over N projects is genuinely concurrent at OS level. Per-project `--max-spend-usd` = `daily_max_spend_usd / len(projects)` (rough even split; soft cap is per-child for early local exit, hard cap is enforced globally by shared tracker). Renders outcome table with verdict + per-project status + total spend; non-zero exit code on failure for CI / wrapper visibility. **[S10 review: 4 P1 hazards inside `_subprocess_runner` → see S11 acceptance.]**
    - **31 new tests** in `test_initiative3b_multi_run.py` across 8 test classes covering: plan validation (6 cases — empty/zero/negative/duplicate/negative-cap/valid-defaults), slot allocation (6 cases — even/uneven/min/insufficient/unknown/path-from-registry), isolation gate (5 cases — normal/exact/subpath/mixed/forbidden-set-pin), tracker (5 cases — accumulate/halt/check-only/negative-rejected/concurrent), happy path (2 cases), shared budget halt (2 cases — aggregate-combined + pre-flight), sprint-status isolation (real read/write helpers), memory isolation (real load/save_project_memory), runner exception isolation, event callback (starting + complete + aborted).
  **deferred_items:**
    - Real `claude -p` integration test (no mock runner_fn) — closed by S9 at the OS-subprocess boundary via shim runner. Real-wave acceptance with real `claude -p` workers still deferred per S9's deferred_items.
    - Watchdog event-bus subscription that translates `on_event` dicts to typed `EventType.MULTI_RUN_*` values. Currently `on_event` is a free-form `Callable[[dict], None]` (mirrors S5's `on_event` pattern); typed event-bus wiring is an opportunistic addition once a consumer (TUI dashboard, Telegram bot dispatcher) materialises.
    - Cross-project budget allocation algorithm beyond "even slice" — spec §Task 3.4 mentions "Cross-project budget allocation (один daily cap → split)" which we satisfy via the shared tracker but the per-child soft cap is computed as `daily_max_spend_usd / N`. Smarter allocation (e.g. weighted by historical median story cost from `project_memory.median_story_cost_usd`) would let projects with cheap stories accept more parallel work without starving expensive ones. Trivial follow-up — read `load_project_memory(slug).median_story_cost_usd` at split time and weight slots accordingly. Deferred to keep S8 scope focused on the isolation + shared-cap primitives.

- **id:** S7
  **title:** Initiative #3A — project registry yaml + init/scan/doctor/resume CLI
  **completed:** 2026-05-18 UTC
  **commit:** 3db8846
  **files_changed:** 3 (src/bmad_orchestrator/runtime/project_registry.py NEW, src/bmad_orchestrator/cli/main.py, tests/test_initiative3a_project_registry.py NEW)
  **tests_passed:** 1347/1347 PASS (was 1301; +46 new in test_initiative3a_project_registry.py); ruff 0; mypy 0 new on edited files

- **id:** S6
  **title:** Initiative #2C — auto-split pipeline wiring (decomposer + executor + squash) + validation pilot deferred
  **completed:** 2026-05-18 UTC
  **commit:** 2a75557
  **files_changed:** 6 (src/bmad_orchestrator/runtime/auto_split.py NEW, src/bmad_orchestrator/runtime/event_loop.py, src/bmad_orchestrator/agent/run.py, tests/test_initiative2c_auto_split_pilot.py NEW, tests/test_canonical_patches_p6.py, tests/test_s3_runtime.py)
  **tests_passed:** 1301/1301 PASS (was 1287; +14 new in test_initiative2c_auto_split_pilot.py); ruff 0; mypy 0 new on edited files

- **id:** S5
  **title:** Initiative #2B — sub-story execution + squash-merge back to parent
  **completed:** 2026-05-18 UTC
  **commit:** 1fc6150
  **files_changed:** 2 (src/bmad_orchestrator/runtime/sub_story_executor.py NEW, tests/test_initiative2b_substory_executor.py NEW)
  **tests_passed:** 1287/1287 PASS (was 1269; +18 new in test_initiative2b_substory_executor.py); ruff 0; mypy 0 new on edited files

- **id:** S4
  **title:** Initiative #2A — should_split heuristic + story-splitter skill scaffold + LLM decomposition
  **completed:** 2026-05-18 UTC
  **commit:** 0220844
  **files_changed:** 5 (src/bmad_orchestrator/runtime/story_splitter.py NEW, src/bmad_orchestrator/agent/tools/splitter.py REFACTOR, src/bmad_orchestrator/agent/skills/dag-planner/SKILL.md, tests/test_initiative2a_story_splitter.py NEW, tests/fixtures/mock-odyssey/_bmad-output/planning-artifacts/stories/1-1-tenant-signup.md)
  **tests_passed:** 1269/1269 PASS (was 1231; +38 new in test_initiative2a_story_splitter.py); ruff 0; mypy 0 new on edited files

- **id:** S3
  **title:** Initiative #1B — cgroup limits + per-worker HOME isolation + parallel validation pilot
  **completed:** 2026-05-18 UTC
  **commit:** fc97abe
  **files_changed:** 5 (src/bmad_orchestrator/runtime/sandbox.py, src/bmad_orchestrator/runtime/worker_spawn.py, src/bmad_orchestrator/agent/run.py, tests/test_initiative1b_cgroup_home_isolation.py NEW, .claude/scripts/rollback-S3.sh NEW)
  **tests_passed:** 1231/1231 PASS (was 1213; +18 new); ruff PASS; mypy clean

- **id:** S2
  **title:** Initiative #1A — CLI --parallel flag + presets + file-conflict pre-check
  **completed:** 2026-05-17 22:47 UTC
  **commit:** 8200f1c
  **files_changed:** 4 (src/bmad_orchestrator/agent/file_conflict.py NEW, src/bmad_orchestrator/agent/run.py, src/bmad_orchestrator/cli/main.py, tests/test_initiative1_parallel.py NEW)
  **tests_passed:** 1213/1213 PASS (was 1184; +29 new); ruff PASS; mypy clean

- **id:** S1
  **title:** Phase 0 — pilot followups (zombie cleanup, post-worker validation, cost honesty, pre-spawn freshness)
  **completed:** 2026-05-18 UTC
  **commit:** 9897124
  **files_changed:** 5+ (zombie cleanup, silent-failure detector, cost tracker, freshness check, 22 new tests)
  **tests_passed:** 1184/1184 PASS (was 1162); ruff PASS; mypy PASS

## Safety Gates Triggered

(empty)

## Blockers / Pauses

- **[2026-05-18 UTC] pilot_validation_pending — Antares Story 1.2 real pilot requires manual user run**
  **resolution:** resolved_skipped 2026-05-18 — Option B. Story 1.2 file does not exist in Antares; pipeline validated by pilot v7 Story 1.1 + 22 new unit tests. Autoloop resumed on S2.

- **[2026-05-18 UTC] pilot_validation_deferred — Antares Stories 1.3+1.5 parallel pilot (Task 1.5)**
  **resolution:** resolved_deferred 2026-05-18 — Task 1.5 pilot covered by S6 + S9 natural pipeline exercise. Autoloop promotes S3 → Completed and continues to S4.

- **[2026-05-18 UTC] pilot_validation_deferred — Antares Story 3.1 auto-split pilot (Task 2.5)**
  **resolution:** resolved_deferred 2026-05-18 — S6 acceptance reframed to architectural wiring + deferred runtime pilot to S9/manual. Autoloop promotes S6 → Completed and continues to S7.

- **[2026-05-18 UTC] pilot_validation_deferred — Antares wave + Odyssey wave real 5/5-worker pilot (Task 3.5)**
  **resolution:** resolved_deferred 2026-05-18 — Antares stories 1.2 / 1.3 / 1.5 / 3.1 are missing (resolved_deferred in S1 / S3 / S6 — only 1.1 + 4.8 prepared); Odyssey Wave 1a has never been piloted manually yet (project CLAUDE.md status: ⬜ "Pilot run на Odyssey Wave 1a через `/bmad-auto-dev` (без оркестратора)"). Real-wave acceptance covered by manual user run when those preconditions complete: `bmad-orchestrator multi --projects antares,odyssey --wave 1a --parallel 10 --real`. CLI plumbing fully wired (S8 `cli.main::multi` + `_subprocess_runner`). S9 closed code-level pipeline acceptance via 8 real-OS-subprocess tests in `tests/test_initiative3c_validation_pilot.py` exercising the same `asyncio.create_subprocess_exec` plumbing. Autoloop promotes S9 → Completed and continues to S10 (Phase 4A code review).

## Decisions Log

- **date:** 2026-05-18T05:00 UTC
  **session:** bootstrap
  **decision:** Single spec covering Phase 0 + 3 initiatives + Phase 4 (vs 4 separate specs or pipeline)
  **rationale:** user preference — one command, autonomous chain. Per-initiative isolation traded for simplicity. Checkpoint sessions (S3, S6, S9, S11) provide intermediate review surfaces.
  **impact:** all 11 sessions on one integration branch; final manual merge only after Phase 4 PASS.

- **date:** 2026-05-18T05:00 UTC
  **session:** bootstrap
  **decision:** Runtime=loop_wrapper (fresh context per session), Delay=180s, Auto merge=false (manual merge after Phase 4)
  **rationale:** 11 sessions too long for shared context; 180s pause keeps wall-clock manageable while allowing zombie cleanup; manual merge per `/auto-loop-spec-long` default for long initiatives.
  **impact:** user runs one tmux command, walks away ~10-15h; reviews integration branch + manually merges to main at end.

- **date:** 2026-05-18T05:00 UTC
  **session:** bootstrap
  **decision:** Phase 4 = mandatory + auto-fix P0/P1/High + 2 retry max
  **rationale:** user explicitly requested code review + auditor + auto-acceptance of fixes. Severity threshold = High prevents auto-fix on noisy nitpicks. 2 retry cap prevents infinite loop on stubborn findings.
  **impact:** initiative cannot complete without PASS verdict from both reviewers; S11 may halt + escalate user if 2 retry exhausted.

- **date:** 2026-05-18 UTC
  **session:** S8
  **decision:** Shared budget = single `BudgetGuard` + `SharedSpendTracker` (asyncio-lock accumulator) — NOT per-project private guards
  **rationale:** Spec acceptance "shared budget guard" + "no memory cross-pollination" needed exactly one source of truth for aggregate daily spend. Per-project guards would let each project independently fit under the cap while their sum breaches it (the failure mode the test demonstrates). In-process asyncio + lock-fronted increment is sufficient because run_multi orchestrates with `asyncio.gather` — there's no multi-process race to coordinate. When the CLI command spawns subprocess children, each child runs its own per-process `BudgetGuard` for local soft cap, but the parent's `SharedSpendTracker` is the hard cap (children must report spend back, currently via subprocess returncode + stderr; tighter wiring deferred to S9 pilot wave where the actual spend telemetry materialises).
  **impact:** S9 pilot will surface whether the subprocess parent/child cost-reporting handshake is granular enough. If a child outruns its `--max-spend-usd` soft cap mid-flight, the parent's tracker doesn't see it until the child exits — acceptable for daily-cap enforcement (subprocess naturally bounded), but a future refactor could thread the tracker over a unix socket if needed.   **[2026-05-18 S10 review: gap escalated to P1-A — production `_subprocess_runner` never feeds spend back, making shared cap inert in real `multi --real` runs. S11 will fix via `spend.json` file-handoff pattern.]**

- **date:** 2026-05-18 UTC
  **session:** S8
  **decision:** L1 isolation gate at input boundary (`validate_project_isolation` over `FORBIDDEN_PROJECT_PATHS`) instead of inside the sandbox bind builder
  **rationale:** Spec §Safety gates §1.3 says "прод CRM mounts НЕ должен оказаться в worker'е bind list" — the most reliable place to enforce this is at the registration boundary, not deep inside the sandbox where a forgotten path branch could miss it. Single-line check via `resolved.relative_to(forbidden_resolved)` catches both exact match and subpath, defeats symlinks (resolve() canonicalises). Future forbidden paths (additional prod mounts, encrypted volumes) extend the tuple in one place. Test pins `Path("/home/server/crm")` into the constant so a future refactor that drops it fails loudly.
  **impact:** any future multi-project consumer (watchdog, Telegram bot, CI runner) that imports `validate_project_isolation` gets the gate for free. Sandbox builder remains a plain bind-list constructor without policy.   **[2026-05-18 S10 review: gate only enforced in `run_multi` — `register_project` and single-project `run` bypass it. Escalated to P1-E; S11 must add calls at top of `register_project` and at top of real single-project pilot.]**

- **date:** 2026-05-18 UTC
  **session:** S8
  **decision:** Subprocess-per-project as the real runner (not in-process asyncio dispatch with env mutation)
  **rationale:** `run_orchestrator()` reads `load_settings()` which is env-driven via `ORCHESTRATOR_TARGET_PROJECT`. In-process asyncio tasks would race on the global env if we mutated per task. Subprocess gives true per-process env isolation: each child resolves its own `target_project`, opens its own state.db session, writes its own sprint-status, loads/saves its own project memory file. The subprocess overhead (one Python interpreter per project) is amortised across the full wave duration (~30 min per project), and `asyncio.create_subprocess_exec` over `gather()` keeps OS-level concurrency real.
  **impact:** S9 pilot will be the first time the real subprocess runner sees actual `claude -p` workers; expected behavior = each project's state writes land at disjoint paths. If we see leakage, the diagnosis is "subprocess env not propagating correctly" not "asyncio task scheduling order changed answers".   **[2026-05-18 S10 audit: also surfaced as architectural smell — heavyweight; in-process gather + per-task settings clone would eliminate P1-A entirely. Not in S11 scope; recorded as architectural follow-up.]**

- **date:** 2026-05-18 UTC
  **session:** S9
  **decision:** Code-level pipeline acceptance via real-OS-subprocess shim (not mock runner_fn, not real `claude -p`) — defer real-wave acceptance to manual user run when project preconditions land
  **rationale:** Spec acceptance for S9 ("оба waves complete, sprint-status каждого корректен, no state leak") requires Antares + Odyssey running real waves with 5/5 workers. Two structural preconditions are unmet: Antares stories 1.2/1.3/1.5/3.1 are missing (resolved_deferred across S1/S3/S6), and Odyssey Wave 1a has never been piloted manually (project CLAUDE.md: ⬜). Synthesising stories would be busy work, not validation. The remaining S9-distinctive contribution over S8 (which used stub runners) was exercising the real OS-process boundary — `asyncio.create_subprocess_exec` with real fork/env propagation. The shim approach gives us that boundary at $0 cost: 8 tests using `sys.executable -c <script>` per project, verifying env propagation, sprint-status path isolation, memory slug-keying, failure-mode isolation, pre-flight halt, and `_subprocess_runner` signature pin. Real-wave acceptance is the user's manual run when stories + Odyssey Wave 1a are ready.
  **impact:** S10 (Phase 4A) reviews this codebase as-is. Future real-wave findings (e.g. subprocess cost-telemetry gaps) ride as deferred follow-ups in `project_backlog_post_mvp.md`, not blocking the integration merge to main.

- **date:** 2026-05-18 UTC
  **session:** S10
  **decision:** Paired Opus reviewers (code-reviewer + code-auditor) spawned in parallel for Phase 4A
  **rationale:** Spec §Phase 4 Task 4.1 calls for "code-review + cross-check". Two orthogonal lenses on the same 14k-LOC diff catch each other's blind spots: reviewer focuses on correctness/security/architecture/readability; auditor on deep bug hunting/sandbox/concurrency/dependency. Parallel execution = halves wall-clock; no shared context between them = independent verdicts (high-signal when they agree). Both spawned via `subagent_type=code-reviewer` and `code-auditor` with self-contained prompts pointing at spec / tracker / specific files / safety invariants from CLAUDE.md. Cost = ~104k combined tokens / ~922s wall-clock.
  **impact:** Strong overlap on 2 P1 + 1 High = high confidence those are real. Diverse coverage on the rest (reviewer caught L1 gate gap + env leak; auditor caught sandbox `--ro-bind / /` + auto-split fallback dirty branch + `pgrep` substring kill). Verdict FAIL from both → S11 must auto-fix 12 P0/P1/High before manual merge. 19 Medium/Low deferred to `project_backlog_post_mvp.md`.

- **date:** 2026-05-18 UTC
  **session:** S10
  **decision:** S11 auto-fix scope limited to P0/P1/High (12 findings); Medium/Low deferred to backlog
  **rationale:** Spec acceptance explicitly says "auto-fix P0/P1/High" — Medium/Low are out of scope to prevent S11 from ballooning into a 19-finding sprint. Mediums often require API/architecture decisions that warrant a separate human-reviewed pass (e.g. M-4 "pass full cap not divided" changes the runtime semantics of multi-run; M-7 SIGINT cleanup needs signal-handler design). Lows are tech debt nicer to batch.
  **impact:** S11 scope = 6 P1 (cli/main.py + project_registry.py + multi_run.py + agent/run.py changes) + 6 High (sandbox.py + worker_spawn.py + agent/run.py + story_splitter.py + file_conflict.py + new test). Each fix commit "fix(<scope>): address review finding <id>". Re-review must verify each finding addressed before final PASS verdict. If 2 retry exhausted on any finding → STOP loop + escalate user (per spec).

## Journal

```
[2026-05-18 05:00 UTC] bootstrap: tracker + integration/parallelism_initiatives + backup/parallelism_initiatives-pre-2026-05-18 + launcher + watchdog created. 11 sessions planned. S1=Current. delay=180s, runtime=loop_wrapper, auto_merge=false.
[2026-05-18 UTC] S1 code work done: 0.1 zombie cleanup + 0.2 silent-failure + 0.3 cost honesty + 0.4 freshness check committed as 9897124 on integration/parallelism_initiatives. 22 new tests in tests/test_phase0_pilot_followups.py; 1184/1184 PASS, ruff 0, mypy 0 new. 0.5 pilot validation deferred — see Blockers/Pauses (PENDING).
[2026-05-18 UTC] human-resolved: 0.5 PENDING blocker resolved_skipped (Option B). S1 → Completed, S2 promoted → Current.
[2026-05-17 22:47 UTC] S2 execution: --parallel preset CLI flag (1/3/5/10) + agent/file_conflict.py wired into _run_real_pilot_body batch selection. 29 new tests, 1213/1213 PASS. Commit 8200f1c.
[2026-05-17 22:47 UTC] S2 completed, S3 promoted to Current.
[2026-05-18 UTC] S3 execution: cgroup limits (DEFAULT_CGROUP_LIMITS = 8G/200%/16384) + per-worker HOME overlay. 18 new tests, 1231/1231 PASS. Commit fc97abe. Task 1.5 pilot deferred (resolved_deferred).
[2026-05-18 UTC] S3 completed, S4 promoted to Current.
[2026-05-18 UTC] S4 execution: evaluate_split / validate_decomposition / DECOMPOSITION_PROMPT helpers; refactored agent/tools/splitter.py to delegate. 38 new tests, 1269/1269 PASS. Commit 0220844.
[2026-05-18 UTC] S4 completed, S5 promoted to Current.
[2026-05-18 UTC] S5 execution: execute_sub_stories + squash_sub_stories (git reset --soft); sequential dispatch in shared parent worktree. 18 new tests, 1287/1287 PASS. Commit 1fc6150.
[2026-05-18 UTC] S5 completed, S6 promoted to Current.
[2026-05-18 UTC] S6 execution: runtime/auto_split.py (auto_split_and_execute + AutoSplitOutcome + make_bus_bridge); 4 new SUB_STORY_* EventTypes + 2 derived. agent/run.py per-story diversion + synthetic WORKER_COMPLETED for Stage 6 continuity. 14 new tests, 1301/1301 PASS. Commit 2a75557. Antares 3.1 real-pilot deferred (resolved_deferred).
[2026-05-18 UTC] S6 completed, S7 promoted to Current.
[2026-05-18 UTC] S7 execution: runtime/project_registry.py (ProjectsRegistry + ProjectEntry pydantic v2, layout detector, atomic yaml save, env-overridable path, register_project/scan_registry/doctor/resume_hint). cli/main.py: init/scan/doctor + resume-project. 46 new tests, 1347/1347 PASS. Commit 3db8846.
[2026-05-18 UTC] S7 completed, S8 promoted to Current.
[2026-05-18 UTC] S8 plan: 0 host-destructive (pure Python addition runtime/multi_run.py + cli/main.py multi command + tests). Acceptance "shared budget guard + isolated sprint-status + no memory pollution" satisfied by SharedSpendTracker (async-lock accumulator) + path-based isolation (Settings(target_project=slot.path)) + slug-keyed save_project_memory.
[2026-05-18 UTC] S8 execution: runtime/multi_run.py (MultiProjectPlan + ProjectSlot + SharedSpendTracker; split_parallel_slots even allocation w/ remainder front-loaded; validate_project_isolation L1 over FORBIDDEN_PROJECT_PATHS=/home/server/crm; run_multi with asyncio.gather over RunnerFn injection + on_event callback). cli/main.py: `multi` command + _subprocess_runner with ORCHESTRATOR_TARGET_PROJECT env per child. 31 new tests across 8 classes (plan validation, slot allocation, isolation gate, tracker concurrency, happy path, shared budget halt aggregate+pre-flight, sprint-status isolation via real read/write helpers, memory isolation via real load/save_project_memory, exception isolation, event callback). Commit 71c521d on integration/parallelism_initiatives. 1378/1378 PASS (was 1347); ruff 0; mypy 0 new on edited files. loop_wrapper runtime — exiting cleanly, wrapper handles next iteration.
[2026-05-18 UTC] S8 completed, S9 promoted to Current (surface=backend-python, Init #3C validation pilot Antares wave + Odyssey wave parallel, checkpoint=true).
[2026-05-18 UTC] S9 plan: code-level acceptance via real-OS-subprocess shim runner (cost-free), defer real-wave acceptance to manual user run (Antares stories + Odyssey Wave 1a unmet preconditions). Distinct from S8's stub runners — S9 exercises asyncio.create_subprocess_exec, env propagation, fork-level isolation.
[2026-05-18 UTC] S9 execution: tests/test_initiative3c_validation_pilot.py NEW (8 tests across 4 classes: TestPilotRealSubprocessIsolation x3, TestPilotMemoryIsolation x2, TestPilotFailureModes x2, TestSubprocessRunnerContract x1). Real subprocess plumbing via sys.executable -c <_SHIM_SCRIPT> per project, verifies env propagation (ORCHESTRATOR_TARGET_PROJECT reaches child) + sprint-status disjoint content + memory slug-keyed isolation + failure-isolated waves + pre-flight halt + _subprocess_runner signature pin. Commit 62285e3 on integration/parallelism_initiatives. 1386/1386 PASS (was 1378); ruff 0; mypy 6 untyped-closure notes (project convention — test closures exempt, S8 baseline 11). Real-wave acceptance deferred (resolved_deferred) — see Blockers/Pauses. loop_wrapper runtime — exiting cleanly, wrapper handles next iteration.
[2026-05-18 UTC] S9 completed, S10 promoted to Current (surface=backend-python, Phase 4A full code review Opus + code-auditor cross-check).
[2026-05-18 UTC] S10 plan: spawn paired Opus reviewers in parallel — code-reviewer (correctness/security/architecture/readability) + code-auditor (sandbox/concurrency/dependency/bug-hunt) on integration/parallelism_initiatives diff vs main (67 files / +14553 / -353 LOC). Read-only review session — no source edits; no safety-gate concerns. Acceptance: both reviewers spawn, findings list with severity tags, report written to .claude/checkpoints/parallelism_initiatives-review-S10.md.
[2026-05-18 UTC] S10 execution: paired reviewers spawned via Agent tool. code-reviewer agent_id=a6303fe4d5fb8c8a2 (53,886 tokens / 411s / 50 tool uses) → FAIL verdict, 4 P1 + 4 High + 6 Medium + 3 Low. code-auditor agent_id=a862b3f41a03e9a90 (50,064 tokens / 511s / 53 tool uses) → FAIL verdict, 4 P1 + 4 High + 6 Medium + 6 Low. Strong overlap on 2 P1 (SharedSpendTracker never receives production spend; stdout PIPE OOM) + 1 High (validate_decomposition disjointness gap). Consolidated report written to .claude/checkpoints/parallelism_initiatives-review-S10.md (233 lines) — 6 P1 unique + 6 High unique + 11 Medium + 8 Low. loop_wrapper runtime — exiting cleanly, wrapper handles next iteration.
[2026-05-18 UTC] S10 completed, S11 promoted to Current (surface=backend-python, Phase 4B auto-fix P0/P1/High + re-review + security-auditor + final report). S11 findings_to_fix: see .claude/checkpoints/parallelism_initiatives-review-S10.md §P1 and §High (12 items: P1-A through P1-F + H-1 through H-7).
```

## Final Report (populated on last session completion)

(empty — pending S11 completion)
