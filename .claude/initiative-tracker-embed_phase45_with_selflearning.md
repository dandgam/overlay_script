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
- **Initiative completed:** 2026-05-17 wake-9 (auto-loop-spec); awaiting manual merge to main
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
(empty — initiative complete)

### Current
(empty — initiative complete; awaiting manual merge to main)

### Completed

- **id:** E9
  **title:** Integration + e2e smoke + docs (FINAL)
  **completed:** 2026-05-17 wake-9 (auto-loop-spec)
  **commit:** 3b700b2
  **files_changed:** 2 (+1190 / -0)
  **tests_passed:** 993 PASS (= 968 baseline + 25 new E9 tests ✓ matches acceptance)
  **decisions_made:**
    - Workflow=direct (one integration test file + one docs file). Pattern locked since wake-2: backend-python.md workflow targets FastAPI gateways, none of E1-E9 produce one. E9 = pure integration tests + docs.
    - **Single comprehensive e2e test walks every step from synthetic project to applied policy proposal** (`test_e9_full_pipeline_synthetic_project_to_policy_proposal_chain`). Steps numbered 1-10 in-line so a reader can map test sections to spec acceptance prose. Two sibling variants (`p0_gate_overrides_approve_to_reject`, `compliance_violation_short_circuits_to_human_query`) cover the two gate branches that the happy-path test bypasses.
    - **Compliance gate semantics = mandatory-fix on tag MATCH, not on tag absence.** Initial test draft passed `compliance_tags=("152-ФЗ", "187-ФЗ")` in approve-flow metrics — that tripped the compliance escalation because the gate treats those tags as «mandatory-fix triggers». Fix: omit tags in approve-flow tests; include only one matching tag (`"152-ФЗ"`) in the violation test. Documented inline; no production code change.
    - **Test file organised into 7 groups (A-G)** — full pipeline e2e (3), embedded skills → worker (3), memory loop (4), lessons → policy (5), CLI policy-apply (3), docs (3), grep DoD (4) = 25 exactly. Same grouping convention as test_e6 / test_w5 so the test file maps 1:1 to the spec's acceptance bullets.
    - **`test_e9_chain_memory_corrupt_yaml_returns_defaults_loud` asserts fail-loud on invalid YAML** (`ProjectMemoryInvalidError`), not silent fallback to defaults. Matches E7 wake-7 decision that schema mismatches raise rather than auto-recover — operator must see the corruption immediately.
    - **CLI tests use `typer.testing.CliRunner`** with explicit `--skills-root` + `--orchestrator-home` overrides so they don't touch the real `_config/` or `skills/`. Same pattern as the s4/s6/s8/E8 test suites.
    - **Grep DoD tests** verify cross-module wiring stays in place: `apply_embedded_skills` in `worker_spawn.py`, `_apply_live_tuning` + `code_review_subscriber` in `agent/run.py`, `prime_from_memory` in `agent/run.py`, `policy-apply` subcommand in `cli/main.py`. Cheap regression guards against accidental dewiring during future refactors.
    - **Docs file (~190 lines) covers all five required sections** (design rationale, component layout, upgrade flow, worker spawn overlay path, 4-layer self-learning) + manual smoke instructions (`bmad-orchestrator run --project ... --wave 1a --max-stories 1 --story <id>`) + operational safety guarantees + tests pointer + out-of-scope list. Section presence asserted by `test_e9_docs_has_required_sections` so future doc edits cannot silently drop a section.
    - **Memory persistence proven via round-trip rather than via new subscriber hook.** E7 deferred a writer-on-WORKER_COMPLETED hook to a future session; E9 instead demonstrates the round-trip in `test_e9_chain_memory_prime_then_review_then_persist_roundtrip` (operator-driven persist after each wave is sufficient for V1 — auto-persist is a follow-up).
  **deferred_items:**
    - **Pre-existing ruff I001 error in tests/test_w1_real_pilot.py:290** (un-sorted imports in a local block). Not introduced by E9; left alone per «ruff clean on touched files» scope used by previous wakes. Worth a one-line cleanup in a future maintenance pass.
    - **Memory writer hook (BudgetGuard → memory.yaml on WORKER_COMPLETED).** E9 e2e demonstrates the manual round-trip; an automatic subscriber would close the loop without operator action. Deferred — pick up when first real pilot retro lands.
    - **Smoke harness as actual CLI subcommand.** E9 ships smoke instructions in docs (`bmad-orchestrator run --project ... --wave 1a --real --max-stories 1`), but no dedicated `bmad-orchestrator smoke` subcommand. Reasonable for V1 — operators will invoke the standard `run` command. Revisit if smoke-mode flags accumulate.
    - **Lessons writer still not implemented** (E8 deferral, unchanged in E9). `policy-apply` reports «no proposals found» until a retrospective run writes `skills/lessons/<project>/wave-*.md`. The retrospective skill is embedded (bmad-retrospective) but the orchestrator does not yet drive it.
    - **Manual merge to main pending** — Auto merge=false per /auto-loop-spec-long default. User must run `git checkout main && git merge --no-ff integration/embed_phase45_with_selflearning -m "merge embed_phase45_with_selflearning E1..E9"` after reviewing the integration branch.

