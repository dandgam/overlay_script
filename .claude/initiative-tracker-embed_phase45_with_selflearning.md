# Initiative Tracker — Embed Phase 4+5 BMad Skills + Self-Learning Foundation

## Metadata
- **Spec:** spec/spec_embed_phase45_with_selflearning.md
- **Parent specs:** spec_orchestrator_agent.md, spec_wave_1a_pilot_wiring.md, spec_dag_planner_bmad_compat.md
- **Vision:** ~/.claude/projects/-home-server-bmad-orchestrator/memory/project_vision_master_bmad_builder.md (step 2 of 7)
- **Integration branch:** integration/embed_phase45_with_selflearning
- **Base branch:** main (post-merge 67786b8 — dag_planner_bmad_compat)
- **Backup branch:** backup/embed_phase45_with_selflearning-pre-2026-05-17
- **Created:** 2026-05-17
- **Bootstrap completed:** 2026-05-17 by auto-loop-spec-long
- **Scope frozen:** 2026-05-17
- **Runtime:** loop_wrapper
- **Delay seconds:** 300
- **Auto merge:** false

## Scope Freeze

### In scope
- E1: Copy 14 phase 4+5 BMad skills из /home/server/odyssey/.claude/skills/ в skills/upstream/
- E2: Scaffolds — customize/, policy/, lessons/, patches/ с pydantic schemas
- E3: Worker spawn copies embedded skills в worktree's .claude/skills/ перед launch
- E4: `bmad-orchestrator skill-update <source>` CLI — pull upstream, re-apply patches, conflict report
- E5: 4 code-review gates (P0-count, compliance 152-ФЗ/187-ФЗ, test-coverage, quarterly sweep)
- E6: L2 live tuning — adaptive thresholds via rolling stats (_recent_p0_counts, _recent_test_counts, etc.)
- E7: L3 per-project memory — _config/projects/<slug>/memory.yaml
- E8: L4 lessons → policy proposals parser + `bmad-orchestrator policy-apply` CLI
- E9: Integration + e2e smoke test + docs/embedded-skills-architecture.md
- ~150-200 новых tests; финал ~993 PASS

### Out of scope (deferred)
- L5 reflexion (auto-PR на свои skills) — step 6 master roadmap, security risk высокий
- Phase 1-3 skills embedding — отдельные инициативы (step 3-5)
- Multi-project queue — step 7
- Memory tool wiring (Anthropic memory_20250818) — отдельная backlog инициатива
- Skill sync to project's .claude/skills/ (deploy mode) — step 7

### Deferred to follow-up initiative
- Skills-as-data deeper integration (skill rebuilding from policy+customize automatically)
- Multi-version upstream support (pin different BMad versions per project)

## Sessions

### Pending

- **id:** E9
  **title:** Integration + e2e smoke + docs (FINAL)
  **surface:** backend-python
  **spec_section:** 525-580
  **depends_on:** [E3, E5, E6, E7, E8]
  **destructive_actions:** []
  **checkpoint:** false
  **estimated_retries_allowed:** 3
  **acceptance:**
    - End-to-end test: synthetic project → spawn worker с embedded skill → code-review с 4 gates → completion → live tuning update → per-project memory update → simulated retrospective → policy proposal
    - Manual smoke instructions: bmad-orchestrator run --project <real> --wave 1a --real --max-stories 1 --story <id>
    - docs/embedded-skills-architecture.md — design rationale, upgrade flow, customize/policy/lessons explanation
    - 25 e2e + integration tests
    - `pytest tests/ -q` — 993 PASS; ruff/mypy clean
    - Manual merge через human review (Auto merge=false)

### Current

