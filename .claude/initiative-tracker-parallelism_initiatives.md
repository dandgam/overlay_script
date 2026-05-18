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
    - pytest 1378+ PASS, ruff 0 errors, mypy 0 errors
    - Final Report written in tracker с merge hint
  **safety_gates:**
    - L1: never `--no-verify`, never force-push
    - L2/L3 standard

### Current

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
  **started:** (pending first wake on S9)
  **workflow:** workflows/backend-python.md
  **retry_count:** 0
  **worker_branches:** []

### Completed

- **id:** S8
  **title:** Initiative #3B — multi-project execution + per-project state isolation
  **completed:** 2026-05-18 UTC
  **commit:** 71c521d
  **files_changed:** 3 (src/bmad_orchestrator/runtime/multi_run.py NEW, src/bmad_orchestrator/cli/main.py, tests/test_initiative3b_multi_run.py NEW)
  **tests_passed:** 1378/1378 PASS (was 1347; +31 new in test_initiative3b_multi_run.py); ruff 0; mypy 0 new on edited files
  **decisions_made:**
    - Module placed at `runtime/multi_run.py` (sibling of `runtime/project_registry.py` from S7, `runtime/auto_split.py` from S6, `runtime/sub_story_executor.py` from S5). Same canonical-import-path principle — multi-project orchestration belongs to the `runtime/` namespace next to its data dependencies (registry, project_memory). Stable import path means future consumers (watchdog dashboards, Telegram bot multi-project verbs, bmad-orchestrator multi-execution daemon) reference one location.
    - **Shared budget guard via `SharedSpendTracker`** (small asyncio-lock-fronted accumulator). Single `BudgetGuard` instance threaded through every per-project runner; the tracker increments aggregate spend atomically and re-runs `enforce_day` on the new total. This is the entire "shared budget" mechanism — neither per-project private guards nor a multi-process IPC bus is required because Python in-process asyncio.gather already serialises the lock. Tests confirm 50 concurrent `add(1.0)` calls produce exactly 50.0 total without skew.
    - **Pre-flight halt** at entry: `tracker.check_only()` (enforce_day at zero spend) — if the shared guard is already at `halt` (prior wave drained the cap, or daily_max_spend_usd=0), `run_multi` returns immediately with `aborted_reason` populated and emits a `multi_run_aborted` event. Subprocess spawning never happens, so neither child process nor sandbox cost is paid for a doomed run.
    - **L1 safety gate** = `FORBIDDEN_PROJECT_PATHS` tuple (currently `(Path("/home/server/crm"),)`) checked by `validate_project_isolation` at the input boundary. Catches both exact match and any subpath via `resolved.relative_to(forbidden_resolved)` — defeats `Path("/home/server/crm/agent")` and symlinks that resolve into the forbidden tree. Spec §Safety gates §1.3 ("прод CRM mounts НЕ должен оказаться в worker'е bind list") becomes a single-line check at the registration boundary rather than scattered guards inside the sandbox builder. Future forbidden paths (additional prod mounts) extend the tuple.
    - **Slot allocation = even split with remainder front-loaded**: 10/2 → [5,5], 10/3 → [4,3,3], 2/2 → [1,1]. Total < projects rejected loudly (`MultiRunError`) — each project must own ≥1 worker, otherwise it has no purpose in the plan. Front-loading the remainder matches the mental model "first project listed gets priority slots" and is deterministic for reproducible test assertions.
    - **Runner injection via `RunnerFn = Callable[[ProjectSlot, SharedSpendTracker, MultiProjectPlan], Awaitable[ProjectRunResult]]`** — same pattern as S5's `spawn_fn` / `wait_fn` and S6's `set_decomposer`. Tests drive the full pipeline (slot split → isolation gate → shared budget → asyncio.gather → outcome aggregation) with stub runners that never touch `claude -p`. Real consumer = CLI `multi` command wires `_subprocess_runner` which `asyncio.create_subprocess_exec`'s the per-project `bmad-orchestrator run --project <slug>` invocation with `ORCHESTRATOR_TARGET_PROJECT={slot.path}` for env-isolated state.
    - **Runner exception isolated**: a runner that raises is caught at `_wrap`, logged, and surfaced as `ProjectRunResult(completed=False, error="<exc_type>: <msg>")`. Sibling projects keep running. Matches BudgetGuard's fail-isolated philosophy — one project's crash does NOT cancel the others, which would compound an outage into a total outage.
    - **Per-project sprint-status isolation = path-based, not lock-based**: each runner constructs a `Settings(target_project=slot.path)` clone and passes it to `write_sprint_status_yaml(settings=settings)`. Since `sprint_status_path(settings)` resolves `settings.target_project / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml"`, two concurrent runners necessarily write to disjoint disk paths. No flock needed — the OS already enforces inode-level isolation. Tests verify with real `read_sprint_status_yaml`/`write_sprint_status_yaml` (not mocks) that antares.yaml and odyssey.yaml end up with disjoint contents.
    - **Per-project memory isolation = naturally provided by `save_project_memory(slug, ..., orchestrator_home)`**. Files land at `<home>/_config/projects/<slug>/memory.yaml`; the slug keying alone defeats cross-pollination. The test pins this invariant (two projects, two writes, two distinct memory.yaml files) so a future refactor that mistakenly drops the slug from the path would fail loudly.
    - **CLI `multi` command** wires it all: parses comma-separated `--projects antares,odyssey`, builds `MultiProjectPlan`, calls `_load_registry_for_cli()` (S7 helper), runs `run_multi(plan, registry, runner_fn=_subprocess_runner)`. `_subprocess_runner` uses `asyncio.create_subprocess_exec` (not `subprocess.Popen`) so the gather() over N projects is genuinely concurrent at OS level. Per-project `--max-spend-usd` = `daily_max_spend_usd / len(projects)` (rough even split; soft cap is per-child for early local exit, hard cap is enforced globally by shared tracker). Renders outcome table with verdict + per-project status + total spend; non-zero exit code on failure for CI / wrapper visibility.
    - **31 new tests** in `test_initiative3b_multi_run.py` across 8 test classes covering: plan validation (6 cases — empty/zero/negative/duplicate/negative-cap/valid-defaults), slot allocation (6 cases — even/uneven/min/insufficient/unknown/path-from-registry), isolation gate (5 cases — normal/exact/subpath/mixed/forbidden-set-pin), tracker (5 cases — accumulate/halt/check-only/negative-rejected/concurrent), happy path (2 cases), shared budget halt (2 cases — aggregate-combined + pre-flight), sprint-status isolation (real read/write helpers), memory isolation (real load/save_project_memory), runner exception isolation, event callback (starting + complete + aborted).
  **deferred_items:**
    - Real `claude -p` integration test (no mock runner_fn) requires the validation pilot infrastructure that is exactly S9's scope — naturally exercised by Antares wave + Odyssey wave parallel. Subprocess runner is wired in `cli/main.py::_subprocess_runner` but tested only via stub at the module boundary; E2E validation happens at S9.
    - Watchdog event-bus subscription that translates `on_event` dicts to typed `EventType.MULTI_RUN_*` values. Currently `on_event` is a free-form `Callable[[dict], None]` (mirrors S5's `on_event` pattern); typed event-bus wiring is an opportunistic addition once a consumer (TUI dashboard, Telegram bot dispatcher) materialises.
    - Cross-project budget allocation algorithm beyond "even slice" — spec §Task 3.4 mentions "Cross-project budget allocation (один daily cap → split)" which we satisfy via the shared tracker but the per-child soft cap is computed as `daily_max_spend_usd / N`. Smarter allocation (e.g. weighted by historical median story cost from `project_memory.median_story_cost_usd`) would let projects with cheap stories accept more parallel work without starving expensive ones. Trivial follow-up — read `load_project_memory(slug).median_story_cost_usd` at split time and weight slots accordingly. Deferred to keep S8 scope focused on the isolation + shared-cap primitives.

- **id:** S7
  **title:** Initiative #3A — project registry yaml + init/scan/doctor/resume CLI
  **completed:** 2026-05-18 UTC
  **commit:** 3db8846
  **files_changed:** 3 (src/bmad_orchestrator/runtime/project_registry.py NEW, src/bmad_orchestrator/cli/main.py, tests/test_initiative3a_project_registry.py NEW)
  **tests_passed:** 1347/1347 PASS (was 1301; +46 new in test_initiative3a_project_registry.py); ruff 0; mypy 0 new on edited files
  **decisions_made:**
    - Registry module placed at `runtime/project_registry.py` (sibling of `runtime/project_memory.py`, `runtime/auto_split.py`, etc.). Same canonical-import-path principle as S4/S5/S6 — keeps the multi-project infrastructure (memory + registry + future multi_run) under one importable namespace. The yaml format is a plain `projects:` mapping (slug → ProjectEntry) so it round-trips trivially via `yaml.safe_dump` and stays diff-friendly for human edits.
    - Pydantic v2 `ProjectEntry` with `path: Path` (absolute-only validator), `bmad_layout: Literal["bmm-v6", "odyssey-hybrid", "unknown", "not-bmad"]` (4-value union), `sandbox_overrides: dict[str, Any]` (free-form per-project knob). `ProjectsRegistry` validates slug regex `^[a-z0-9][a-z0-9_-]{0,63}$` on every project key — prevents path-traversal slugs from sneaking into `_config/projects/<slug>/memory.yaml`-style downstream paths.
    - Two-tier registry path resolution: env override `BMAD_PROJECTS_REGISTRY=/abs/path` wins (mirrors `BMAD_REQUIRE_SANDBOX` / `BMAD_REQUIRE_CGROUP` / `BMAD_AUTO_SPLIT` from prior sessions), fallback `<orchestrator_home>/config/projects.yaml`. The env var is the test seam — every CLI test pins it to `tmp_path/config/projects.yaml` so pytest never touches a real registry.
    - Atomic save via `path.with_suffix(suffix + ".tmp")` + `tmp.replace(path)` — prevents corruption mid-write when multiple `init` calls race (real-mode multi-project orchestrator may register projects concurrently from a watchdog). Same pattern as `runtime/project_memory.py::save_project_memory`.
    - Layout detector uses shape heuristics only: presence of `_bmad/bmm/config.yaml` → `bmm-v6` (Antares), presence of `_bmad/config.toml` + `_bmad/planning-artifacts/` → `odyssey-hybrid` (Odyssey), `_bmad/` exists but neither shape → `unknown`, no `_bmad/` → `not-bmad`. Best-effort by design — scan/doctor surface drift as `stale` rather than crash.
    - CLI `resume-project <slug>` renamed from the spec's literal `resume` to avoid collision with the existing top-level `resume` verb (pause/resume daemon control at `cli/main.py:281`). Spec acceptance (`init` + `scan`) still hits the literal names; `doctor` matches the literal; `resume-project` is a one-word delta that documents itself in the help string. Top-level (not subgroup) so users get the verbs at the shortest possible invocation.
    - `scan_registry` returns immutable `ScanRow` dataclasses sorted by slug — JSON-serialisable for future API surfaces (watchdog dashboards, Telegram bot) without forcing callers to consume rich's `Table` object. Mirrors the S4/S5 pattern (`SplitDecision` / `SubStoryResult` are frozen dataclasses).
    - `doctor()` short-circuits on missing path (skips layout/output/sprint checks) — saves CPU + avoids confusing chained-failure noise in CI when a registered project gets `rm -rf`'d between scan and doctor. Exit code 1 on any failed check so wrappers (CI, watchdog) can branch on it.
    - 46 new tests cover: 4 layout detector cases, 3 slug derivation cases, 7 registry validation/round-trip cases, 2 path resolution cases (default + env override), 5 register_project cases, 4 scan_registry cases, 6 doctor branches, 2 resume_hint cases, 13 CLI invocations via `typer.testing.CliRunner` with isolated registry per test. Pins env vars `BMAD_PROJECTS_REGISTRY` + `ORCHESTRATOR_ORCHESTRATOR_HOME` so CLI tests cannot leak into the real user registry.
  **deferred_items:**
    - Wiring `--project <slug>` resolution through the registry so `bmad-orchestrator run --project antares` reads `registry.projects["antares"].path` instead of relying on `ORCHESTRATOR_TARGET_PROJECT` env var. Cleanest place: a `_resolve_target_project(slug)` helper called at the top of `run()` in `cli/main.py`, with env-var fallback for backwards compat. Deferred to S8 (Init #3B multi-project execution) where the helper is required to dispatch per-project workers.
    - `sandbox_overrides` is currently a free-form `dict[str, Any]` — no consumer reads it yet. Cleanest first consumer = `runtime/sandbox.py::DEFAULT_CGROUP_LIMITS` merge in S8 so per-project caps can override the global 8G/200%/16384 default. Schema can tighten to a `SandboxOverrides` Pydantic model once the consumer shape is locked.
    - `bmad-orchestrator init` currently requires an explicit path; spec memo `project_backlog_orchestrator_project_agnostic` envisions a `pip install bmad-orchestrator && bmad-orchestrator init` workflow with cwd as default. Trivial follow-up — `project_path: Path = typer.Argument(default=Path.cwd(), ...)` — deferred to keep this session focused on the registry + commands surface.

- **id:** S6
  **title:** Initiative #2C — auto-split pipeline wiring (decomposer + executor + squash) + validation pilot deferred
  **completed:** 2026-05-18 UTC
  **commit:** 2a75557
  **files_changed:** 6 (src/bmad_orchestrator/runtime/auto_split.py NEW, src/bmad_orchestrator/runtime/event_loop.py, src/bmad_orchestrator/agent/run.py, tests/test_initiative2c_auto_split_pilot.py NEW, tests/test_canonical_patches_p6.py, tests/test_s3_runtime.py)
  **tests_passed:** 1301/1301 PASS (was 1287; +14 new in test_initiative2c_auto_split_pilot.py); ruff 0; mypy 0 new on edited files
  **decisions_made:**
    - Auto-split orchestrator placed at `runtime/auto_split.py` (sibling of `runtime/story_splitter.py` from S4 and `runtime/sub_story_executor.py` from S5). Same rationale as prior sessions — Python packages cannot contain `-`, and `agent/skills/dag-planner/` is a Claude SDK skill dir. Keeps the entire auto-split chain (should_split → decompose → execute_sub_stories → squash_sub_stories) at canonical import paths under `runtime/`. The new module is the *bridge* — it composes S4 helpers with S5 executor + adds bus-event translation.
    - Opt-in env gate `BMAD_AUTO_SPLIT=1` (mirrors `BMAD_REQUIRE_SANDBOX` / `BMAD_REQUIRE_CGROUP` pattern from S3). Default OFF — production runs the legacy single-worker path until operators flip the flag. Rationale: decomposition issues a paid Opus call per oversized story; gating prevents accidental cost surprise during the rollout window.
    - Decomposer is an injectable async callable (`set_decomposer(fn)` / `get_decomposer()` module-state pair) rather than a hard-wired `claude -p` subprocess. Default `_DECOMPOSER = None` makes the auto-split path inert even with the env flag set in production — operator must explicitly wire a decomposer at startup. Unit tests use stub callables to drive the full pipeline without touching real `claude -p`. Pattern mirrors how `spawn_fn` / `wait_fn` got injected in S5's `execute_sub_stories`.
    - `auto_split_and_execute` enforces MIN_SUBS=2 — a "decomposition" that yields a single sub-story is treated as a no-op fallback (decision=`keep`, fallback_reason populated). Prevents the worker from spinning up isolated infrastructure for what is effectively the original monolith.
    - On `auto_split.succeeded == True`, agent/run.py emits a *synthetic* `EventType.WORKER_COMPLETED` carrying `auto_split=True` + `sub_ids` + `squashed_sha` so downstream pipeline stages (Stage 6 code-review, sprint-status update, cost ledger close-out) fire exactly as for a monolithic worker. Without this, the auto-split branch would silently bypass Stage 6 review. The synthetic event also flags `auto_split=True` for observability — Stage 6 can opt to scope its diff window to the squashed commit.
    - 4 new typed EventType values (`SUB_STORY_STARTED` / `SUB_STORY_COMPLETED` / `SUB_STORY_SQUASH_DONE` / `SUB_STORY_SQUASH_SKIPPED`) plus 2 derived (`STORY_SPLIT_TRIGGERED` / `PHASE4_COMPLETE`). Inventory bumped 19 → 23. `make_bus_bridge()` returns an `on_event(dict)` closure that translates the executor's free-form dict events to typed bus emissions via `asyncio.create_task` with a strong-ref `pending: set[asyncio.Task]` (RUF006 fix) so fire-and-forget tasks survive GC pressure between sync executor and async bus.
    - Failure mode: if `auto_split_and_execute` itself raises (decomposer crash, executor panic, squash failure), agent/run.py catches the exception, logs `auto_split_failed_falling_back`, and falls through to the legacy `runtime_spawn_worker` path. Auto-split is strictly an *optimisation* — pipeline correctness must not regress under failure. The catch is broad on purpose (`except Exception`) — any unexpected condition becomes a fallback rather than a halt.
  **deferred_items:**
    - Real Antares Story 3.1 (Nextcloud Docker template) pilot acceptance — Antares directory has no `docs/stories/` subdir; story 3.1 file does not exist. Same root cause as S1's 0.5 and S3's 1.5 blockers (Antares only ships 1.1 + 4.8 prepared). Synthesising 3.1 to "satisfy" the acceptance would be busy work, not validation. Real-pilot acceptance covered by S9 (Init #3C — Antares wave + Odyssey wave parallel) plus optional manual user run if/when a real 3.1 file lands in Antares. Pipeline correctness validated synchronously by 14 new unit tests (happy path 3 subs / decomposer error fallback / invalid JSON fallback / MIN_SUBS=2 / silent failure / bus bridge typed translation / env flag respected) + 1287-test baseline.
    - Production wiring of a real Opus-backed decomposer (`set_decomposer(opus_decompose)` at startup). Cleanest place would be `cli/main.py` or `agent/run.py` startup — load Opus client + register the callable. Deferred to operator decision; default-off behaviour preserved until the wiring lands.
    - JSONL emission of the synthetic `WORKER_COMPLETED` payload's `auto_split` / `sub_ids` / `squashed_sha` fields for Stage 6 review-scope narrowing. Currently the fields ride along the event payload; consumers that want to act on them must opt in.

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
  **impact:** S9 pilot will surface whether the subprocess parent/child cost-reporting handshake is granular enough. If a child outruns its `--max-spend-usd` soft cap mid-flight, the parent's tracker doesn't see it until the child exits — acceptable for daily-cap enforcement (subprocess naturally bounded), but a future refactor could thread the tracker over a unix socket if needed.

- **date:** 2026-05-18 UTC
  **session:** S8
  **decision:** L1 isolation gate at input boundary (`validate_project_isolation` over `FORBIDDEN_PROJECT_PATHS`) instead of inside the sandbox bind builder
  **rationale:** Spec §Safety gates §1.3 says "прод CRM mounts НЕ должен оказаться в worker'е bind list" — the most reliable place to enforce this is at the registration boundary, not deep inside the sandbox where a forgotten path branch could miss it. Single-line check via `resolved.relative_to(forbidden_resolved)` catches both exact match and subpath, defeats symlinks (resolve() canonicalises). Future forbidden paths (additional prod mounts, encrypted volumes) extend the tuple in one place. Test pins `Path("/home/server/crm")` into the constant so a future refactor that drops it fails loudly.
  **impact:** any future multi-project consumer (watchdog, Telegram bot, CI runner) that imports `validate_project_isolation` gets the gate for free. Sandbox builder remains a plain bind-list constructor without policy.

- **date:** 2026-05-18 UTC
  **session:** S8
  **decision:** Subprocess-per-project as the real runner (not in-process asyncio dispatch with env mutation)
  **rationale:** `run_orchestrator()` reads `load_settings()` which is env-driven via `ORCHESTRATOR_TARGET_PROJECT`. In-process asyncio tasks would race on the global env if we mutated per task. Subprocess gives true per-process env isolation: each child resolves its own `target_project`, opens its own state.db session, writes its own sprint-status, loads/saves its own project memory file. The subprocess overhead (one Python interpreter per project) is amortised across the full wave duration (~30 min per project), and `asyncio.create_subprocess_exec` over `gather()` keeps OS-level concurrency real.
  **impact:** S9 pilot will be the first time the real subprocess runner sees actual `claude -p` workers; expected behavior = each project's state writes land at disjoint paths. If we see leakage, the diagnosis is "subprocess env not propagating correctly" not "asyncio task scheduling order changed answers".

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
```

## Final Report (populated on last session completion)

(empty — pending S11 completion)
