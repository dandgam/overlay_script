# Spec — Embed Phase 4+5 BMad Skills + Self-Learning Foundation

**Дата:** 2026-05-17
**Версия:** 0.1
**Базовая ветка:** `main` (post-merge 67786b8 — dag_planner_bmad_compat)
**Backup branch:** `backup/embed_phase45_with_selflearning-pre-2026-05-17`
**Integration branch:** `integration/embed_phase45_with_selflearning`
**Auto merge:** false

---

## 1. Контекст

Текущий orchestrator — тонкая обёртка над project's `bmad-auto-dev` skill (читает skill из проекта при `claude -p`). User vision (`project_vision_master_bmad_builder.md`): **self-contained BMad master agent** с embedded skills и self-learning.

Этот spec реализует **step 2 of 7** master roadmap:
- Embed 14 phase 4+5 BMad skills в orchestrator репо
- Upgrade-механизм (pull upstream без потери наших улучшений)
- 4 code-review gate'a из lesson (P0-count, compliance, test-coverage, sweep)
- Self-learning foundation (L2 live tuning + L3 per-project memory + L4 lessons → policy proposals)

После step 2 — orchestrator работает на ЛЮБОМ BMad-проекте **по нашей канонической версии**, тюнит thresholds **per-project**, проращивает урок'ов в **proposed policy changes**.

## 2. Принципы

- **Skills as data**: pristine upstream копии в `skills/upstream/`, наши tweaks в `customize/`, `policy/`, `patches/`. Upgrade не трогает наши data файлы.
- **Live tuning over fixed thresholds**: каждый story emit'ит metrics; policy YAML обновляется автоматически на основе rolling p95.
- **Per-project memory**: тюнинг per-project, не глобальный.
- **Manual approval для policy proposals**: orchestrator парсит lessons → пишет YAML proposals; user approve'ит перед apply.
- **No project mutation**: skills копируются в worktree's `.claude/skills/`, не в `<project>/.claude/skills/` main checkout.

## 3. Stack / Constraints

- Python 3.11+, без новых deps кроме `tomli-w` (TOML write) если нужен
- Сохранить все 798 PASS из main + добавить ~150-200 тестов
- ruff + mypy --strict зелёные
- Deny-list: `runtime/sandbox.py`, `runtime/worker_spawn.py` (sandbox), `agent/safety/budget_guard.py` — frozen
- Embedded skills копируются **в worktree** перед spawn'ом, project's main checkout не трогается

## 4. Session Plan

9 сессий, surface=`backend-python`, code-only кроме E1 (file copy).

### E1 — Skills directory + copy 14 phase 4+5 BMad skills (CHECKPOINT)

