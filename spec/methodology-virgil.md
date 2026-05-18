# Methodology applied to Virgil

> Применение универсальной методички (`spec/methodology.md`) к проекту **Virgil** (bmad-orchestrator).

---

## 1. Что это за агент

- **Use case:** Автономный оркестратор BMad Phase 4 — читает `_bmad-output/planning-artifacts/` целевого проекта, строит DAG зависимостей stories, спавнит параллельные `claude -p` workers в git worktrees, мержит через quality gate в integration branch
- **Target users:** Solo-оператор (1 человек) для управления крупными BMad-проектами без ручного запуска каждой story
- **Long-term vision:** Master BMad builder — agent делает phases 1-5 end-to-end, embedded skills, self-learning (см. memory `project_vision_master_bmad_builder.md`)
- **Success metrics** (TBD — формальных пока нет, надо определить):
  - Pass rate ≥ 85% на 20 random stories из Odyssey backlog
  - Cost ≤ $X per story (TBD baseline после pilot)
  - Escalation rate ≤ 20% (4 из 5 решений автоматом)
  - Cache hit rate ≥ 50% (требование Anthropic SDK best practice)

---

## 2. Какие паттерны Anthropic используем

| Паттерн | Применение в Virgil | Статус |
|---|---|---|
| **P1 Chaining** | Stages внутри `/bmad-auto-dev`: create-story → gauntlet → dev-story → code-review | ✅ Реализован |
| **P2 Routing** | Model selection (Opus для architecture, Sonnet для impl, Haiku для mechanical); preset routing (parallelism presets) | 🟡 Частично — presets есть, model routing в backlog |
| **P3 Parallelization** | DAG planner + worker pool (4-8 параллельных workers в worktrees); sectioning независимых stories | ✅ Merged `6e8a18e` (parallelism_initiatives S1..S11) |
| **P4 Orchestrator-Workers** | **Основной паттерн** — central orchestrator делит эпик на stories, динамически спавнит workers с tool harness | ✅ Реализован (наш core loop) |
| **P5 Evaluator-Optimizer** | bmad-code-review должен превратиться в loop (review → auto-fix → re-review) | ⬜ **Не реализован** — главный архитектурный gap |

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

### Фаза 1 — Plan ✅ DONE (с оговоркой)