- **id:** E8
  **title:** L4 Lessons → policy proposals (CHECKPOINT)
  **completed:** 2026-05-17 wake-8 (auto-loop-spec)
  **commit:** 2240e72
  **files_changed:** 3 (+1170 / -0)
  **tests_passed:** 968 PASS (= 938 baseline + 30 new E8 tests)
  **decisions_made:** see prior commit log
  **deferred_items:** see prior commit log

- **id:** E7
  **title:** L3 Per-project memory (CHECKPOINT)
  **completed:** 2026-05-17 wake-7 (auto-loop-spec)
  **commit:** 523a774
  **files_changed:** 4 (+756 / -0)
  **tests_passed:** 938 PASS (= 913 baseline + 25 new E7 tests)
  **decisions_made:** see prior commit log
  **deferred_items:** see prior commit log

- **id:** E6
  **title:** L2 Live tuning — adaptive thresholds (CHECKPOINT)
  **completed:** 2026-05-17 wake-6 (auto-loop-spec)
  **commit:** 104f845
  **files_changed:** 4 (+1086 / -0)
  **tests_passed:** 913 PASS (= 888 baseline + 25 new E6 tests)
  **decisions_made:** see prior commit log
  **deferred_items:** see prior commit log

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
(none — all 9 sessions were code-only, no destructive actions taken)

## Blockers / Pauses

- **date:** 2026-05-17 wake-9
  **session:** E9
  **type:** manual_merge_pending
  **detail:** Initiative complete on integration/embed_phase45_with_selflearning (E1..E9, 9 feature commits + 9 tracker commits + 1 bootstrap = 19 commits; +17708 / -10 across 106 files; 993 PASS). User must merge manually:
      git checkout main
      git merge --no-ff integration/embed_phase45_with_selflearning -m "merge embed_phase45_with_selflearning E1..E9"
  **resolution:** PENDING (user action — manual merge to main)

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

- **date:** 2026-05-17 wake-1 through wake-9
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

- **date:** 2026-05-17 wake-8
  **session:** E8
  **decision:** Lesson markdown contract pinned to `## Policy proposal: <file>.<field>` header + `before:` / `after:` / optional `rationale:` lines, parsed via `yaml.safe_load` on the value.
  **rationale:** Spec said «specific markdown format» without pinning syntax. Chose the most operator-readable shape so lessons can be hand-edited or LLM-generated. YAML value parsing reuses the same library as the rest of the policy stack.
  **impact:** Future lesson writers (E9 e2e simulation, real retrospective output) MUST emit blocks matching `_PROPOSAL_HEADER_RE`. Unknown policy file OR field raises `LessonProposalInvalidError` — operator sees the typo on next `policy-apply` run.

- **date:** 2026-05-17 wake-8
  **session:** E8
  **decision:** `apply_proposal` validates via `pydantic.model_validate` BEFORE the atomic write — disk content never moves through an invalid state.
  **rationale:** A bad `after` value (e.g. `p0_threshold=2.0`) would silently land on disk if the write came first. Validating first means the policy YAML stays at its pre-call content on any schema violation; the audit log + the `PolicyApplyError` give the operator a clean signal.
  **impact:** Every future policy schema field MUST be declared on its pydantic model so `model_validate` can gate it. Loose-typed dict policies (none currently) would bypass this protection.

- **date:** 2026-05-17 wake-8
  **session:** E8
  **decision:** Batch errors collected in `ApplyResult.errors` rather than raised; CLI exits non-zero only after all proposals processed.
  **rationale:** A 10-proposal batch with one bad proposal should still apply the other 9 — aborting on the first failure makes lesson batches brittle. Operator sees the failed proposal in the CLI summary and can fix the lesson file for next run.
  **impact:** Callers consuming `apply_proposals_batch` programmatically must check `result.errors` themselves — silent skip is intentional for batch mode but could surprise a one-off caller. Document in the docstring.

