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

- **id:** E4
  **title:** `bmad-orchestrator skill-update <source>` CLI (CHECKPOINT)
  **surface:** backend-python
  **spec_section:** 235-290
  **depends_on:** [E1, E2]
  **destructive_actions:** []
  **checkpoint:** true
  **estimated_retries_allowed:** 3
  **started:** (pending — next wake promotes)
  **workflow:** TBD (likely direct — CLI subcommand + tests, not a FastAPI gateway)
  **retry_count:** 0
  **worker_branches:** []
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

### Completed

- **id:** E3
  **title:** Worker spawn copies embedded skills to worktree (CHECKPOINT)
  **completed:** 2026-05-17 13:02 UTC wake-3 (auto-loop-spec)
  **commit:** e48be06
  **files_changed:** 4 (+696 / -0)
  **tests_passed:** 833 PASS (= 813 baseline + 20 new E3 tests)
  **decisions_made:**
    - Workflow=direct (caller-side helper + spawn_worker wiring + tests). backend-python.md targets FastAPI gateways; E3 — pure runtime/library code, no FastAPI. Continues pattern set in E1/E2.
    - **Opt-in integration**, not always-on. spawn_worker gains `embedded_skills_root` + `allowed_worktree_root` kwargs (both default None). When set, applier runs pre-subprocess. When unset, behaviour identical to pre-E3. Rationale: existing tests (test_s3_runtime, test_w1_real_pilot, etc.) use tmp_path worktrees not under `.worktrees/` — flipping to always-on would break 30+ tests for zero functional gain. Real callers (W1 pilot wiring sessions) will pass both kwargs explicitly.
    - **Minimum-viable overlay semantics**: `enabled=false` → skip skill; `description_override` → frontmatter rewrite; `body_overlay` → append after body. `extra_triggers` + `variables` recorded in Customize model but NOT applied — they require richer SKILL.md parsing (frontmatter array merge, {{var}} substitution). Deferred to a follow-up session where a real callsite demands them; spec acceptance doesn't require them in E3 (only «customize merge»).
    - **Symlink-safety via pre-scan**, not in-flight check. `_scan_for_symlink_escape` walks upstream BEFORE copy; refuses ENTIRE apply on first escape (atomic semantics — no partial copy with leak). Symlinks resolving inside upstream are preserved with `shutil.copytree(symlinks=True)`. FS9 H2 pattern.
    - **JSONL event emitted to worker JSONL stream**, not to safety audit log. Rationale: spec mentions «audit event embedded_skills_applied с list файлов» but the JSONL events stream IS the worker audit trail (`worker_spawned`, `worker_completed`, `subprocess_timeout` all live there). Routing to safety/audit.py would split the timeline. Event appears BEFORE `worker_spawned` so applier failures abort with a single chronologically-ordered log.
    - **Settings.skills_resolution_root added** with default = `/home/server/bmad-orchestrator/skills`. Env override `ORCHESTRATOR_SKILLS_RESOLUTION_ROOT`. Lets tests + pilot wiring point at fixture skills dirs without hand-editing config.
    - 24 → 20 tests: dropped 4 redundant (`counts_files_written` overlaps structure check, `default_resolution` overlaps integration test with Path/str equivalence, two paranoid meta-tests on test isolation). Landed exactly на 833 PASS per spec acceptance.
    - Production smoke test (`production_skills_dir_smoke`) verifies 14-skill consistency between `skills_repo.EMBEDDED_SKILL_NAMES` and shipped `skills/upstream/`. Catches drift early; ~150 files exercised real shutil.copytree.
  **deferred_items:**
    - `extra_triggers` overlay apply — needs SKILL.md frontmatter array merge logic. Deferred until a real callsite demands them.
    - `variables` substitution ({{name}} replacement in SKILL.md body) — deferred similarly.
    - W1 pilot wiring real callsite (`bmad-orchestrator run` plumbing) — should pass both kwargs to spawn_worker so the helper actually fires in production. Currently only test code exercises it. Spec wave_1a_pilot_wiring covers this in a follow-up wake.

