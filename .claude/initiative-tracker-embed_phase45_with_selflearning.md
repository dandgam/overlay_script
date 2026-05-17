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
(empty — E9 promoted to Current)

### Current

- **id:** E9
  **title:** Integration + e2e smoke + docs (FINAL)
  **surface:** backend-python
  **spec_section:** 525-580 (spec is shorter — see lines 176-188)
  **depends_on:** [E3, E5, E6, E7, E8]
  **destructive_actions:** []
  **checkpoint:** false
  **estimated_retries_allowed:** 3
  **started:** (pending — next wake promotes)
  **workflow:** direct (e2e harness + docs — pattern locked since wake-2)
  **retry_count:** 0
  **worker_branches:** []
  **acceptance:**
    - End-to-end test: synthetic project → spawn worker с embedded skill → code-review с 4 gates → completion → live tuning update → per-project memory update → simulated retrospective → policy proposal
    - Manual smoke instructions: bmad-orchestrator run --project <real> --wave 1a --real --max-stories 1 --story <id>
    - docs/embedded-skills-architecture.md — design rationale, upgrade flow, customize/policy/lessons explanation
    - 25 e2e + integration tests
    - `pytest tests/ -q` — 993 PASS; ruff/mypy clean
    - Manual merge через human review (Auto merge=false)

### Completed