- **date:** 2026-05-17 wake-9
  **session:** E9
  **decision:** Single comprehensive e2e test (`test_e9_full_pipeline_synthetic_project_to_policy_proposal_chain`) walks every step from synthetic project to applied policy proposal in a single function, with steps numbered 1-10 in-line so a reader can map test sections to spec acceptance prose.
  **rationale:** Spec acceptance prose lists the chain as a single narrative («synthetic project → spawn worker → embedded skill → code-review → completion → live tuning → per-project memory → lesson → policy proposal»). Splitting this into 8 micro-tests would lose the «single coherent walk» property. Sibling variants cover gate branches (p0 override, compliance violation) that the happy-path test bypasses.
  **impact:** Future regressions on the chain show up in one test with a clear step number in the assertion stack trace. Adding a new chain step (e.g. lessons writer hook) means inserting a new numbered step rather than authoring a new test file.

- **date:** 2026-05-17 wake-9
  **session:** E9
  **decision:** Compliance-gate test wiring — approve-flow tests omit compliance tags in metrics; only the dedicated violation test includes a matching tag.
  **rationale:** `_gate_compliance` semantics = «these tags require mandatory fix» (tag match on findings triggers escalation). Initial test draft passed all-matching tags in approve-flow metrics, tripping compliance escalation instead of letting the chain reach the verdict. Documented inline in `_approve_event` helper.
  **impact:** Future tests adding compliance-tag scenarios must remember the «tag = mandatory-fix trigger» semantic. The helper signature reflects this (default = no tags).

- **date:** 2026-05-17 wake-9
  **session:** E9
  **decision:** Memory persistence demonstrated via operator-driven round-trip in e2e test, NOT via a new auto-persist subscriber hook on `WORKER_COMPLETED`.
  **rationale:** Auto-persist has its own design questions (when to write — per story / per wave / on shutdown; what to recompute — median / mean / p99; how to handle concurrent writes from multiple worker subscribers). V1 ships the round-trip primitives (`save_project_memory`, `prime_from_memory`); the hook is a follow-up once first real pilot retro lands and gives feedback on cadence.
  **impact:** Until the auto-persist hook lands, restarting the orchestrator without an explicit `save_project_memory` call loses the rolling windows. Documented in deferred_items + in the `out of scope` / «memory writer hook» follow-up bullet.

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
[2026-05-17 wake-8] E8 execution: runtime/lesson_parser.py (LessonProposal dataclass + parse_lesson_markdown line-by-line state machine + parse_lessons_dir glob + proposals_yaml_path slug sanitiser + atomic save_proposals_yaml + load_proposals_yaml round-trip + apply_proposal w/ pydantic validate-before-write + policy_proposal_applied audit emit + apply_proposals_batch collects errors); cli/main.py policy-apply subcommand (typer.confirm prompt OR --auto-apply); 30 new tests
[2026-05-17 wake-8] E8 verification: pytest 968 PASS (938 + 30 = 968 ✓ matches acceptance); ruff clean on lesson_parser.py + cli/main.py + test_e8; mypy --strict clean on lesson_parser.py + cli/main.py
[2026-05-17 wake-8] E8 committed 2240e72 (3 files, +1170 / -0); E8 → Completed, E9 → Current; loop_wrapper runtime + Auto merge=false → no main merge, no ScheduleWakeup, wrapper drives next iteration to E9 (FINAL)
[2026-05-17 wake-9] E9 promoted Pending → Current; workflow=direct (e2e test file + docs file — pattern locked since wake-2)
[2026-05-17 wake-9] E9 execution: tests/test_e9_integration_e2e.py (25 tests in 7 groups: 3 full-pipeline e2e + 3 embedded-skills→worker + 4 memory loop + 5 lessons→policy + 3 CLI policy-apply + 3 docs presence + 4 grep DoD); docs/embedded-skills-architecture.md (~190 lines: design rationale, component layout, upgrade flow, worker spawn overlay path, 4-layer self-learning [L1-L4], manual smoke instructions, operational safety, tests pointer, out-of-scope)
[2026-05-17 wake-9] E9 fix: compliance gate semantics = mandatory-fix on tag MATCH (not absence) — removed matching tags from approve-flow tests; one matching tag retained only in dedicated violation test
[2026-05-17 wake-9] E9 verification: pytest 993 PASS (968 + 25 = 993 ✓ matches acceptance); ruff clean on touched files (pre-existing I001 in test_w1_real_pilot.py left untouched per «touched files» scope); mypy --strict clean on test_e9_integration_e2e.py
[2026-05-17 wake-9] E9 committed 3b700b2 (2 files, +1190 / -0); E9 → Completed; Pending empty; initiative complete on integration/embed_phase45_with_selflearning
[2026-05-17 wake-9] FINAL: loop_wrapper + Auto merge=false → manual_merge_pending blocker written (PENDING resolution stops wrapper); Final Report populated immediately per skill protocol (no autonomous main merge); user reviews integration branch then merges manually
[2026-05-17 wake-9] S9 done, runtime=loop_wrapper — wrapper detects Final Report populated + Blockers PENDING → exits cleanly; initiative complete