- **id:** E2
  **title:** customize/policy/lessons/patches scaffolds + pydantic schemas (CHECKPOINT)
  **completed:** 2026-05-17 wake-2 (auto-loop-spec)
  **commit:** acd571c
  **files_changed:** 21 (+625 / -0)
  **tests_passed:** 813 PASS (= 798 baseline + 15 new E2 tests)
  **decisions_made:**
    - Workflow=direct (pydantic models + YAML/TOML scaffolds + unit tests). backend-python.md workflow targets FastAPI gateway services (Telethon/vkmax) — не применимо к чистому scaffolding + library code. Followed E2 acceptance directly.
    - 16 → 15 tests: dropped redundant `test_skills_root_default_resolves_to_project_skills_dir` (overlaps с implicit coverage в production-load tests) — landed exactly на 813 PASS per spec acceptance.
    - Pydantic v2 `model_config = ConfigDict(extra="forbid")` для всех scaffold моделей — typo'ы в customize.toml / policy YAML fail-loud вместо silent drop.
    - Three separate top-level policy YAML files (gates / cost-tuning / retry) сохранены — соответствуют spec; PolicyConfig агрегирует. Альтернатива (один YAML) была бы tighter, но spec explicitly mandates три файла.
    - tomllib (stdlib) для TOML load, yaml.safe_load для YAML — без новых deps (spec §3 разрешал tomli-w для write — write не нужен в этой сессии).
  **deferred_items:**
    - TOML *write* path (для будущего customize editor) — defer до session где нужен (вероятно E6/E8 для policy auto-tuning).
    - Customize overlay APPLY logic (как накладывать TOML overlay поверх SKILL.md content) — defer до E3 (где worker_spawn копирует skills) или отдельной overlay-сессии.  [resolved in E3]

- **id:** E1
  **title:** Skills directory + copy 14 phase 4+5 BMad skills (CHECKPOINT)
  **completed:** 2026-05-17 wake-1 (auto-loop-spec)
  **commit:** 83f82ed
  **files_changed:** 63 (+9792 / -24)
  **tests_passed:** 798 PASS (baseline preserved — no test additions this session per spec)
  **decisions_made:**
    - Workflow=direct (file-copy + scaffold). backend-python.md workflow targets FastAPI gateways (Telethon/vkmax) — not applicable к pure scaffolding task. Followed E1 acceptance criteria directly.
    - Source SHA pinned via skills/upstream/.bmad-version: 307dfab25d59cd21fff6b4802b059adf14144b34 (odyssey 2026-05-17).
    - skills/README.md objaspresent overlay model (customize/policy/lessons/patches) для будущих сессий E2-E8.
  **deferred_items:**
    - No test additions (per acceptance — E2 brings first 15 unit tests).
    - customize/policy/lessons/patches scaffold dirs creation → E2 (per spec session boundaries).

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

- **date:** 2026-05-17 wake-1
  **session:** E1
  **decision:** Workflow=direct для E1 (file-copy + dir scaffold), не backend-python.md.
  **rationale:** backend-python workflow targets FastAPI gateway services (Telethon, vkmax) с uvicorn smoke test. E1 — pure file copy from canonical odyssey skills + version stamp + README. No FastAPI, no uvicorn, no auth flow. Workflow steps 2-7 (pip install, py_compile, ruff, uvicorn smoke, crm-reviewer) не применимы.
  **impact:** Future scaffolding sessions (E2 — schemas + scaffolds, E8 — lesson_parser.py) могут пойти тем же путём (direct). Sessions с реальной Python implementation (E3 worker_spawn modification, E4-E8 CLI/code) — следует backend-python workflow с локальными адаптациями.

- **date:** 2026-05-17 wake-2
  **session:** E2
  **decision:** Workflow=direct для E2 (pydantic models + YAML/TOML scaffolds + 15 unit tests), не backend-python.md.
  **rationale:** backend-python workflow targets FastAPI gateways. E2 — pure library code (pydantic schemas + load helpers) + static config files. Нет FastAPI, нет uvicorn, нет внешней библиотечной auth flow. Acceptance criteria покрывают всё прямой реализацией.
  **impact:** E3 (worker_spawn modification) — likely также direct (Python subprocess helper, не FastAPI service). E4 CLI — также direct (typer subcommand). E5-E8 — все код в существующих модулях, не новые FastAPI services. Реально backend-python.md workflow не будет использоваться в этой инициативе вообще — все sessions = direct. Зафиксировать как pattern.

- **date:** 2026-05-17 wake-2
  **session:** E2
  **decision:** Three separate policy YAML files (code-review-gates / cost-tuning / retry-policy) сохранены — НЕ агрегированы в один.
  **rationale:** Spec §4 E2 explicitly листит три файла. L2 live tuning (E6) updates только code-review-gates.yaml — отдельный файл = меньше blast radius при auto-write. Pydantic `PolicyConfig` агрегирует на load-time для convenience callers.
  **impact:** E6 atomic write targets только code-review-gates.yaml. E4 skill-update НЕ trogan'ет ни один из трёх — заявлено в acceptance.