- ✅ Vision сформулирован (`project_vision_master_bmad_builder.md`, 7-step roadmap)
- ✅ Architecture spec (`spec/spec_master_orchestrator.md` + 11 детальных spec'и по инициативам)
- ✅ Use case определён (BMad Phase 4 для Odyssey, далее любой BMad-проект)
- ✅ Backlog ведётся (memory + progress.md)
- ⬜ **GAP:** формальные success metrics не зафиксированы (см. раздел 1 — TBD)

### Фаза 2 — Build 🟡 IN PROGRESS

- ✅ Scaffold (Python 3.11 + Anthropic SDK + Claude Agent SDK + networkx + pydantic + typer)
- ✅ Orchestrator-Workers паттерн (P4)
- ✅ Parallelization паттерн (P3) — parallelism_initiatives merged `6e8a18e`
- ✅ Prompt chaining (P1) через embedded Stages
- ✅ First pilot success — Antares Story 1.1 (commit `9e84da6`, проверка что core loop работает)
- ✅ Tests: 1426 PASS (post S11)
- ✅ Sandbox isolation (bubblewrap)
- ✅ Cost tracking + budget hard-cap
- 🟡 Routing паттерн (P2) — preset routing есть, model routing нет
- ⬜ **GAP:** Evaluator-Optimizer (P5) — bmad-code-review = single-shot, не loop
- ⬜ **GAP:** Embedded skills (vision step 2) — пока reads из `<project>/.claude/skills/bmad-*/`
- ⬜ **GAP:** 19 Medium + 8 Low S10 findings (review-findings-followup) — backlog cleanup

### Фаза 3 — Test & Release ⬜ NOT STARTED

- ⬜ **GAP:** Нет eval suite — benchmark из N stories с известным baseline
- ⬜ **GAP:** Pilot Odyssey Wave 1a (production-mode end-to-end) не запущен
- 🟡 Red-team/security: lesson `code_review_pipeline_gaps` зафиксирован (4 gate'а identified, не встроены)
- ⬜ R3 security-auditor minors: canonicalize+allowed-root для `--lessons-dir` / `--skills-root` / `--orchestrator-home`
- ⬜ Acceptance criteria для exit из фазы не определены

### Фаза 4 — Deploy ⬜ NOT STARTED

Зависит от закрытия Test & Release. Deploy = регулярные production runs на Odyssey, потом на других BMad-проектах.

### Фаза 5 — Monitor & Improve ⬜ NOT STARTED

- ⬜ Dashboard (cost / cache hit rate / escalation rate / pass rate per epic)
- 🟡 Retrospective trigger — частично есть (через phase-5 wrapper), не continuous loop
- ⬜ Self-learning (vision step 6) — deferred, нужна Anthropic memory tool + policy auto-update
- ⬜ TTS notifications (backlog)

---

## 5. Priority queue

Сортировано по фазам ADLC — закрываем фазы по порядку. Внутри фазы — по влиянию на закрытие gate'а.

### Сейчас → закрыть фазу 2 Build

1. **Real end-to-end pilot validation** — Antares Story 1.2 или 3.1 (auto-split) в production-mode
   - Паттерны: P4 + P3
   - Критерий: один полный story end-to-end без manual intervention
   - **Это переходный шаг между Build и Test** — pilot валидирует Build, готовит Test
2. **Define success metrics** (закрывает gap фазы 1)
   - Зафиксировать в spec пороги: pass rate, cost, escalation rate, cache hit rate
   - Без этого фаза 3 не имеет gate'а
3. **Embedded skills** (vision step 2)
   - Копии `bmad-auto-dev` + `bmad-code-review` в `bmad-orchestrator/skills/`
   - Снимает зависимость от per-project skill drift
   - Паттерн: — (рефакторинг storage)

### Открыть фазу 3 Test & Release

4. **Eval suite** — 5-10 stories разной сложности из Odyssey backlog
   - Метрики: pass rate, токены, время, escalation rate
   - «BMad-bench в миниатюре»
5. **Evaluator-Optimizer loop** (паттерн P5) — bmad-code-review → итеративный gate
   - Решает lesson: code-review pipeline gaps (auto-fix top 12, остальные defer)
   - **Главный архитектурный gap фазы 2/3**
6. **4 security gates** из lesson `code_review_pipeline_gaps`

### Фазы 4-5 (после закрытия 3)

7. **Multi-LLM routing** (паттерн P2) — Opus/Sonnet/Haiku/GPT-4o через LiteLLM
8. **Observability dashboard** — cost, cache, escalations, quality
9. **Vision steps 3-7** — embedding phase 3/2/1 skills, self-learning loop, multi-project skill sync

### Параллельно/между инициативами

- Backlog cleanup (19 Medium + 8 Low S10 findings) — quick wins
- R3 security minors (canonicalize paths)
- Manual merge `integration/canonical_patches_port → main` — оргвопрос
- TTS notifications

---

## 6. References

- **Universal methodology:** `spec/methodology.md`
- **Architecture spec:** `spec/spec_master_orchestrator.md`
- **Vision:** memory `project_vision_master_bmad_builder.md`
- **Progress tracker:** `.claude/memory/progress.md`
- **Project status:** memory `project_milestone_parallelism_initiatives_complete.md` (post `6e8a18e`)
- **Current pilot artifact:** Antares Story 1.1 commit `9e84da6`
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

**Last updated:** 2026-05-18
**Status:** v1 — created post parallelism_initiatives merge `6e8a18e`, before Odyssey Wave 1a pilot
**Owner:** user + Claude orchestrator