- **id:** E8
  **title:** L4 Lessons → policy proposals (CHECKPOINT)
  **completed:** 2026-05-17 wake-8 (auto-loop-spec)
  **commit:** 2240e72
  **files_changed:** 3 (+1170 / -0)
  **tests_passed:** 968 PASS (= 938 baseline + 30 new E8 tests)
  **decisions_made:**
    - Workflow=direct (new runtime module + CLI subcommand + tests). Pattern locked since wake-2: backend-python.md targets FastAPI gateways, none of E1-E9 produce one. E8 = pure parser + atomic YAML writer + CLI subcommand.
    - **Markdown contract = strict `## Policy proposal: <file>.<field>` header + `before:` + `after:` + optional `rationale:`.** Spec literally says «specific markdown format» without pinning syntax — chose the most operator-readable shape so lessons can be hand-edited or LLM-generated. `<file>` is the policy YAML stem (one of `code-review-gates`, `cost-tuning`, `retry-policy`); `<field>` is a top-level key on the matching pydantic model. Unknown file OR field → fail-loud `LessonProposalInvalidError` rather than silently dropping — operator sees the typo immediately.
    - **`before` / `after` parsed via `yaml.safe_load`.** Allows scalars (0.7, 5, "string"), JSON-flavoured lists (`[a, b, c]`), and bool/null without a custom mini-parser. Round-trips back into the YAML schema check cleanly.
    - **Parser raises on missing `before:` / `after:` lines.** A block opened by `## Policy proposal: ...` MUST have both — partial blocks indicate a hand-edit bug, not a soft warning. Test `test_e8_parse_malformed_missing_before_raises` and the matching after-variant pin this contract.
    - **Narrative-text guard via line-by-line state machine.** A block scans forward only between `## Policy proposal:` headers — any narrative line lacking `:` is skipped, but a line shaped like `before:foo` outside a block is also ignored because the parser only reads `key:` lines after entering a block. Test `test_e8_parse_ignores_narrative_text` documents the boundary.
    - **`PolicyApplyError` raised by `apply_proposal` when pydantic validation fails — disk untouched.** The flow is load → mutate dict → `model_validate` → `model_dump` → atomic write. Validation precedes any write, so an out-of-range `after` value (e.g. `p0_threshold=2.0`) cannot half-corrupt the YAML. Test `test_e8_apply_proposal_schema_violation_raises_without_disk_write` reads the file content before+after and asserts byte-equality.
    - **Sibling fields preserved on partial update.** `model_validate(current_dict | {field: after})` then `model_dump` round-trips the OTHER fields through pydantic, keeping their on-disk values. Tested explicitly per-target (`code-review-gates`, `cost-tuning`, `retry-policy`) so a future schema bump that adds a field doesn't accidentally drop it.
    - **Atomic write = same tempfile + fsync + os.replace pattern as `runtime.live_tuning.atomic_write_gates_yaml` and `runtime.project_memory.save_project_memory`.** Same parent dir for the tempfile (so `os.replace` is a same-filesystem rename = atomic on POSIX). On exception the tempfile is unlinked. Test `test_e8_save_proposals_yaml_atomic_tempfile_cleanup_on_raise` pins both invariants.
    - **`policy_proposal_applied` audit event includes full before/after + rationale + source_file.** That's the rollback record — if an operator runs `--auto-apply` then realises a proposal was wrong, the audit log contains the exact prior value to set the YAML back to. Test `test_e8_apply_emits_policy_proposal_applied_audit_with_before_after` reads the JSONL and asserts every field is present.
    - **Batch errors are collected, not raised.** `apply_proposals_batch` continues past a single failing proposal so a bad apple doesn't abort the batch. Failed proposals land in `ApplyResult.errors`; the CLI reports them and exits non-zero, but valid proposals before/after still applied. Test `test_e8_apply_proposals_batch_schema_error_lands_in_errors` pins this.
    - **Non-auto mode requires a prompt callable — `apply_proposals_batch(..., prompt=None, auto_apply=False)` raises `ValueError`.** Defensive — prevents silently approving everything when the caller forgot to wire the prompt. CLI passes `typer.confirm`; tests pass `lambda _: True/False`.
    - **CLI `policy-apply <project> [--auto-apply] [--lessons-dir] [--skills-root] [--orchestrator-home]`.** All four flags overridable for test isolation; defaults resolve via `load_settings()`. Saves proposals YAML BEFORE applying so even if apply fails the operator has the review artefact. Exit code 1 when any proposal errors; 0 otherwise.
    - **CLI test uses `typer.testing.CliRunner` + monkeypatched `ORCHESTRATOR_ORCHESTRATOR_HOME` / `ORCHESTRATOR_TARGET_PROJECT` / `BMAD_AUDIT_LOG`.** Same pattern as the s4/s6/s8 test suites — keeps CLI test hermetic without touching the real `_config/` or audit log.
    - **30 → 30 tests exactly** matched spec acceptance. Watched for parametrize inflation (E7 lesson learned); used a single inline test for the slug-rejection family.
  **deferred_items:**
    - **Lessons writer not yet implemented.** E8 only reads `skills/lessons/<project>/wave-*.md` — generating those files from retrospective runs is a separate concern (likely E9 e2e simulates one, future BMad pilot retro produces real ones). Without a writer, `policy-apply` will report «no proposals found» for projects without a hand-written lesson file.
    - **Rollback CLI not yet implemented.** Audit event carries before/after but there's no `bmad-orchestrator policy-rollback <audit-id>` companion command — operator must hand-edit policy YAML using the audit log as reference. Reasonable for V1; revisit if rollback frequency justifies tooling.
    - **No diff display in interactive mode.** Prompt shows before/after as raw repr — for list-valued fields a `difflib`-style diff would be more readable. Deferred until operator feedback says it matters.
    - **`policy-proposals.yaml` is regenerated from scratch on every `policy-apply` run.** No merge with prior proposals — if the operator manually edited the file between runs, those edits are overwritten. Acceptable because the source of truth is the markdown; revisit if the file becomes operator-editable in its own right.
    - **Aggregate fields in memory.yaml (median_story_cost_usd, etc.) still not populated.** E7 deferred this to E9; E8 doesn't touch the aggregates either. E9 e2e wiring is the natural place.

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

- **date:** 2026-05-17 wake-1 through wake-8
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

## Final Report (populated on last session completion)

(empty — E9 still ahead)