- **surface:** backend-python
- **spec_section:** 80-120
- **depends_on:** []
- **destructive_actions:** []
- **checkpoint:** true
- **acceptance:**
  - Создать `skills/upstream/` директорию в orchestrator repo
  - Скопировать 14 phase 4+5 skills из `/home/server/odyssey/.claude/skills/`:
    - bmad-auto-dev, bmad-dev-story, bmad-agent-dev, bmad-code-review
    - bmad-review-adversarial-general, bmad-review-edge-case-hunter, bmad-correct-course
    - bmad-quick-dev, bmad-checkpoint-preview, bmad-create-story
    - bmad-advanced-elicitation, bmad-retrospective, bmad-customize, bmad-sprint-status
  - Зафиксировать source SHA через `skills/upstream/.bmad-version` (Odyssey's git rev + date)
  - README в `skills/` с explanation структуры
  - `pytest tests/ -q` — 798 PASS (no test additions yet)

### E2 — customize/policy/lessons/patches scaffolds + schemas (CHECKPOINT)

- **surface:** backend-python
- **spec_section:** 125-180
- **depends_on:** [E1]
- **checkpoint:** true
- **acceptance:**
  - `skills/customize/` — empty TOML stubs для каждого embedded skill (`bmad-auto-dev.customize.toml`, etc.)
  - `skills/policy/` — YAML files с initial defaults:
    - `code-review-gates.yaml` — P0 thresholds (auto-fix scope, compliance tags)
    - `cost-tuning.yaml` — story reserve, daily cap defaults
    - `retry-policy.yaml` — max retries per story, escalation triggers
  - `skills/patches/` — пустая dir (placeholder для future code patches)
  - `skills/lessons/` — пустая dir (filled by retrospective output)
  - `src/bmad_orchestrator/skills_repo.py` — load/parse helpers с pydantic models
  - 15 unit tests на parse customize/policy schemas
  - `pytest tests/ -q` — 813 PASS

### E3 — Worker spawn copies embedded skills to worktree (CHECKPOINT)

- **surface:** backend-python
- **spec_section:** 185-230
- **depends_on:** [E1, E2]
- **destructive_actions:** [«copy skills into worktree's .claude/skills/, overwriting any project version»]
- **checkpoint:** true
- **acceptance:**
  - Modify `runtime/worker_spawn.py::spawn_worker` (caller-side helper, NOT inside sandbox-critical path):
    - Before subprocess launch, copy `skills/upstream/` + apply `customize/` overrides → `<worktree>/.claude/skills/`
    - Symlinks-safe copy (FS9 H2 pattern), abort if worktree path не под `_root/.worktrees/`
  - Add `skills_resolution_root` config option (default = orchestrator's `skills/`)
  - Audit event `embedded_skills_applied` с list файлов
  - 20 tests: copy logic, overwriting, symlink safety, customize merge
  - `pytest tests/ -q` — 833 PASS

### E4 — `bmad-orchestrator skill-update <source>` CLI (CHECKPOINT)

- **surface:** backend-python
- **spec_section:** 235-290
- **depends_on:** [E1, E2]
- **checkpoint:** true
- **acceptance:**
  - `cli/main.py` → new `skill-update` subcommand:
    - `--source <path>` (default: read from `skills/upstream/.bmad-version` URL/path)
    - Pulls latest upstream → diff vs current `skills/upstream/`
    - Re-applies `patches/*.diff` — conflicts → exit 1 + report file `skills/upstream-conflicts-<timestamp>.md`
    - `customize/`, `policy/`, `lessons/` НЕ trogan'ятся
    - Dry-run mode default; `--apply` to actually write
  - `bmad-orchestrator skill-status` — shows current upstream version, applied patches, conflicts pending
  - 20 tests + integration test на mock BMad upgrade
  - `pytest tests/ -q` — 853 PASS

### E5 — 4 code-review gates implementation (CHECKPOINT)

- **surface:** backend-python
- **spec_section:** 295-360
- **depends_on:** [E2, E3]
- **checkpoint:** true
- **acceptance:**
  - Extend `code_review_subscriber` (agent/run.py W4) с 4 gates:
    - **P0-count gate**: parse review verdict, count P0 findings, count auto-fix coverage; reject if `fixed < found * threshold` (threshold from policy YAML)
    - **Compliance gate**: scan findings tags `[152-ФЗ]`, `[187-ФЗ]`, any compliance label из policy → mandatory fix, defer запрещён → escalate HUMAN_QUERY
    - **Test-coverage gate**: count test files modified vs spec'ed N_tests; reject if `todo!()` placeholders > threshold
    - **Quarterly sweep**: on `wave_boundary_reached` если `completed_stories % 50 == 0` → emit `COMPLIANCE_SWEEP_NEEDED` event
  - Gates configured через `skills/policy/code-review-gates.yaml`
  - 35 tests
  - `pytest tests/ -q` — 888 PASS

### E6 — L2 Live tuning (adaptive thresholds) (CHECKPOINT)

- **surface:** backend-python
- **spec_section:** 365-410
- **depends_on:** [E5]
- **checkpoint:** true
- **acceptance:**
  - Extend `BudgetGuard._recent_story_costs` pattern до:
    - `_recent_p0_counts` (deque maxlen=10) — для P0-count gate auto-tune
    - `_recent_test_counts` (deque maxlen=10) — для test-coverage gate
    - `_recent_review_iterations` (deque maxlen=10) — fix-cycle depth
  - После каждого `WORKER_COMPLETED` → update deques
  - Threshold auto-adjusts: median + 1.5×IQR rule (robust to outliers)
  - Periodic write to `skills/policy/code-review-gates.yaml` (atomic write)
  - 25 tests: tuning logic, write atomicity, threshold-out-of-range guards
  - `pytest tests/ -q` — 913 PASS

### E7 — L3 Per-project memory (CHECKPOINT)

- **surface:** backend-python
- **spec_section:** 415-460
- **depends_on:** [E6]
- **checkpoint:** true
- **acceptance:**
  - Create `<orchestrator>/_config/projects/<slug>/memory.yaml` per target project on first run
  - Schema: `median_story_cost_usd`, `median_review_p0`, `median_test_count`, `last_wave`, `success_rate`, `compliance_findings_count`, `lessons_files_count`
  - `BudgetGuard` reads memory.yaml on init, primes deques с last 10 known values
  - `--project <slug>` resolves к right memory file
  - 25 tests: memory load/save, fresh project init, schema migration
  - `pytest tests/ -q` — 938 PASS

### E8 — L4 Lessons → policy proposals (CHECKPOINT)

- **surface:** backend-python
- **spec_section:** 465-520
- **depends_on:** [E7]
- **checkpoint:** true
- **acceptance:**
  - New `runtime/lesson_parser.py`:
    - Parse `skills/lessons/<project>/wave-<N>.md` markdown
    - Extract «policy proposals» blocks (specific markdown format: `## Policy proposal: <field>` → before/after values)
    - Generate `_config/projects/<slug>/policy-proposals.yaml`
  - New CLI: `bmad-orchestrator policy-apply <project>` — review proposals + interactive accept/reject (or `--auto-apply` flag)
  - Audit event на каждое applied proposal с before/after для rollback
  - 30 tests: parser edge cases, YAML generation, apply logic
  - `pytest tests/ -q` — 968 PASS

### E9 — Integration tests + smoke + docs (FINAL)

- **surface:** backend-python
- **spec_section:** 525-580
- **depends_on:** [E3, E5, E6, E7, E8]
- **checkpoint:** false
- **acceptance:**
  - End-to-end test: synthetic project → spawn worker → embedded skill applied → code-review с 4 gates → completion → live tuning update → per-project memory update → simulated retrospective → policy proposal
  - Manual smoke test instructions: `bmad-orchestrator run --project <real> --wave 1a --real --max-stories 1 --story <id>` с embedded skills (verify worktree's `.claude/skills/` matches orchestrator's `skills/upstream/`)
  - `docs/embedded-skills-architecture.md` — design rationale, upgrade flow, customize/policy/lessons explanation
  - 25 e2e + integration tests
  - `pytest tests/ -q` — 993 PASS; ruff/mypy clean
  - Manual merge через human review

## 5. Risks & deferred

| Риск | Митигация |
|---|---|
| Skills из Odyssey могут расходиться с upstream BMad maintainers | `skills/upstream/.bmad-version` фиксирует source SHA; upgrade detects drift |
| customize.toml schema может различаться между skills | Per-skill schema definitions в pydantic, fail-loud parse |
| Live tuning может drift'ить thresholds в небезопасную сторону | `policy/bounds.yaml` с min/max guards; threshold movements > 50% require human approval |
| L5 reflexion (auto-PR на skills) НЕ В SCOPE | deferred к step 6 master roadmap |

## 6. Acceptance — initiative level

После E9 merge'а:
- ✅ Orchestrator имеет canonical phase 4+5 skills (14 skills embedded)
- ✅ Любой target project получает same canonical version при worker spawn
- ✅ `bmad-orchestrator skill-update <source>` чинит upstream drift без потери customize/policy/lessons
- ✅ 4 gates встроены в code-review с auto-tuning thresholds
- ✅ Per-project memory тюнит behavior per project (Odyssey ≠ CRM)
- ✅ Retrospective lessons → policy proposals → user-approved apply loop
- ✅ 993 PASS, ruff/mypy clean
- ✅ Docs published

**Step 2 of 7 master roadmap complete.** Phase 1-3 embedding — отдельные инициативы.

---

**End of spec v0.1.**