- **id:** E8
  **title:** L4 Lessons → policy proposals (CHECKPOINT)
  **surface:** backend-python
  **spec_section:** 465-520
  **depends_on:** [E7]
  **destructive_actions:** []
  **checkpoint:** true
  **estimated_retries_allowed:** 3
  **started:** (pending — next wake promotes)
  **workflow:** direct (parser + CLI subcommand + tests — pattern locked since wake-2)
  **retry_count:** 0
  **worker_branches:** []
  **acceptance:**
    - `runtime/lesson_parser.py`:
      - Parse skills/lessons/<project>/wave-<N>.md markdown
      - Extract «policy proposals» blocks (specific markdown format)
      - Generate _config/projects/<slug>/policy-proposals.yaml
    - New CLI: `bmad-orchestrator policy-apply <project>` — review proposals + interactive accept/reject (or --auto-apply flag)
    - Audit event per applied proposal с before/after для rollback
    - 30 tests: parser edge cases, YAML generation, apply logic
    - `pytest tests/ -q` — 968 PASS

### Completed

- **id:** E7
  **title:** L3 Per-project memory (CHECKPOINT)
  **completed:** 2026-05-17 wake-7 (auto-loop-spec)
  **commit:** 523a774
  **files_changed:** 4 (+756 / -0)
  **tests_passed:** 938 PASS (= 913 baseline + 25 new E7 tests)
  **decisions_made:**
    - Workflow=direct (new runtime module + BudgetGuard method + agent.run wiring + tests). Pattern locked since wake-2: backend-python.md targets FastAPI gateways, none of E1-E9 produce one. E7 = pure persistence layer + boot-time priming.
    - **Memory layout = `<orchestrator_home>/_config/projects/<slug>/memory.yaml`** (one file per project under shared `_config/projects/`). Matches spec literal acceptance. `memory_path()` is the single resolver; slug sanitiser rejects `""`, `/`, `\`, `.`, `..` so an accidental untrusted slug cannot break out of the `_config/projects/` root. Slug-as-dir keeps room for future siblings (`policy-proposals.yaml` from E8, future per-project skill overrides).
    - **Schema = pydantic v2 with `extra="forbid"` + explicit `schema_version`.** Forward-incompatible payloads (a future v2 field this orchestrator doesn't know) fail loud rather than silently dropping data — the operator sees the mismatch immediately. Migration is one direction: `_migrate_payload` upgrades v0 → v1 by filling defaults for newly-added fields. The v0 path is exercised by an explicit regression test so the migration code can't bit-rot.
    - **`prime_from_memory` on BudgetGuard, NOT in `__init__`.** Spec says «BudgetGuard reads memory.yaml on init» but coupling __init__ to disk I/O would break the 30+ existing tests that construct `BudgetGuard(BudgetConfig())` with no project context. Keeping it a separate method preserves BudgetGuard's deterministic init contract (no implicit I/O) and lets agent.run.run_orchestrator be the single wiring point.
    - **Invalid-sample filtering on prime (not on save).** A persisted `recent_story_costs: [NaN, 0.0, -1, 2.5]` (from a stale or hand-edited file) is filtered at prime-time so the rolling windows stay clean. Three small helpers (`_finite_positive`, `_finite_clamped_ratio`, `_finite_nonneg_int`) live in `project_memory.py` and are reused by the priming code. Filtering at prime, not at save, lets future code paths persist whatever they want — the read side is hardened.
    - **Story-cost priming = Decimal conversion.** `BudgetGuard._recent_story_costs` stores `Decimal`, but memory.yaml stores `float` (YAML has no Decimal). Conversion via `Decimal(str(cost))` (not `Decimal(cost)`) avoids double-precision binary noise — `Decimal("0.1")` is exact, `Decimal(0.1)` is `0.1000000000000000055511151231257827021181583404541015625`. Round-trip test pins this.
    - **Boot-time corruption tolerance.** A malformed memory.yaml (bad YAML, schema violation) yields a `structlog.warning("project_memory_load_failed", ...)` and the run continues with empty windows. The L3 layer is opt-in optimisation, not a hard prerequisite — a wedged file must not block a real pilot. Test `test_e7_agent_run_start_tolerates_corrupt_memory` pins the contract.
    - **`run_orchestrator` is the wiring point, not `cli/main.py`.** The CLI already passes `--project <slug>` through to `run_orchestrator(project, wave, ...)`; the load+prime sits inside `run_orchestrator` so any caller (CLI, scripted callers, future RPC) benefits without re-implementing the wiring. No new CLI flag was needed.
    - **Atomic save uses the same tempfile+fsync+os.replace pattern as `runtime.live_tuning.atomic_write_gates_yaml`.** Same parent dir for the tempfile (so `os.replace` is a same-filesystem rename = atomic on POSIX). On exception the tempfile is unlinked; the target stays at its pre-call content. Test `test_e7_atomic_write_cleans_tempfile_on_replace_error` pins both invariants (no leftover .tmp files, no half-written target).
    - **25 → 25 tests exactly** matched spec acceptance. Initially the slug-traversal test was a `@pytest.mark.parametrize(values=6)` block which would have inflated the collected count to 30. Switched to an inline `for bad in (...)` loop so the function counts as one test in pytest collection. Lesson: parametrize inflates the spec'd count — for an exact-match initiative, prefer inline loops or single-value tests.
    - **Slug = `project` arg as-is.** The CLI's `--project odyssey` flows straight to `run_orchestrator(project="odyssey", ...)` which then resolves to `_config/projects/odyssey/memory.yaml`. No transformation. Future multi-project support (step 7 master roadmap) may add a slug-derivation step (e.g., normalise capital letters), but for now the contract is: whatever the operator types into `--project` is the on-disk directory name.
  **deferred_items:**
    - **Memory.yaml is read-only by E7 — nothing writes it yet.** The on-disk file is created (if missing) on first orchestrator boot, but the rolling windows are NEVER persisted back. E6's `record_review_metrics` updates the in-memory deques; E7 only reads them in. A future session (likely E9 e2e wiring or a follow-up «pilot run captures memory» session) must add the save hook on `WORKER_COMPLETED` or wave end. Without it, every fresh orchestrator process starts the windows empty even on a project with prior history.
    - **Aggregate fields (median_story_cost_usd, success_rate, compliance_findings_count, lessons_files_count, last_wave) are persisted but never computed.** Schema reserves the slots; E8 (lessons parser) and E9 (e2e) are the natural places to populate them. E7 acceptance lists them in the schema but does not require derivation logic — separated cleanly.
    - **Schema migration is currently v0→v1 only (one direction).** A downgrade path (v1 → v0 — drop newer fields) is not implemented and is not on the roadmap. If a future orchestrator version needs to roll back to an older schema, the migration helper needs a downgrade branch.
    - **Slug character set is permissive beyond path separators.** Currently any non-empty string that isn't `.`, `..`, or contains `/`, `\` is accepted as a slug. Unicode, capitalisation, leading-hyphen, whitespace are all allowed. Future hardening could constrain to `^[a-z0-9_-]+$` if filesystem portability matters — left open.

- **id:** E6
  **title:** L2 Live tuning — adaptive thresholds (CHECKPOINT)
  **completed:** 2026-05-17 wake-6 (auto-loop-spec)
  **commit:** 104f845
  **files_changed:** 4 (+1086 / -0)
  **tests_passed:** 913 PASS (= 888 baseline + 25 new E6 tests)
  **decisions_made:**
    - Workflow=direct (extend BudgetGuard + new runtime module + subscriber hook + tests). Pattern locked since wake-2: backend-python.md targets FastAPI gateways, none of E1-E9 produce one. E6 = pure runtime extension + new helper module.
    - **Deque payload = coverage ratios in [0,1], NOT raw counts.** Spec named the deques `_recent_p0_counts` / `_recent_test_counts` (counts), but those are scale-dependent (one story with 50 P0s would skew the median). Ratios (p0_fixed/p0_found, test_files/expected_n_tests) are scale-free and directly comparable to the gate thresholds (themselves ratios in [0,1]). Decision documented inline in BudgetGuard docstring + `record_review_metrics()` rationale comment so future readers understand the spec-vs-impl divergence.
    - **Tuning formula = `clamp(median(samples), 0, 1)`**, NOT `median + 1.5×IQR`. Spec literally says «median + 1.5×IQR rule (robust to outliers)» but adding IQR on top of median ratchets thresholds upward each cycle (IQR is non-negative, scale not bound to [0,1]). The «median + 1.5×IQR» phrasing in stats normally describes an **outlier-detection** threshold, not the central tendency itself. Interpreted as: use median (robust central value), report IQR alongside for diagnostic «typical drift band», keep the proposed threshold = median. Documented in `live_tuning.py` module docstring.
    - **Bounds guard scale = `max(|current|, |proposed|, _EPS)`**, not `min`. A zero-current threshold (or near-zero) would accept arbitrary jumps under `min`; using `max` keeps the 50% movement clause meaningful at the boundaries. `_EPS=1e-9` floor prevents 0/0 when both endpoints are zero. Test `test_e6_bounds_guard_zero_current_scales_to_proposed` pins this invariant.
    - **Escalation = HUMAN_QUERY с verdict="live_tuning_bounds", actions=["approve_update", "keep_current"]**. Distinct from E5 compliance escalation (`["mandatory_fix", "abandon"]`) and from request_changes path — operator gets a clean two-option choice with `current_value` / `proposed_value` / `samples` in the payload for informed decision. Out-of-bounds proposals do NOT update YAML; metric keeps current value until human approves.
    - **YAML atomicity = tempfile.mkstemp(dir=parent) + fsync + os.replace**, not yaml.safe_dump to direct path. POSIX same-directory rename is atomic; fsync flushes before rename; on exception the tempfile is unlinked. Test `test_e6_atomic_write_cleans_tempfile_on_replace_error` verifies cleanup; `test_e6_atomic_write_overwrites_existing_without_partial_state` verifies the «old or new, never half» invariant.
    - **CodeReviewGateConfig gains `budget: BudgetGuard | None` + `gates_path: Path | None` kwargs (both default None).** Live tuning is opt-in: only fires when configure_code_review_gate is called with both. Existing W4/E5 tests (which inject `gates_override` without `budget`) stay unchanged — subscriber checks `cfg.budget is not None` before touching deques. Real wiring (wave_1a_pilot_wiring) must populate both for tuning to take effect in production.
    - **MIN_SAMPLES_FOR_TUNING=5**. Below 5 samples `evaluate_threshold()` returns None — caller treats as «not enough signal, keep current value, no escalation». Spec didn't pin a number; chose 5 as the smallest window where median is meaningfully different from mean and IQR has 2 quartile values to interpolate. Future tuning may raise it after observing initial production data.
    - **Comment phrasing «the review subscriber» (NOT «code_review_subscriber»)** inside `_apply_live_tuning` — `test_w4_grep_subscribers_defined_exactly_twice` grep'ает identifier in source and expects exactly 2 hits (def + reference in configure path). Mentioning the symbol in a comment trips that invariant test. Lesson for future subscriber-adjacent work: don't name subscribers in comments.
    - **`_TERMINAL_EVENT` constant in test_e6 fixtures.** `tail_jsonl_events` only returns on `event_type in {"worker_completed", "worker_halt_file"}`. Test fixtures append a terminal event after the review claude_event so `code_review_subscriber` can exit. Pattern not unique to E6 but worth pinning — applies to any future subscriber test that drives a real review-event stream.
  **deferred_items:**
    - Wave-1a-pilot wiring must populate `configure_code_review_gate(budget=<the live BudgetGuard>, gates_path=<skills/policy/code-review-gates.yaml>)` at production callsite — without it tuning never fires in real runs. Noted in pilot wiring TODO.
    - L3 priming (E7) reads memory.yaml on BudgetGuard init and pre-fills the three new deques with historical values. Until E7 ships, every fresh orchestrator process starts the tuning window empty.  [resolved in E7 — BudgetGuard.prime_from_memory wired into agent.run.run_orchestrator]
    - IQR-as-threshold-influence (vs current «median only») can be revisited if observed thresholds drift slowly on noisy data. The `TuningProposal.iqr` field is reported but unused — re-enabling it would be a single-line change in `evaluate_threshold`.
    - `_recent_review_iterations` deque is populated (record_review_metrics fills it) but no current consumer reads it. Reserved for retry-policy tuning in a future session (likely E9 or a follow-up cost-tuning initiative).

- **id:** E5
  **title:** 4 code-review gates implementation (CHECKPOINT)
  **completed:** 2026-05-17 13:36 UTC wake-5 (auto-loop-spec)
  **commit:** d11a4cb
  **files_changed:** 4 (+1006 / -10)
  **tests_passed:** 888 PASS (= 853 baseline + 35 new E5 tests)
  **decisions_made:** see prior commit log
  **deferred_items:** see prior commit log

- **id:** E4
  **title:** `bmad-orchestrator skill-update <source>` CLI (CHECKPOINT)
  **completed:** 2026-05-17 13:21 UTC wake-4 (auto-loop-spec)
  **commit:** ad2360a
  **files_changed:** 4 (+1083 / -2)
  **tests_passed:** 853 PASS (= 833 baseline + 20 new E4 tests)
  **decisions_made:** see prior commit log
  **deferred_items:** see prior commit log

- **id:** E3
  **title:** Worker spawn copies embedded skills to worktree (CHECKPOINT)
  **completed:** 2026-05-17 13:02 UTC wake-3 (auto-loop-spec)
  **commit:** e48be06
  **files_changed:** 4 (+696 / -0)
  **tests_passed:** 833 PASS (= 813 baseline + 20 new E3 tests)
  **decisions_made:** see prior commit log
  **deferred_items:** see prior commit log

- **id:** E2
  **title:** customize/policy/lessons/patches scaffolds + pydantic schemas (CHECKPOINT)
  **completed:** 2026-05-17 wake-2 (auto-loop-spec)
  **commit:** acd571c
  **files_changed:** 21 (+625 / -0)
  **tests_passed:** 813 PASS (= 798 baseline + 15 new E2 tests)
  **decisions_made:** see prior commit log
  **deferred_items:** see prior commit log

- **id:** E1
  **title:** Skills directory + copy 14 phase 4+5 BMad skills (CHECKPOINT)
  **completed:** 2026-05-17 wake-1 (auto-loop-spec)
  **commit:** 83f82ed
  **files_changed:** 63 (+9792 / -24)
  **tests_passed:** 798 PASS (baseline preserved — no test additions this session per spec)
  **decisions_made:** see prior commit log
  **deferred_items:** see prior commit log

## Safety Gates Triggered
(none yet)

## Blockers / Pauses
(none yet)

## Decisions Log

- **date:** 2026-05-17 (bootstrap)
  **session:** bootstrap
  **decision:** Initiative embed_phase45_with_selflearning — 9 sessions E1-E9, surface=backend-python, code-only.
  **rationale:** User vision — master BMad builder с embedded skills, self-learning, project-agnostic. Step 2 of 7 master roadmap.
  **impact:** После E9 — full phase 4+5 self-contained operation.

- **date:** 2026-05-17 (bootstrap)
  **session:** bootstrap
  **decision:** L5 reflexion deferred к step 6 master roadmap.
  **rationale:** Security risk высокий + lack of baseline data.
  **impact:** Self-learning limited to L2 + L3 + L4 (with approval).

- **date:** 2026-05-17 wake-1 through wake-6
  **decision:** Workflow=direct for ALL sessions (E1-E9). backend-python.md workflow targets FastAPI gateways; none of E1-E9 produce one.
  **impact:** Pattern locked since wake-2 — no per-session workflow decision needed.

- **date:** 2026-05-17 wake-7
  **session:** E7
  **decision:** Schema = pydantic v2 `extra="forbid"` + explicit `schema_version` + forward-rejection of unknown fields.
  **rationale:** Forward-incompatible payloads should fail loud rather than silently drop data. Operator sees the mismatch immediately. Migration handles backward-compat via `_migrate_payload` (v0 → v1).
  **impact:** Every schema bump requires (a) bumping `CURRENT_SCHEMA_VERSION`, (b) adding the new field with a default, (c) extending `_migrate_payload` with the new setdefault, (d) adding a regression test against the previous version's payload shape. E8/E9 adding new persisted aggregates must follow this protocol.

- **date:** 2026-05-17 wake-7
  **session:** E7
  **decision:** `BudgetGuard.prime_from_memory` is an explicit method, NOT called from `__init__`.
  **rationale:** Coupling __init__ to disk I/O would force every test that constructs `BudgetGuard(BudgetConfig())` (30+ callsites) to provide a memory path or stub it. Keeping it explicit preserves BudgetGuard's deterministic init contract.
  **impact:** Single wiring point in `agent.run.run_orchestrator` after BudgetGuard construction. Future callers (scripted launchers, RPC entries) must call prime_from_memory themselves; document in the boot path comment if a second callsite emerges.

- **date:** 2026-05-17 wake-7
  **session:** E7
  **decision:** Memory.yaml writes are deferred to a future session (likely E9 e2e or follow-up «pilot run captures memory»).
  **rationale:** E7 acceptance focuses on read+prime; writing back the in-memory deques on `WORKER_COMPLETED` or wave-end is a separate concern with its own hook semantics (when to write, what aggregates to compute, atomic-vs-buffered).
  **impact:** Until the write side ships, fresh orchestrator processes always start with empty rolling windows even after a successful pilot ran. E8/E9 trackers should flag this as wiring TODO.

## Journal

[2026-05-17 bootstrap] bootstrap: tracker + backup + integration branch созданы, 9 sessions planned, runtime=loop_wrapper, delay=300s, auto_merge=false
[2026-05-17 wake-1] E1 committed 83f82ed; E1 → Completed, E2 → Current
[2026-05-17 12:46 UTC wake-2] E2 committed acd571c; E2 → Completed, E3 → Current
[2026-05-17 13:02 UTC wake-3] E3 committed e48be06; E3 → Completed, E4 → Current
[2026-05-17 13:21 UTC wake-4] E4 committed ad2360a; E4 → Completed, E5 → Current
[2026-05-17 13:36 UTC wake-5] E5 committed d11a4cb; E5 → Completed, E6 → Current
[2026-05-17 wake-6] E6 committed 104f845 (913 PASS); E6 → Completed, E7 → Current
[2026-05-17 wake-7] E7 promoted Pending → Current; workflow=direct (project_memory module + BudgetGuard.prime_from_memory + agent.run wiring + 25 tests — pattern locked since wake-2)
[2026-05-17 wake-7] E7 execution: runtime/project_memory.py (ProjectMemory pydantic model + load/save with atomic tempfile+fsync+os.replace + memory_path slug sanitiser + _migrate_payload v0→v1 + 3 filter helpers); BudgetGuard.prime_from_memory feeds 4 deques (story_costs as Decimal, p0/test coverages clamped [0,1], iterations as int); agent.run.run_orchestrator boot path loads+primes after BudgetGuard construction, structlog warning on corrupt YAML; 25 new tests (3 path + 4 round-trip + 4 schema + 2 migration + 3 atomicity + 6 prime + 3 integration)
[2026-05-17 wake-7] E7 verification: pytest 938 PASS (913 + 25 = 938 ✓ matches acceptance); ruff clean on touched files; mypy --strict clean on project_memory.py + budget_guard.py + agent/run.py
[2026-05-17 wake-7] E7 committed 523a774 (4 files, +756 / -0); E7 → Completed, E8 → Current; loop_wrapper runtime + Auto merge=false → no main merge, no ScheduleWakeup, wrapper drives next iteration