## Final Report

**Initiative:** embed_phase45_with_selflearning (step 2 of 7 master BMad builder roadmap)
**Status:** COMPLETE on integration branch — awaiting manual merge to main.

### Acceptance — initiative level (all met)

- ✅ Orchestrator имеет canonical phase 4+5 skills (14 skills embedded under `skills/upstream/`)
- ✅ Любой target project получает same canonical version при worker spawn (overlay via `apply_embedded_skills` in `runtime/embedded_skills.py`)
- ✅ `bmad-orchestrator skill-update <source>` чинит upstream drift без потери customize/policy/lessons (E4)
- ✅ 4 gates встроены в code-review с auto-tuning thresholds (E5 gates + E6 live tuning)
- ✅ Per-project memory тюнит behavior per project (E7 `_config/projects/<slug>/memory.yaml`)
- ✅ Retrospective lessons → policy proposals → user-approved apply loop (E8)
- ✅ 993 PASS, ruff/mypy clean on touched files
- ✅ Docs published (`docs/embedded-skills-architecture.md`)

### Commits on integration/embed_phase45_with_selflearning

| Session | Commit  | Description                                                     | Files | +/-           | Tests |
|---------|---------|-----------------------------------------------------------------|-------|---------------|-------|
| E1      | 83f82ed | Embed 14 phase 4+5 BMad skills + skills/ scaffolding            | 63    | +9792 / -24   | 798   |
| E2      | acd571c | customize/policy/lessons/patches scaffolds + skills_repo        | 21    | +625 / -0     | 813   |
| E3      | e48be06 | worker_spawn copies embedded skills to worktree                 | 4     | +696 / -0     | 833   |
| E4      | ad2360a | skill-update + skill-status subcommands + skill_update pipeline | 4     | +1083 / -2    | 853   |
| E5      | d11a4cb | 4 code-review gates + quarterly compliance sweep                | 4     | +1006 / -10   | 888   |
| E6      | 104f845 | L2 live tuning of code-review thresholds + atomic YAML write    | 4     | +1086 / -0    | 913   |
| E7      | 523a774 | L3 per-project memory primes BudgetGuard rolling windows        | 4     | +756 / -0     | 938   |
| E8      | 2240e72 | L4 lessons → policy proposals parser + policy-apply CLI         | 3     | +1170 / -0    | 968   |
| E9      | 3b700b2 | Integration + e2e + docs (FINAL)                                | 2     | +1190 / -0    | 993   |

Plus 9 tracker commits + 1 bootstrap commit = **19 commits total**, **+17708 / -10 across 106 files**.

### Test count progression

798 (E1 baseline preserved) → 813 (+15 E2) → 833 (+20 E3) → 853 (+20 E4) → 888 (+35 E5) → 913 (+25 E6) → 938 (+25 E7) → 968 (+30 E8) → 993 (+25 E9) = **993 PASS** (195 new tests across E2-E9; E1 added no tests per spec).

### Manual merge — user action required

```bash
git checkout main
git merge --no-ff integration/embed_phase45_with_selflearning \
  -m "merge embed_phase45_with_selflearning E1..E9 — phase 4+5 skills embedded + L1-L4 self-learning"
```

Review integration branch first:

```bash
git log main..integration/embed_phase45_with_selflearning --oneline
git diff main..integration/embed_phase45_with_selflearning --stat
```

Rollback if needed (last-resort, uses backup branch):

```bash
git checkout main
git reset --hard backup/embed_phase45_with_selflearning-pre-2026-05-17
```

### Master roadmap progress

Step 2 of 7 complete. Next steps (separate initiatives):

- Step 3-5: Phase 1-3 skills embedding (analysis / planning / solutioning)
- Step 6: L5 reflexion (auto-PR на own skills) — deferred until baseline data accumulates
- Step 7: Multi-project queue + skill sync to project's `.claude/skills/` (deploy mode)

### Carry-forward TODOs for follow-up initiative

- Memory writer hook (BudgetGuard → memory.yaml on WORKER_COMPLETED) — E7+E9 deferral
- Lessons writer (retrospective output → `skills/lessons/<project>/wave-*.md`) — E8+E9 deferral
- Rollback CLI (`bmad-orchestrator policy-rollback <audit-id>`) — E8 deferral
- Diff display in interactive `policy-apply` prompt for list-valued fields — E8 deferral
- Skills-as-data deeper integration + multi-version upstream support — bootstrap deferrals
- Pre-existing ruff I001 in test_w1_real_pilot.py:290 — one-line cleanup pass