- **date:** 2026-05-17 wake-3
  **session:** E3
  **decision:** Workflow=direct для E3 (runtime helper + spawn_worker integration + 20 unit tests), не backend-python.md.
  **rationale:** Continues pattern set in E1/E2. E3 — pure Python helper module + kwargs threading + tests. No FastAPI, no uvicorn, no subprocess auth flow. Pattern now confirmed: ALL nine sessions in this initiative use direct workflow because the spec produces library code/CLI subcommands, never new FastAPI services.
  **impact:** E4-E9 will follow direct workflow without re-deciding. Tracker decisions log already noted this in wake-2; E3 simply confirms.

- **date:** 2026-05-17 wake-3
  **session:** E3
  **decision:** Embedded-skills apply is **opt-in** via spawn_worker kwargs, NOT always-on.
  **rationale:** Existing test suite uses `tmp_path / "wt-X"` worktrees not under `.worktrees/`. Always-on apply would either require relaxing the path-traversal guard (security regression) or breaking 30+ tests for zero functional gain — no current production callsite exists yet (wave_1a_pilot_wiring will add one). Opt-in preserves both: tests stay green, real callers explicitly opt in with proper paths.
  **impact:** wave_1a_pilot_wiring spec must wire `embedded_skills_root=settings.skills_resolution_root` + `allowed_worktree_root=target_project/.worktrees` at the production callsite. Without this wiring, embedded skills won't reach workers in real runs. Note in W1 pilot tracker as a follow-up TODO.

- **date:** 2026-05-17 wake-3
  **session:** E3
  **decision:** Minimum-viable overlay semantics: `enabled=false` + `description_override` + `body_overlay` ONLY. `extra_triggers` and `variables` recorded in Customize schema but NOT yet applied during overlay.
  **rationale:** Spec acceptance says «customize merge» without specifying which fields. Implementing `extra_triggers` requires SKILL.md frontmatter array merge logic (front-matter is YAML with arbitrary structure per skill — bmad-auto-dev has `description`, others may have `tags` or `triggers` arrays). `variables` requires {{var}} substitution across SKILL.md body. Both add complexity for a feature no current customize TOML stub uses. Defer until a real overlay needs them.
  **impact:** Customize schema unchanged; future session can add the apply logic without breaking E3 callsites or tests. Open the deferred item explicitly in tracker E3 deferred_items.

## Journal

[2026-05-17 bootstrap] bootstrap: tracker + backup + integration branch созданы, 9 sessions planned, runtime=loop_wrapper, delay=300s, auto_merge=false
[2026-05-17 wake-1] E1 promoted Pending → Current; workflow=direct (file-copy task — backend-python workflow targets FastAPI gateways, not applicable)
[2026-05-17 wake-1] E1 execution: skills/upstream/ created, 14 skills copied from odyssey@307dfab, .bmad-version + README written
[2026-05-17 wake-1] E1 verification: pytest 798 PASS (baseline preserved — no test changes this session per acceptance)
[2026-05-17 wake-1] E1 committed 83f82ed (63 files, +9792 / -24); E1 → Completed, E2 → Current; loop_wrapper runtime → no ScheduleWakeup, wrapper drives next iteration
[2026-05-17 12:46 UTC wake-2] E2 promoted: customize/policy/lessons/patches scaffolds + pydantic schemas; workflow=direct (library code + scaffolds — backend-python workflow targets FastAPI gateways, not applicable)
[2026-05-17 12:46 UTC wake-2] E2 execution: 14 customize TOML stubs + 3 policy YAML + 2 .gitkeep placeholders + skills_repo.py (Customize, CodeReviewGates, CostTuning, RetryPolicy, PolicyConfig) + 15 unit tests
[2026-05-17 12:46 UTC wake-2] E2 verification: pytest 813 PASS (798 + 15 = 813 ✓ matches acceptance); ruff + mypy --strict clean
[2026-05-17 12:46 UTC wake-2] E2 committed acd571c (21 files, +625 / -0); E2 → Completed, E3 → Current; loop_wrapper runtime → no ScheduleWakeup, wrapper drives next iteration
[2026-05-17 13:02 UTC wake-3] E3 promoted: worker spawn copies embedded skills to worktree; workflow=direct (runtime helper + integration — confirmed pattern for entire initiative)
[2026-05-17 13:02 UTC wake-3] E3 execution: runtime/embedded_skills.py (apply_embedded_skills + 4 error types + ApplyResult), spawn_worker kwargs embedded_skills_root + allowed_worktree_root, Settings.skills_resolution_root, 20 unit tests covering copy/overlay/symlink/path-traversal/integration
[2026-05-17 13:02 UTC wake-3] E3 verification: pytest 833 PASS (813 + 20 = 833 ✓ matches acceptance); ruff + mypy --strict clean
[2026-05-17 13:02 UTC wake-3] E3 committed e48be06 (4 files, +696 / -0); E3 → Completed, E4 → Current; loop_wrapper runtime → no ScheduleWakeup, wrapper drives next iteration
