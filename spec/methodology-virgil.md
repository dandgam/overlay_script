# Methodology applied to Virgil

> Применение универсальной методички (`spec/methodology.md`) к проекту **Virgil** (bmad-orchestrator).

---

## 1. Что это за агент

- **Use case:** Автономный оркестратор BMad Phase 4 — читает `_bmad-output/planning-artifacts/` целевого проекта, строит DAG зависимостей stories, спавнит параллельные `claude -p` workers в git worktrees, мержит через quality gate в integration branch
- **Target users:** Solo-оператор (1 человек) для управления крупными BMad-проектами без ручного запуска каждой story
- **Long-term vision:** Master BMad builder — agent делает phases 1-5 end-to-end, embedded skills, self-learning (см. memory `project_vision_master_bmad_builder.md`)
- **Success metrics** (v2 — зафиксированы 2026-05-18 как Phase 3 gate):

  | Метрика | Порог | Где измеряется | Статус baseline |
  |---|---|---|---|
  | `pass_rate` | ≥ 85% | eval suite (5-10 stories) — % stories с verdict==approve без human override | TBD после первого eval run |
  | `escalation_rate` | ≤ 20% | % stories выходящих в HUMAN_QUERY (включая compliance, iteration cap, gate trips) | TBD |
  | `review_iteration_p95` | ≤ 2 | 95-й перцентиль `recent_review_iterations` window (cap=3 в policy) | TBD |
  | `cache_hit_rate` | ≥ 50% | Anthropic prompt cache hit rate из worker logs | TBD |
  | `cost_per_story_median` | TBD | median(recent_story_costs) — baseline после первого eval run, дальше регрессия | TBD |
  | `p0_auto_fix_coverage` | ≥ 80% | `_gate_p0_threshold` — fixed/found per story | Текущий policy default |
  | `test_coverage_ratio` | ≥ 50% | `_gate_test_coverage` — actual/expected test count | Текущий policy default |

  **Acceptance criteria для exit Phase 3:** все пороги достигнуты на eval suite из 5+ stories (или явное обоснование почему ниже — например cost_per_story_median baseline ещё не известен в первом запуске).

---

## 2. Какие паттерны Anthropic используем

| Паттерн | Применение в Virgil | Статус |
|---|---|---|
| **P1 Chaining** | Stages внутри `/bmad-auto-dev`: create-story → gauntlet → dev-story → code-review | ✅ Реализован |
| **P2 Routing** | `RoleModels` (planner=Opus, reviewer=Opus, dev=Sonnet, routine=Sonnet) + `set_model` tool для runtime swap; preset routing (parallelism presets) | ✅ Реализован (`config.py` + `agent/system_prompt.py` + tool) |
| **P3 Parallelization** | DAG planner + worker pool (4-8 параллельных workers в worktrees); sectioning независимых stories | ✅ Merged `6e8a18e` (parallelism_initiatives S1..S11) |
| **P4 Orchestrator-Workers** | **Основной паттерн** — central orchestrator делит эпик на stories, динамически спавнит workers с tool harness | ✅ Реализован (наш core loop) |
| **P5 Evaluator-Optimizer** | bmad-code-review → autofix loop (Pilot 2 прошёл «via autofix loop»). `max_review_iterations` cap в `CodeReviewGates` (default=3) + `_gate_iteration_cap` + `review_iteration` payload field. Runaway-loop guard escalates на HUMAN_QUERY за cap | ✅ Formalised 2026-05-18 |

---

## 3. Augmented LLM stack

- **LLM:** Anthropic Claude (subscription mode через `claude -p` CLI; API key возможен позднее с multi-LLM failover)
- **Tools:** Claude Agent SDK tool harness (Read/Write/Edit/Bash/Grep/Glob/etc.) внутри workers; orchestrator использует Anthropic SDK напрямую
- **Retrieval:** Direct file reads из `<project>/_bmad-output/planning-artifacts/`; нет RAG (не нужен — структура known)
- **Memory:**
  - Memory bank `<worktree>/.claude/memory/` (per-session, gitignored)
  - SQLite `state.db` (orchestrator state, retries, retrospective traces)
  - Anthropic memory tool wiring — deferred (vision step 6)
- **Sandbox:** OS-level через bubblewrap (`runtime/sandbox.py`); `_scan_bash` — defence-in-depth

