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

- **id:** E2
  **title:** customize/policy/lessons/patches scaffolds + pydantic schemas (CHECKPOINT)
  **surface:** backend-python
  **spec_section:** 125-180
  **depends_on:** [E1]
  **destructive_actions:** []
  **checkpoint:** true
  **estimated_retries_allowed:** 3
  **acceptance:**
    - `skills/customize/<skill>.customize.toml` — empty TOML stubs для каждого embedded skill (14 файлов)
    - `skills/policy/code-review-gates.yaml`, `cost-tuning.yaml`, `retry-policy.yaml` — initial defaults
    - `skills/patches/` — empty dir (placeholder)
    - `skills/lessons/` — empty dir
    - `src/bmad_orchestrator/skills_repo.py` — load/parse с pydantic models (Customize, PolicyConfig)
    - 15 unit tests на parse customize/policy schemas
    - `pytest tests/ -q` — 813 PASS

- **id:** E3
  **title:** Worker spawn copies embedded skills to worktree (CHECKPOINT)
  **surface:** backend-python
  **spec_section:** 185-230
  **depends_on:** [E1, E2]
  **destructive_actions:**
    - Copy skills/upstream/ + customize/ overrides в worktree's .claude/skills/ (overwrites if exists)
  **checkpoint:** true
  **estimated_retries_allowed:** 3
  **acceptance:**
    - Modify `runtime/worker_spawn.py::spawn_worker` (caller-side helper, NOT inside sandbox-critical path):
      - Before subprocess launch, copy skills/upstream/ + customize/ overrides → <worktree>/.claude/skills/
      - Symlinks-safe (FS9 H2), abort if worktree path не под `_root/.worktrees/`
    - `skills_resolution_root` config option (default = orchestrator's skills/)
    - Audit event `embedded_skills_applied` с list файлов
    - 20 tests: copy logic, overwriting, symlink safety, customize merge
    - `pytest tests/ -q` — 833 PASS

- **id:** E4
  **title:** `bmad-orchestrator skill-update <source>` CLI (CHECKPOINT)
  **surface:** backend-python
  **spec_section:** 235-290
  **depends_on:** [E1, E2]
  **destructive_actions:** []
  **checkpoint:** true
  **estimated_retries_allowed:** 3
  **acceptance:**
    - `cli/main.py` → new `skill-update` subcommand:
      - `--source <path>` (default: read from skills/upstream/.bmad-version)
      - Pulls latest upstream → diff vs current skills/upstream/
      - Re-applies patches/*.diff — conflicts → exit 1 + report `skills/upstream-conflicts-<ts>.md`
      - customize/, policy/, lessons/ НЕ trogan'ятся
      - Dry-run mode default; `--apply` to actually write
    - `bmad-orchestrator skill-status` — shows current version + applied patches + pending conflicts
    - 20 tests + integration test на mock BMad upgrade
    - `pytest tests/ -q` — 853 PASS

- **id:** E5
  **title:** 4 code-review gates implementation (CHECKPOINT)
  **surface:** backend-python
  **spec_section:** 295-360
  **depends_on:** [E2, E3]
  **destructive_actions:** []
  **checkpoint:** true
  **estimated_retries_allowed:** 3
  **acceptance:**
    - Extend code_review_subscriber (agent/run.py W4):
      - **P0-count gate**: count review P0 vs fixed P0; reject if `fixed < found * threshold` (threshold from policy/code-review-gates.yaml)
      - **Compliance gate**: scan tags [152-ФЗ], [187-ФЗ], any in policy → mandatory fix, defer запрещён → HUMAN_QUERY escalation
      - **Test-coverage gate**: count test files vs spec'ed N_tests; reject if `todo!()` placeholders > threshold
      - **Quarterly sweep**: on wave_boundary_reached если completed_stories % 50 == 0 → emit COMPLIANCE_SWEEP_NEEDED
    - 35 tests
    - `pytest tests/ -q` — 888 PASS

- **id:** E6
  **title:** L2 Live tuning — adaptive thresholds (CHECKPOINT)
  **surface:** backend-python
  **spec_section:** 365-410
  **depends_on:** [E5]
  **destructive_actions:** []
  **checkpoint:** true
  **estimated_retries_allowed:** 3
  **acceptance:**
    - Extend BudgetGuard pattern:
      - _recent_p0_counts (deque maxlen=10)
      - _recent_test_counts (deque maxlen=10)
      - _recent_review_iterations (deque maxlen=10)
    - WORKER_COMPLETED → update deques
    - Threshold auto-adjusts: median + 1.5×IQR (robust to outliers)
    - Atomic write to skills/policy/code-review-gates.yaml
    - Bounds guard: threshold movements > 50% require human approval (escalate)
    - 25 tests
    - `pytest tests/ -q` — 913 PASS

- **id:** E7
  **title:** L3 Per-project memory (CHECKPOINT)
  **surface:** backend-python
  **spec_section:** 415-460
  **depends_on:** [E6]
  **destructive_actions:** []
  **checkpoint:** true
  **estimated_retries_allowed:** 3
  **acceptance:**
    - Create _config/projects/<slug>/memory.yaml per target project on first run
    - Schema: median_story_cost_usd, median_review_p0, median_test_count, last_wave, success_rate, compliance_findings_count, lessons_files_count
    - BudgetGuard reads memory.yaml on init, primes deques с last 10 known values
    - `--project <slug>` resolves к right memory file
    - 25 tests: load/save, fresh project init, schema migration
    - `pytest tests/ -q` — 938 PASS

- **id:** E8
  **title:** L4 Lessons → policy proposals (CHECKPOINT)
  **surface:** backend-python
  **spec_section:** 465-520
  **depends_on:** [E7]
  **destructive_actions:** []
  **checkpoint:** true
  **estimated_retries_allowed:** 3
  **acceptance:**
    - `runtime/lesson_parser.py`:
      - Parse skills/lessons/<project>/wave-<N>.md markdown
      - Extract «policy proposals» blocks (specific markdown format)
      - Generate _config/projects/<slug>/policy-proposals.yaml
    - New CLI: `bmad-orchestrator policy-apply <project>` — review proposals + interactive accept/reject (or --auto-apply flag)
    - Audit event per applied proposal с before/after для rollback
    - 30 tests: parser edge cases, YAML generation, apply logic
    - `pytest tests/ -q` — 968 PASS

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

- **id:** E1
  **title:** Skills directory + copy 14 phase 4+5 BMad skills (CHECKPOINT)
  **surface:** backend-python
  **spec_section:** 80-120
  **depends_on:** []
  **destructive_actions:** []
  **checkpoint:** true
  **estimated_retries_allowed:** 3
  **started:** 2026-05-17 (auto-loop wake)
  **workflow:** direct (file-copy + scaffold, not FastAPI — backend-python workflow.md not applicable to pure scaffolding)
  **retry_count:** 0
  **worker_branches:** []
  **acceptance:**
    - Create `skills/upstream/` directory в orchestrator repo
    - Copy 14 phase 4+5 skills из `/home/server/odyssey/.claude/skills/` (canonical source):
      bmad-auto-dev, bmad-dev-story, bmad-agent-dev, bmad-code-review,
      bmad-review-adversarial-general, bmad-review-edge-case-hunter, bmad-correct-course,
      bmad-quick-dev, bmad-checkpoint-preview, bmad-create-story,
      bmad-advanced-elicitation, bmad-retrospective, bmad-customize, bmad-sprint-status
    - `skills/upstream/.bmad-version` с Odyssey's git rev + date + source path
    - `skills/README.md` объясняет структуру (upstream/customize/policy/lessons/patches)
    - `pytest tests/ -q` — 798 PASS (no test changes yet)
  **safety_gates:**
    - L1: no force/no-verify; copy operations верны, чистые
    - L2: deny-list freeze (sandbox/worker_spawn/budget_guard)
    - L3: branch isolation

### Completed
(none)

## Safety Gates Triggered
(none yet)

## Blockers / Pauses
(none yet)

## Decisions Log

- **date:** 2026-05-17 (bootstrap)
  **session:** bootstrap
  **decision:** Initiative embed_phase45_with_selflearning — 9 sessions E1-E9, surface=backend-python, code-only. Embed 14 phase 4+5 BMad skills + upgrade mechanism + 4 code-review gates + self-learning L2/L3/L4 foundation (L5 reflexion deferred).
  **rationale:** User vision сформулирован 2026-05-17 — master BMad builder с embedded skills, self-learning, project-agnostic. Текущее = step 1 (wrapper). Этот spec = step 2 of 7 master roadmap. После — orchestrator работает на любом BMad-проекте по canonical version с per-project tuning через retrospective lessons.
  **impact:** После E9 — full phase 4+5 self-contained operation. Phase 1-3 embed — отдельные инициативы (step 3-5).

- **date:** 2026-05-17 (bootstrap)
  **session:** bootstrap
  **decision:** L5 reflexion (auto-PR на свои skills) **deferred к step 6** master roadmap.
  **rationale:** Security risk высокий (agent редактирующий свои skills + commits в integration без human review). Lack of baseline data для understanding patterns — нужны 2-3 waves successful pilots first.
  **impact:** Self-learning ограничен L2 (live tuning) + L3 (per-project memory) + L4 (lessons → proposals с user approval). Это даёт 80% UX без 100% risk.

## Journal

[2026-05-17 bootstrap] bootstrap: tracker + backup + integration branch созданы, 9 sessions planned, runtime=loop_wrapper, delay=300s, auto_merge=false
[2026-05-17 wake-1] E1 promoted Pending → Current; workflow=direct (file-copy task — backend-python workflow targets FastAPI gateways, not applicable)