---

## 4. Gap-analysis (по фазам ADLC)

### Фаза 1 — Plan ✅ DONE

- ✅ Vision сформулирован (`project_vision_master_bmad_builder.md`, 7-step roadmap)
- ✅ Architecture spec (`spec/spec_master_orchestrator.md` + 11 детальных spec'и по инициативам)
- ✅ Use case определён (BMad Phase 4 для любого target BMad-проекта — project-agnostic)
- ✅ Backlog ведётся (memory + progress.md)
- ✅ **Success metrics зафиксированы** (2026-05-18) — таблица 7 порогов в разделе 1, Phase 3 acceptance criteria определены

### Фаза 2 — Build ✅ DONE (2026-05-18)

- ✅ Scaffold (Python 3.11 + Anthropic SDK + Claude Agent SDK + networkx + pydantic + typer)
- ✅ Orchestrator-Workers паттерн (P4)
- ✅ Parallelization паттерн (P3) — parallelism_initiatives merged `6e8a18e`
- ✅ Prompt chaining (P1) через embedded Stages
- ✅ Routing (P2) — `RoleModels` (planner=Opus, reviewer=Opus, dev=Sonnet, routine=Sonnet) + `set_model` tool
- ✅ **P5 Evaluator-Optimizer formalised** (2026-05-18) — `max_review_iterations` cap в `CodeReviewGates` + `_gate_iteration_cap` + `review_iteration` field в CODE_REVIEW_VERDICT payload (11 tests)
- ✅ Pilot 1 success — Story 1.1 end-to-end (commit `9e84da6`)
- ✅ Pilot 2 success — Story 1.2 via autofix loop + 5 патчей Z/AA/BB/CC/DD (commits `5440614 → 585b8be`)
- ✅ **Регрессионные тесты для Z/AA/BB/CC/DD** (2026-05-18) — 10 guard tests в `tests/test_regression_pilot2_patches.py`
- ✅ Embedded skills (vision step 2) — 14 BMad skills в `skills/upstream/` + `skill_update.py` (pin/diff/patches/conflict reports, 470 строк)
- ✅ 12 встроенных sub-agent skills (cost-watchdog, dag-planner, elicitation-router, failure-analyst, intent-router, merge-gate, proactive-improver, reflexion-learner, retrospective-writer, story-splitter, wave-coordinator, worker-dispatcher) — wired в `agent/skills/__init__.py` по event types
- ✅ 8 policy YAML (`skills/policy/`: build-check, code-review-gates, cost-tuning, deletion-safety, diff-size-gate, retry-policy, security-review, stage5-completeness)
- ✅ Tests: **1447 PASS** (1426 base + 10 regression + 11 P5)
- ✅ **Pytest stability hardened** (2026-05-18) — `pytest-timeout` + 120s/test cap в `pyproject.toml` (страховка от future hang)
- ✅ Sandbox isolation (bubblewrap)
- ✅ Cost tracking + budget hard-cap
- ⬜ **Deferred:** 19 Medium + 8 Low S10 findings (review-findings-followup) — backlog cleanup, не блокирует Phase 3

### Фаза 3 — Test & Release ⬜ NOT STARTED

- ⬜ **GAP:** Нет eval suite — benchmark из N stories с известным baseline
- ⬜ **GAP:** Production-mode end-to-end pilot на реальном target BMad-проекте не запущен
- 🟡 Red-team/security: lesson `code_review_pipeline_gaps` зафиксирован (4 gate'а identified, не встроены)
- ⬜ R3 security-auditor minors: canonicalize+allowed-root для `--lessons-dir` / `--skills-root` / `--orchestrator-home`
- ⬜ Acceptance criteria для exit из фазы не определены

### Фаза 4 — Deploy ⬜ NOT STARTED

Зависит от закрытия Test & Release. Deploy = регулярные production runs на любых target BMad-проектах (configurable `--project-root`).

### Фаза 5 — Monitor & Improve ⬜ NOT STARTED

- ⬜ Dashboard (cost / cache hit rate / escalation rate / pass rate per epic)
- 🟡 Retrospective trigger — частично есть (через phase-5 wrapper), не continuous loop
- ⬜ Self-learning (vision step 6) — deferred, нужна Anthropic memory tool + policy auto-update
- ⬜ TTS notifications (backlog)

---

## 5. Priority queue

Сортировано по фазам ADLC — закрываем фазы по порядку. Внутри фазы — по влиянию на закрытие gate'а.

### ✅ Фаза 2 closed (2026-05-18)

- Pytest stability hardened (pytest-timeout + 120s cap)
- 10 регрессионных тестов для патчей Z/AA/BB/CC/DD
- P5 formalised (max_review_iterations cap + gate + payload + 11 tests)
- Success metrics зафиксированы

### Фаза 3 Test & Release 🟡 IN PROGRESS

- ✅ **Step A: Eval harness + 5 synthetic cases** (2026-05-18) — `bmad_orchestrator.eval` module (metrics + runner) + CLI `bmad-orchestrator eval run` + 28 unit-тестов + 5 cases (2 easy + 2 medium + 1 hard) в `evals/cases/`. Mock-mode baseline: 5/5 PASS (100%)
- ⬜ **Step B: Real BMad stories** — добавить 10+ реальных stories из target BMad-проекта (configurable `--project-root`, не привязано к конкретному проекту) в `evals/cases/`, прогнать в `--mode real`, baseline cost/latency
- ⬜ **Auto-elicitation policy engine** (1-2 сессии) — `elicitation-router` skill wired, нужна LLM-driven логика поверх 8 policy YAML
- ⬜ **R3 security minors** (0.3 сессии) — canonicalize+allowed-root для `--lessons-dir` / `--skills-root` / `--orchestrator-home`
- ⬜ **4 security gates** из lesson `code_review_pipeline_gaps`
- ⬜ **Wire worker → review_iteration** (0.5 сессии) — bmad-auto-dev runner.sh должен эмитить `review_iteration` в WORKER_COMPLETED payload (orchestrator принимает поле, worker его пока не пишет)

### Фаза 4 — Deploy (после закрытия Phase 3)

9. **Supervisor LLM-loop + Terminal TUI** (3-4 сессии) — управление halts/elicitations без ручного оператора
   - Превращает агента в standalone — может работать без сидящего рядом человека
   - Зависимость: auto-elicitation engine (#6) должен быть готов
10. **Production pilot на реальном target BMad-проекте** — выбор проекта остаётся на момент запуска (project-agnostic)

### Фаза 5 — Monitor & Improve

11. **Anthropic memory tool wiring** (1 сессия) — vision step 6 (foundation для self-learning)
    - Cross-session knowledge accumulation
    - Зависимость: pilot logs накоплены (нужно хотя бы 1 production run)
12. **Observability dashboard** — cost / cache hit / escalations / pass rate per epic
13. **TTS notifications** (backlog)
14. **Vision steps 3-7** — embedding phase 3/2/1 skills, self-learning loop, multi-project skill sync

### Параллельно/между инициативами

- Backlog cleanup (19 Medium + 8 Low S10 findings) — quick wins
- Manual merge `integration/canonical_patches_port → main` — оргвопрос
- Doc-of-experiment Pilot 2 retrospective (опционально, memory уже captured)

---

## 6. References

- **Universal methodology:** `spec/methodology.md`
- **Architecture spec:** `spec/spec_master_orchestrator.md`
- **Vision:** memory `project_vision_master_bmad_builder.md`
- **Progress tracker:** `.claude/memory/progress.md`
- **Project status:** memory `project_milestone_parallelism_initiatives_complete.md` (post `6e8a18e`)
- **Current pilot artifact:** Story 1.1 commit `9e84da6`
- **Backlog memories:**
  - `project_backlog_orchestrator_project_agnostic.md`
  - `project_backlog_parallelism_presets.md`
  - `project_backlog_post_mvp.md`
  - `project_backlog_auto_split_parallelism.md`
  - `project_backlog_sandbox_cgroup_migration.md`
- **Lessons:**
  - `project_lesson_code_review_pipeline_gaps.md`
  - `project_lesson_canonical_bmad_chain_gaps.md`

---

**Last updated:** 2026-05-18 (v4 — Phase 3 Step A landed)
**Status:** v5 — project-agnostic. Phase 3 Step A ✅ (eval harness + 5 synthetic cases mock-mode 100% PASS, 28 new tests). Tests: 1475 PASS. Next: Step B (real BMad stories из любого target проекта) или Auto-elicitation engine.
**Owner:** user + Claude orchestrator
