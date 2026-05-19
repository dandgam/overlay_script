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
| **P2 Routing** | `RoleModels` (planner=Opus, reviewer=Opus, dev=Sonnet, routine=Sonnet) + `set_model` tool для runtime swap; preset routing; **auto-elicitation engine** (Tier 0/1/2) для WORKER_ELICITATION → auto_resolve/escalate | ✅ Реализован (`config.py` + `agent/system_prompt.py` + `elicitation/`) |
| **P3 Parallelization** | DAG planner + worker pool (4-8 параллельных workers в worktrees); sectioning независимых stories | ✅ Merged `6e8a18e` (parallelism_initiatives S1..S11) |
| **P4 Orchestrator-Workers** | **Основной паттерн** — central orchestrator делит эпик на stories, динамически спавнит workers с tool harness | ✅ Реализован (наш core loop) |
| **P5 Evaluator-Optimizer** | bmad-code-review → autofix loop (Pilot 2 прошёл «via autofix loop»). `max_review_iterations` cap в `CodeReviewGates` (default=3) + `_gate_iteration_cap` + `review_iteration` payload field. Runaway-loop guard escalates на HUMAN_QUERY за cap. **Supervisor circuit breaker** добавил вторую защиту (max_consecutive_escalations). **Self-learning loop (Phase 5 #11)**: extract lessons → risk gate → auto-apply low-risk → regression guard N=2 waves → auto-rollback. Pluggable ExtractorProtocol + StubExtractor. 4 bus triggers. Monthly cron scheduler (asyncio task + CLI). | ✅ Closed 2026-05-18 (v12) |

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

### Memory infrastructure ✅ DONE (Phase 2 retroactive discovery)

- `agent/memory/` — 3-уровневая модель + schedule + gates + proposals
- `agent/tools/memory.py` — vendor-agnostic `read_memory` / `write_memory` (custom SDK tools, work for Claude / OpenAI / Gemini equally)
- `agent/memory/memory_tool.py` — Anthropic native tool definition (deferred wiring per `run.py:32-37`)

### Фаза 3 — Test & Release ⬜ NOT STARTED

- ⬜ **GAP:** Нет eval suite — benchmark из N stories с известным baseline
- ⬜ **GAP:** Production-mode end-to-end pilot на реальном target BMad-проекте не запущен
- ✅ **Red-team gates** (closed retroactively 2026-05-18 — discovery): 4 gate'а из lesson `code_review_pipeline_gaps` уже в `agent/run.py` после Phase 2 closure (`0070d6f`):
  - `_gate_p0_threshold` (P0-count) + `_gate_compliance` (152-ФЗ/187-ФЗ tags) + `_gate_test_coverage` (ratio + todo!() placeholders) + `quarterly_sweep_subscriber` (compliance sweep каждые N stories)
  - Tests: 35 PASS в `test_e5_code_review_gates.py`. Methodology запись была стале
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

### Фаза 3 Test & Release ✅ DONE (2026-05-19)

- ✅ **Step A: Eval harness + 5 synthetic cases** (2026-05-18) — `bmad_orchestrator.eval` module (metrics + runner) + CLI `bmad-orchestrator eval run` + 28 unit-тестов + 5 cases (2 easy + 2 medium + 1 hard) в `evals/cases/`. Mock-mode baseline: 5/5 PASS (100%)
- ✅ **Step B: Real BMad stories** (2026-05-19) — 10 BMad-shaped stories в `evals/cases/real/` (3 easy + 5 medium + 2 hard) + harness CLI flags (`--cases-dir`, `--project-root`, `--tag`) + manifest schema с `tags` field + `filter_cases_by_tags` (OR semantics) + baseline scaffold `evals/baselines/phase3-step-b-baseline.json`. Commits S7 `5073856` (harness + first 5 cases) → S8 (5 more cases + baseline). Test deltas: +7 (S7) + 0 (S8 fixtures-only). Aggregate medians populate after first prod-pilot real run.
- ✅ **Auto-elicitation policy engine** (2026-05-18) — P2 Routing двухтировая (Tier 0 hard-override + Tier 1 rule match + Tier 2 LLM-judge stub + window cap). Модуль `bmad_orchestrator.elicitation` (4 файла, ~580 строк) + `runtime/elicitation_routing.py` subscriber + Settings.elicitation_policy_path. **34 unit-тестов**, mypy/ruff clean. Tests: 1509 PASS
- ✅ **R3 security minors** (2026-05-18) — `cli/path_validation.py` (`safe_resolve_path` + `ensure_inside_root`) + deny-list системных префиксов (`/etc`, `/root`, `/proc`, `/sys`, `/dev`, `/boot`, `/lib*`, `/sbin`, `/bin`, `/usr/bin*`, `/var/log`). Applied на 4 callsites в `cli/main.py`. 18 unit-тестов покрывают traversal/symlink/deny-list escapes
- ✅ **Wire worker → review_iteration** (2026-05-18) — `_read_review_iteration` helper в `worker_spawn.py` читает `<worktree>/_bmad/auto-dev-state/current-batch.json:retries[story_id]`, surface через `_wait_and_finalize` + mock path + bridge в `run.py`. P5 loop integration закрыт end-to-end (worker → JSONL → bus → `_gate_iteration_cap`). 11 unit-тестов

### Фаза 4 — Deploy (после закрытия Phase 3)

9. ✅ **Supervisor LLM-loop + Terminal TUI** (2026-05-18) — P4 Orchestrator-Workers + P2 Routing двухтировая
   - `supervisor/` модуль (policy + engine + actions + audit + llm_judge stub) — ~720 строк
   - `runtime/supervisor_subscriber.py` подписан на 5 event типов (HUMAN_QUERY, WORKER_HALT_FILE, BUDGET_THRESHOLD_HIT, WORKER_SILENT_FAILURE, COMPLIANCE_SWEEP_NEEDED)
   - Tier 0 hard rules (`config/supervisor-policy.yaml` 3 default rules) → Tier 1 LLM-judge (StubJudge, real Sonnet behind future flag) → Tier 2 fail-safe escalate
   - Rate limiter (10/min default) + circuit breaker (3 consecutive escalations → abort)
   - Anti-loop: skip events с `payload.source=supervisor`
   - TUI: `_supervisor_panel` показывает последние N решений + judge state + rate capacity
   - **50 unit-тестов** в 6 файлах. Tests: 1588 PASS. mypy/ruff clean
   - Settings.supervisor_policy_path через `--supervisor-policy` (CLI флаг отложен до прод-pilot'а)
10. **Production pilot на реальном target BMad-проекте** — выбор проекта остаётся на момент запуска (project-agnostic)
10a. ✅ **Phase 4 hardening Session 1 — #3 Banned-phrase linter** (2026-05-19, commit `85ee1ae`)
   - spec_phase4_hardening §1.3 — Tier 1 pre-pilot hardening
   - `_gate_banned_phrases` + `_load_banned_phrases` в `agent/run.py` (5-й gate в `code_review_subscriber`)
   - `skills/policy/banned-phrases.yaml` — overridable через `Settings.banned_phrases_path`
   - Blocks `verdict=approve` если summary содержит red-flag фразы («should work», «Done!», «Great!», «looks good to me», etc.)
   - **10 tests** (8 spec + 2 extra). Tests delta: +10
10b. ✅ **Phase 4 hardening Session 1 — #4 Permission deny-list** (2026-05-19, commit `8a6a78c`)
   - spec_phase4_hardening §1.4 — Tier 1 pre-pilot hardening
   - `FsDenyList` + `BashDenyList` + `compile_deny_lists` + `match_fs_deny` + `match_bash_deny` в `runtime/sandbox.py`
   - `_scan_fs_access` + расширен `_scan_bash` в `agent/safety/hooks.py`
   - PreToolUse hook блокирует Read/Glob/Grep/Edit/Write/Bash при совпадении с deny-list
   - `skills/policy/sandbox-deny-list.yaml` — overridable через `BMAD_DENY_LIST_PATH`
   - Работает даже при NoSandbox fallback (bwrap недоступен)
   - **14 tests** (spec exact). Tests delta: +14
10c. ✅ **Phase 4 hardening Session 2 — #1 SessionStart hook** (2026-05-19, commit `c00ff93`)
   - spec_phase4_hardening §1.1 — Tier 1 pre-pilot hardening
   - `agent/safety/session_start.py` — `build_session_start_block` + `inject_into_worker_env` + `_resolve_skill_slug`
   - `agent/safety/policies/worker_session_policy.md` — 4 секции (no merge без gate, 3-attempt cap, evidence-based completion, sandbox boundaries)
   - `runtime/worker_spawn.py` — wire в `_build_worker_env` перед subprocess.Popen
   - Инжектирует `ORCHESTRATOR_SESSION_BOOTSTRAP` env-var в каждый worker subprocess
   - **10 tests** (5 canonical skills + 3 inject + 2 integration). Tests delta: +10
10d. ✅ **Phase 4 hardening Session 2 — #2 PreCompact + SessionStart memory persistence** (2026-05-19, commit `0ba9407`)
   - spec_phase4_hardening §1.2 — Tier 1 pre-pilot hardening
   - `agent/memory/levels.py` — `MemoryPersistor.dump_state` / `load_state` (stale guard, corruption guard)
   - `runtime/event_loop.py` — +`WORKER_STATE_PERSISTED` event (26 events total)
   - `runtime/worker_spawn.py` — `trigger_precompact_dump` + wire `load_state` в SessionStart block
   - `agent/safety/session_start.py` — embed `load_state` output в block якщо state есть («Resumed from: retry=N, scope_drift=X»)
   - **9 tests** (4 dump/load + 3 model-swap + 2 SessionStart block). Tests delta: +9
10e. ✅ **Phase 4 hardening Session 3 — #5 Two-stage merge-gate split** (2026-05-19, commit `c6d240c`)
   - spec_phase4_hardening §2.5 — Tier 2 instrumentation
   - `agent/skills/merge-gate-spec/SKILL.md` — AC coverage only
   - `agent/skills/merge-gate-quality/SKILL.md` — code quality only
   - `agent/skills/merge-gate/SKILL.md` — deprecated stub
   - `agent/run.py` — split code_review_subscriber на 2-stage invocations, worst-wins merge
   - `runtime/event_loop.py` — +`MERGE_GATE_STAGE_COMPLETED` event (27 total)
   - **9 tests** (3 unit verdict merge + 4 unit stage ordering + 2 integration). Tests delta: +9
10f. ✅ **Phase 4 hardening Session 3 — #6 Stop-hook cost + learning consolidation** (2026-05-19, commit `e4eadfb`)
   - spec_phase4_hardening §2.6 — Tier 2 instrumentation
   - `runtime/stop_hook_subscriber.py` — новый subscriber
   - `runtime/event_loop.py` — +`STORY_COMPLETED` + `STORY_METRICS_AGGREGATED` events (29 total)
   - `config.py` — `Settings.cost_tracker_debug_mode` (default False)
   - **11 tests** (5 aggregation + 3 self-learning trigger + 2 integration + 1 math). Tests delta: +11
10g. ✅ **Phase 4 hardening Session 3 — #7 pass^k метрика** (2026-05-19, commit `b7649fc`)
   - spec_phase4_hardening §2.7 — Tier 2 instrumentation
   - `eval/metrics.py` — `pass_at_k` + `pass_consistency_at_k`
   - `eval/runner.py` — `repeat` parameter (run each case K times)
   - `cli/main.py` — `--repeat` / `--metric` flags в `eval run` команде
   - **9 tests** (6 unit math + 3 integration). Tests delta: +9
11. ✅ **Interactive TUI menu — Session 2 (M2 forms + execute + polish)** (2026-05-18)
   - `cli/menu/form_screen.py` — FormScreen с typed widgets per ParamSpec: Select (Literal), Checkbox (bool), Input(integer) (int), Input (str/Path). Live CLI preview, inline validation, pre-filled defaults, required markers (`*`). Wires to ConfirmScreen (destructive) or ExecuteScreen (normal)
   - `cli/menu/confirm_screen.py` — ConfirmScreen для destructive commands: typed-name confirmation (как `terraform destroy`), exact match guard, preview CLI command
   - `cli/menu/execute_screen.py` — ExecuteScreen: in-process call через `asyncio.to_thread`, stdout/stderr capture via `contextlib.redirect_stdout/stderr`, 100ms flush interval, cancel button (`ctrl+c`), toast ✅/❌, «Any key to return»
   - `cli/menu/browse_screen.py` — search mode (`/`): Input widget показывается at top, live case-insensitive substring filter, `escape` для выхода из search. Leaf commands теперь открывают FormScreen (not toast)
   - `filter_items()` — pure testable search helper
   - `_build_cli_preview()`, `_cast_value()`, `_build_kwargs()` — pure testable helpers для form/execute
   - **49 новых тестов** в 4 файлах (test_menu_form, test_menu_confirm, test_menu_execute, test_menu_search). Tests: **1762 PASS** (+105 vs v12 baseline 1657). mypy/ruff clean

### Фаза 5 — Monitor & Improve

11. ✅ **Memory foundation** (2026-05-18 — retroactive close): vendor-agnostic уже работает
    - Custom SDK tools `read_memory` / `write_memory` (vendor-agnostic) в `agent/tools/memory.py`
    - 3-уровневая модель (tactical/strategic/architectural) в `agent/memory/levels.py`
    - Schedule + hard gates + proposals (`gates.py` / `schedule.py` / `proposals.py`)
    - Native Anthropic `memory_20250818` намеренно пропущен (claude_agent_sdk не exposed server-managed tool block) — custom tools покрывают workflow; миграция на OpenAI/Gemini = native tool и не понадобится
    - Tests: `test_e7_project_memory.py` + `test_s7_memory_retro.py`
    - Vision step 6 foundation = ✅. Что осталось — self-learning loop (использовать накопленную память, separate initiative)
12. **Observability dashboard** — cost / cache hit / escalations / pass rate per epic
13. **TTS notifications** (backlog)
14. **Vision steps 3-7** — embedding phase 3/2/1 skills, self-learning loop, multi-project skill sync

### Параллельно/между инициативами

- Backlog cleanup (19 Medium + 8 Low S10 findings) — quick wins
- Manual merge `integration/canonical_patches_port → main` — оргвопрос
- Doc-of-experiment Pilot 2 retrospective (опционально, memory уже captured)

### Backlog — research findings из утёкшего Claude Code (R1/R2 closed 2026-05-19, R3-R5 deferred)

Источник: анализ `/home/server/crm/claude-code-main/` (leaked Claude Code source, npm sourcemap leak 2026-03-31). Идеи — архитектурные паттерны, НЕ копирование кода (proprietary Anthropic PBC).

- ✅ **R1 (P1): AbortController per worker** (2026-05-19, integration commit `6929ee7`, S4 of pilot_findings_closure) — `CancellationToken` + module-level `_REGISTRY` keyed by `story_id::branch::pid`, `cancel_worker(SIGTERM→2s→SIGKILL)`, supervisor action wired, `WORKER_CANCELLED` event. Tests +8.
- ✅ **R2 (P1): MCP server readiness polling** (2026-05-19, integration commit `2a3a0d6`, S5 of pilot_findings_closure) — `runtime/mcp_readiness.poll_mcp_ready` (30s timeout / 500ms interval, injectable clock+sleep), `spawn_worker` pre-Popen gate, `MCPNotReadyError`, `Settings.required_mcp_tools`, `MCP_NOT_READY` event. Tests +8.
- ⬜ **R3 (P2)** · `Тип: ✨ Улучшение` — **Per-turn token snapshot** — сохранять token count по каждому шагу worker'а (не только aggregated total). Mid-wave cost tracking + pause-resume без потери granularity. Ref: `tasks/LocalAgentTask:41-104`. Reserved event `COST_SNAPSHOT_RECORDED` planned (see real-6 eval fixture).
- ⬜ **R4 (P2)** · `Тип: ✨ Улучшение` — **Stale worktree GC** — periodic cleanup orphan worktrees из crashed workers (regex slug pattern + 30-day mtime cutoff). Ref: `utils/worktree.ts:1058`
- ⬜ **R5 (P3)** · `Тип: ✨ Улучшение` — **Fail-closed cleanup policy** — не удалять worktree если `git status` вернул ошибку или есть unpushed commits (guard для R4). Ref: `utils/worktree.ts:1113`
- 💡 Дополнительно: bundled skills которых нет у нас — `skillify` (command→skill конвертер), `stuck` (escape failure loop), `remember` (auto-memory → CLAUDE.md promotion)

### Backlog — pilot findings (closed 2026-05-19 via integration/pilot_findings_closure)

Источник: 7 прогонов real-пилота на Antares Epic 1 (stories 1.3/1.4/1.5). 2 настоящих
бага Virgil уже зафикшены (D-Bus `0c0967d`, overlay `bb4fdf1`). Все P1/P2/P3 находки
закрыты в integration ветке `integration/pilot_findings_closure` (S1..S8). Финальный
merge в `main` — manual (Auto merge=false).

- ✅ **P1 #1: mark-done ID-format mismatch + #9 spawned/succeeded counter** (S1, commit `7edc9bb`) — `normalize_story_id` для dotted↔kebab lookup, `_tail_and_emit_completion` returns outcome tag, log `spawned=X succeeded=Y failed=Z`. Tests +12.
- ✅ **P1 #2: bmad-auto-dev → orchestrator verdict event disconnect** (S2, commit `4ac3e56`) — Variant B (orchestrator-side fallback reader): `runtime/verdict_fallback.py` (~80 LOC) считывает Stage-6 review log и эмитит синтетический CODE_REVIEW_VERDICT с `source=runner_log_fallback`. Tests +9.
- ✅ **P1 #3: Sonnet autofix LOC-300 cap → auto-escalate + auto-split trigger** (S3, commit `949d8e7`) — `runtime/autofix_routing` (typed policy + python -m CLI) + runner-side bash branch (security-critical|iter≥2 → opus); decomposer subscriber emits STORY_AUTO_SPLIT on loc_cap halt. Tests +20.
- ✅ **P2 #6: subscription-mode budget auto-detect** (S6, commit `185a948`) — `runtime/budget_autodetect.evaluate_budget_disabled` idempotent per-run, emits `BUDGET_AUTO_DISABLED` on subscription auto-path (manual `BMAD_DISABLE_BUDGET=1` suppressed from emission). Tests +5.
- ✅ **P2 #7: pre-flight halt-state check** (S6, same commit `185a948`) — `spawn_worker(auto_clear_halt=False)` + `WorkerHaltPrespawnError` + pre-Popen halt-reason.txt gate + `WORKER_HALT_PRESPAWN` event. Tests +5.
- ✅ **P2 #8: subprocess timeout adaptive / configurable per story** (S3, same commit `949d8e7`) — runner default 1800→3600, `BMAD_RUNNER_CLAUDE_TIMEOUT_SEC` env override, `pick_timeout_sec(ac_count)` bucket logic (0→DEFAULT, 1-3→SMALL, 4-8→MEDIUM, 9+→LARGE). Tests +8.
- ⬜ **P3** · `Тип: ✨ Улучшение` — **backlog-writer subscriber (auto-capture dev findings)** — сейчас Virgil
  авто-пишет только в `skills/policy/*.yaml` (operational tuning) + `retrospective.md`
  (per-wave). Архитектурные находки (subprocess_timeout patterns, отсутствие
  verdict event, recurring worker_silent_failure) попадают в dev backlog
  (`methodology-virgil.md` §5) **только через ручной просмотр JSONL**.
  Возможная мета-фича: subscriber на events.jsonl, который из паттернов
  (≥N occurrences одного типа за W прогонов) генерит **черновики backlog-items**
  (через 888 → gap-analysis или напрямую markdown-шаблоном). Не автокоммит —
  draft → human-review → merge.
  Связано с self-learning loop, но другой слой: тот тюнит цифры, этот ловит
  АРХИТЕКТУРНЫЕ паттерны. После P1-3 (критический путь).

### Backlog — НОВЫЕ pilot findings (Antares 1a real 2026-05-19, post-closure)

Источник: первый production pilot после merge `pilot_findings_closure` (`f2ff857` на main).
Запуск `virgil run --project antares --wave 1a --real --story 1.3/1.4/1.5`. Все 3 stories
halted на одном паттерне, 1.3 с реальной работой потеряна (не merged в integration).

- ✅ **P1 NEW-1: `--project <slug>` flag не побеждает `ORCHESTRATOR_TARGET_PROJECT` env var** — DONE 2026-05-19 (commits `64f07a9` + `0348b75`, S1 `pilot_findings_closure_v2`): CLI `run` резолвит registry в strict mode, `ProjectNotFoundError` fail-loud, `run_orchestrator` принимает pre-resolved `Settings`. —
  при запуске `virgil run --project antares` оркестратор создал worktrees в
  `/home/server/odyssey/.worktrees/wt-1.X` несмотря на `config/projects.yaml::antares.path=/home/server/Antares`.
  Workaround: `ORCHESTRATOR_TARGET_PROJECT=/home/server/Antares virgil run ...` (env override
  поверх env override). Fix: `runtime/project_registry.py` resolved path должен override'ить
  `Settings.target_project` ДО любых downstream resolvers. См. memory
  [[project_backlog_target_resolution_bug]]. ~0.5 сессии + тест
  `test_project_flag_overrides_env.py`. **Блокирует чистый запуск любого target проекта.**

- ✅ **P1 NEW-2: runner Stage 7 cleanup ломается на reused worktree** — DONE 2026-05-19 (commit `0e4e6d0`, S2 `pilot_findings_closure_v2`): Layer A — `stage7_cleanup_feature_branch()` graceful skip при worktree-hold + synthetic verdict + `BMAD_RUNNER_SKIP_STAGE7=1`. Layer B — pure detector `runtime/worker_silent_failure.py` + wire-in `_tail_and_emit_completion`, EventType #35 `RUNNER_CLEANUP_FAILED_REUSED_WORKTREE`. Layer C (pre-spawn refresh) deferred в follow-up. — 3/3 stories
  на Antares 1a (включая 1.3 с 2 коммитами реальной autofix-работы) halted одинаково:
  `error: cannot delete branch 'feature/1.X' used by worktree at '/home/server/Antares/.worktrees/wt-1.X'`.
  `bmad-auto-dev-runner.sh` Stage 7 cleanup делает `git branch -D feature/<id>` на
  ветке, которая checked out в worktree — git отказывает. Runner exit 1, но claude -p
  обёртка exit 0 → orchestrator пишет `worker_silent_failure`. Особенно болезненно:
  **1.3 fully прошла Stage 4-6 (dev-story + autofix iteration H1/H2/H3/M2-M11),
  2 новых коммита на feature/1.3 (+1286/-37 LOC, 22 файла)** — но Stage 7 cleanup
  fail → НЕТ verdict event → `merge_to_integration_subscriber` не сработал →
  **integration/wave-1a ветка не создана, реальная работа потеряна на feature/1.3**.
  Fix варианты (комбинация):
  - **Runner-side:** Stage 7 detect «used by worktree» error → skip cleanup gracefully
    + emit `verdict=approve` если есть коммиты с base_sha.
  - **Orchestrator-side detect:** `worker_silent_failure` detector распознаёт pattern
    `cannot delete branch ... used by worktree` в stdout_lines → emit synthetic
    `runner_cleanup_failed_reused_worktree` event + treat as `verdict=approve` если
    commits есть.
  - **Pre-spawn worktree refresh:** если `branch_tip == base_sha` (story already done) →
    emit `verdict=approve` без spawn'а; иначе fresh worktree через `--force-new-worktree`.
  См. memory [[project_backlog_runner_reused_worktree_cleanup]]. ~1-1.5 сессии.
  **Блокирует ЛЮБОЙ production pilot на projects где worktrees уже существуют (т.е. почти всегда).**

- ✅ **P2 NEW-3: S1 `normalize_story_id` resolver не находит match** — DONE 2026-05-19 (commit `729650f`, S1 `pilot_findings_closure_v2`): `resolve_sprint_status_key` получил детерминированный tie-break (lexicographic sort + `sprint_status_key_ambiguous` warning) для kebab+slug composite. — log показал
  `pilot_mark_done_unresolved reason='no matching sprint-status key in any epic block' spawned_id=1.3`,
  при этом sprint-status имеет ключ `1-3-fastapi-app-lifespan-health`. Наш
  `resolve_sprint_status_key` (S1, commit `7edc9bb`) видимо не сматчил `1.3` → kebab+slug
  composite key. Fix: расширить fuzzy match — split kebab key на dotted-prefix +
  slug-suffix, match только по prefix. ~0.3 сессии.

- ✅ **P2 NEW-4: `worker_completed status=success` при runner exit 1** — DONE 2026-05-19 (commit `5bd9ff1`, S3 `pilot_findings_closure_v2`): `parse_inner_exit_code` сканирует stdout-tail на `^(?:❯ )?Exit code: (\d+)$`; при inner!=0 && outer==0 `_tail_and_emit_completion` ставит `status=failure` + `inner_exit_code`/`outer_exit_code` в payload. race условие:
  outer claude -p exit 0 (он dutifully reported inner exit), но inner
  `bmad-auto-dev-runner.sh` exit 1.

**Связь с pilot_findings_closure:** этот pilot — первый РЕАЛЬНЫЙ production run после
merge `f2ff857`. Подтвердил что fixes работают (S6 budget auto-disable сработал; S1
counter split дал spawned/succeeded/failed; S2 verdict-fallback не помог в этом случае
потому что Stage 7 падает ДО Stage 6 review log creation). Нашёл 4 новых P1/P2 баг'а,
из которых **P1 NEW-2 — главная блокировка** для любого production pilot.

**Recommended next initiative:** ✅ `pilot_findings_closure_v2` ЗАКРЫТА 2026-05-19
(S1..S3 на `integration/pilot_findings_closure_v2`, NEW-1..NEW-4 + EventType #35,
2009 PASS). Layer C (pre-spawn worktree refresh) deferred.

### Backlog — validation replay findings (Antares 1a replay 2026-05-19)

Источник: validation-replay pilot Antares 1a ПОСЛЕ merge `pilot_findings_closure_v2`
(`f76816f`). Результат `spawned=3 succeeded=2 failed=1`. NEW-2 validated, остальные
фиксы оказались частичными. Детали — memory [[project_pilot_antares_1a_replay_2026-05-19]].

- ✅ **NEW-2 VALIDATED** — `grep -c "cannot delete branch"` по логу = 0 (прошлый run халтил 3/3). Stage 7 graceful cleanup работает в проде.
- ✅ **NEW-1-completion (P1): pilot body перечитывает env** — DONE 2026-05-19 (commit `80a15d2`, S1 `pilot_findings_closure_v3`): `settings` сделан required keyword-only на `_run_mock_pilot`/`_run_real_pilot`/`_run_real_pilot_body`, оба `load_settings()` из pilot-chain убраны; ~25 test-callsite'ов обновлены.
- ✅ **NEW-3-completion (P2): resolver всё ещё не матчит** — DONE 2026-05-19 (commit `80a15d2`, S1): root cause = mark-done loop читал raw YAML и понимал только legacy `epics:` layout; на BMad-flat `development_status:` resolver вообще не вызывался. Fix — новый `mark_sprint_status_done` в `bmad_format.py`, диспатчит по layout.
- ✅ **NEW-6 (P2): NEW-4 format gap** — DONE 2026-05-19 (commit `1a80fa1`, S3): `_INNER_EXIT_RE` = `^(?:❯\s*)?(?:Exit code:\s*|EXIT_CODE=)(\d+)$` — альтернация ловит оба формата, last-match семантика сохранена.
- ✅ **NEW-7 (P1, ГЛАВНЫЙ БЛОКЕР): integration ветка не создаётся даже на succeeded** — DONE 2026-05-19 (commits `db48b2c` S2 + `1a80fa1` S3): root cause = в real-режиме никто не дренировал шину (`dispatch_one` не вызывался → вся W4-цепочка мёртвая). Fix — `EventLoop.drain()` + `_reconcile_success_verdicts` synthetic-verdict fallback; `_run_real_pilot_body` делает drain→reconcile→drain перед `real_pilot_done`. EventType #36 `INTEGRATION_MERGE_SKIPPED` (reason no_commits/verdict_missing/ff_conflict) для observability.
- ✅ **NEW-5 (P2): dirty worktree блокирует Stage 0** — DONE 2026-05-19 (S4 `pilot_findings_closure_v3`): `spawn_worker` получил pre-spawn dirty-worktree gate — `auto_clean_dirty_worktree=True` (default) → `git reset --hard` + `clean -fd` в managed worktree, `False` → `WORKER_HALT_PRESPAWN reason=dirty_worktree` без spawn. Env override `BMAD_AUTO_CLEAN_DIRTY_WORKTREE`.
- ✅ **NEW-8 (P2): orchestrator висит ~13 мин после `real_pilot_done`** — DONE 2026-05-19 (S4): `_shutdown_orchestrator` — `bus.stop()` + cancel orchestrator-spawned background tasks (`all_tasks() - pre_existing - {current}`), `asyncio.wait` hard-timeout 30s guard, лог `orchestrator_shutdown_complete`.

**Closed by initiative:** `pilot_findings_closure_v3` (S1..S4 на `integration/pilot_findings_closure_v3`,
NEW-1-completion/NEW-3-completion/NEW-5/NEW-6/NEW-7/NEW-8 + EventType #36, tests 2009→2038).
Merged в main `be655eb`.

### Backlog — pilot run #3 findings (Antares 1a после v3 merge, 2026-05-19)

3-й production pilot ПОСЛЕ merge v3 (`be655eb`). `spawned=3 succeeded=0 failed=3`,
integration ветка не создана. Детали — memory [[project_pilot_antares_1a_run3_2026-05-19]].

- ✅ **NEW-1-completion validated** — worktrees в `/home/server/Antares` без env-override.
- ✅ **NEW-8 validated** — `orchestrator_shutdown_complete elapsed_sec=0.0` (чистый выход).
- ✅ **NEW-7 validated в run #4** — на run #3 НЕ проверялся (`_reconcile_success_verdicts`
  реконсилит только success verdict'ы; все 3 story `failed` → нечего мержить). Merge-path
  провалидирован в pilot run #4 после merge v4 — см. секцию ниже.
- ✅ **NEW-9 (P1, КОРЕНЬ)** — DONE 2026-05-19 (S1 `pilot_findings_closure_v4` `80dd56d`):
  `decide_worker_status(verdict, new_commits_count, inner_exit, outer_exit)` —
  `verdict=approve` + commits → `success` независимо от runner exit code; `request_changes`/
  `reject` → `failure`; verdict=None → старый exit-code fallback. `read_runner_verdict`
  читает stage6 review log. Wired в `_tail_and_emit_completion`; `worker_completed` payload
  получил `verdict`/`new_commits_count`/`status_decided_by`. +11 tests. Разблокирует NEW-7.
- ✅ **NEW-10 (P2)** — DONE 2026-05-19 (S2 `pilot_findings_closure_v4`): модуль
  `runtime/worker_events.py` — `merge_worktree_events` мержит worktree-internal
  `events.jsonl` в главный `runs/<wave>/wt-<id>.events.jsonl` (append + dedup по ts) после
  `worker_completed`; `detect_stage_marker` эмитит `worker_stage_progress` лог-строки чтобы
  orchestrator-лог не молчал. +7 tests.
- ✅ **NEW-5 recheck (P2)** — DONE 2026-05-19 (S2 `pilot_findings_closure_v4`):
  dirty-worktree gate (`filter_dirty_outside_claude`) игнорирует `.claude/` пути — embedded
  skills (~73 файла) больше не считаются «грязью»; `_clean_dirty_worktree` использует
  `git clean -fd -e .claude` чтобы реальная грязь чистилась без сноса skills. +5 tests.

**Closed by initiative:** `pilot_findings_closure_v4` (S1..S2, NEW-9 + NEW-10 + NEW-5
recheck), merged в main `48febc0`.

### Backlog — pilot run #4 findings (Antares 1a после v4 merge `48febc0`, 2026-05-19)

**ПЕРВЫЙ end-to-end production success Virgil.** Запуск 17:14 после merge v4,
`BMAD_AUTO_SPLIT=1`. Результат `spawned=3 succeeded=2 failed=1`. **`integration/1a` ветка
СОЗДАНА**, story 1.3 смержена end-to-end (dev `2f8b442` + autofix `6808712`,
`story_merged sha=68087124`). Детали — memory [[project_milestone_first_integration_merge]].

- ✅ **NEW-7 VALIDATED** — verdict→reconcile→merge pipeline работает:
  `integration_reconcile_synthetic_verdict commits=2 → story_merged`. Pipeline замкнут
  (worker → dev → review → verdict → reconcile → merge → integration). Раньше работа
  «оседала» на feature-ветках — теперь доходит до integration автономно.
- ✅ **NEW-9 validated** — story 1.3 с коммитами дошла до success, не отброшена по exit code.
- ✅ **NEW-8 validated** — `orchestrator_shutdown_complete elapsed_sec=0.0`.

Не дошли до integration (1.4/1.5) — fixable, pipeline не разорван:

- ✅ **NEW-11 (P2) · Тип: 🐛 Баг — ruff build_check_halt** — `build_check_halt command=ruff
  exit_code=1` на 1.3 и 1.4. ruff в worktree падает — вероятно конфиг проекта или версия.
  **DONE** (v5 S1, `integration/pilot_findings_closure_v5`): флаг `skip_if_no_ruff_config` на
  ruff build-check команде — worktree без ruff-конфига → ruff пропускается gracefully
  (exit 0 + audit-лог), не halt. Конфиг есть → ruff бежит, реальные нарушения халтят как
  раньше.
- ✅ **NEW-12 (P2) · Тип: 🐛 Баг — pre-commit config missing** — `stage5_recovery_failed:
  No .pre-commit-config.yaml file` на 1.3/1.4. Worktree без pre-commit конфига → git commit
  ругается. **DONE** (v5 S1): `PRE_COMMIT_ALLOW_NO_CONFIG=1` инжектится в env воркера
  (`WORKER_ENV_INJECTED` в `worker_spawn.py`) + pre-spawn detector логирует
  `precommit_config_absent`. Конфиг в чужой worktree НЕ создаётся (Critical Boundary).
- ✅ **NEW-13 (P2) · Тип: 🐛 Баг — security_review error → circuit breaker abort** — story
  1.4 ушла в security review, тот вернул `verdict=error` (не approve/reject), 3 escalations
  подряд → `supervisor_abort_pipeline circuit breaker`. Закрыто в
  `integration/pilot_findings_closure_v5` S2: `SupervisorEngine._is_security_review_error`
  не инкрементит circuit breaker на `verdict=error` (technical failure ≠ escalation);
  `security_review_subscriber` ретраит runner `error_retry_max` раз (default 1), эмитит
  `SECURITY_REVIEW_ERROR` (EventType #37) per attempt, при исчерпании — одиночная
  HUMAN_QUERY эскалация story (не abort pipeline). +7 tests.

**Recommended next initiative:** pilot replay (Antares 1a) — NEW-11/12/13 закрыты в
`integration/pilot_findings_closure_v5`; после merge v5 → replay, ожидаем 3/3 stories
(1.3/1.4/1.5) в `integration/1a`. Pipeline замкнут (NEW-7 ✅), блокеры 1.4/1.5 устранены.
**Spec:** `spec/spec_pilot_findings_closure_v5.md` (v1.0, READY for `/auto-loop-spec-short`
bootstrap — 3 P2 items, ~2 сессии).

---

### Backlog — pilot run #5 findings (Antares 1a после v5 merge `dbeb936`, 2026-05-19)

**Replay-проверка фиксов NEW-11/12/13. Результат — НЕ 2/2.** Запуск 19:58 (только 1.4 + 1.5,
1.3 уже в `integration/1a` с run #4). Итог `real_pilot_done spawned=2 succeeded=1 failed=1`,
но **фактически 0 историй смержено** в `integration/1a` — ни одного `story_merged` события.
`succeeded=1` — ложное (см. NEW-16).

Validated частично:
- ✅ **NEW-11 validated** — `build_check_skip_no_ruff_config` сработал на 1.4 (`ruff skipped`),
  `build_check_clean` прошёл. ruff больше не халтит config-less worktree.

Регрессии и новые баги:

- ⬜ **NEW-14 (P1) · Тип: 🐛 Баг — NEW-12 регрессия: stage5_recovery git commit без env** —
  `stage5_recovery_failed: No .pre-commit-config.yaml file` на 1.4 снова. Фикс v5 прокинул
  `PRE_COMMIT_ALLOW_NO_CONFIG=1` в env *воркера* (`worker_spawn.py`), но stage5 recovery path
  вызывает `git commit` отдельным процессом без проброшенного env. Fix — инжектить флаг во
  все `git commit` вызовы stage5_recovery (или глобально в subprocess env пути runner'а).
- ⬜ **NEW-15 (P1) · Тип: 🐛 Баг — code_review verdict=error блокирует merge gate** —
  `code_review_dispatched review_jsonl= verdict=error` на 1.4: review_jsonl пустой → verdict
  становится `error`, оба merge-gate stage (`spec`/`quality`) дают `verdict=error`. Аналог
  NEW-13, но для **code_review**, не security_review — v5-фикс error→retry покрыл только
  security_review. Под-баг (b): `code_review_runner_log_fallback fallback_verdict=approve`
  отработал, но `merge_gate_quality_stage_done` всё равно `verdict=error` — fallback-verdict
  не применяется к итоговому merge-gate verdict.
- ⬜ **NEW-16 (P1) · Тип: 🐛 Баг — ложный `succeeded` для no-op story** — 1.5 worker написал
  код (`.pre-commit-config.yaml`, `.gitleaks.toml`, `ci.yml`, ADR 0005) но **не закоммитил**
  (uncommitted changes висят в `wt-1.5`), не дошёл до merge gate, нет ни одного merge_gate /
  `story_merged` события — однако засчитан `succeeded=1`. `succeeded` обязан требовать
  `story_merged` (или хотя бы непустой коммит на feature-ветке), иначе метрика врёт.
- ⬜ **NEW-17 (P2) · Тип: 🐛 Баг — 1.5 worker завершился молча без коммита** — для 1.5 нет
  `pilot_mark_done`, нет `stage5_recovery`, нет `merge_gate` — только `cost_tracking_unavailable`.
  Worker написал файлы и вышел, не закоммитив. Вероятно тот же pre-commit-config блок
  (NEW-14), но для 1.5 даже `stage5_recovery_failed` не залогирован — silent failure.
- ⬜ **NEW-18 (P2) · Тип: 🐛 Баг — `bmad_format.unknown_status` ×4** — парсер статусов историй
  печатает голый `bmad_format.unknown_status` в stdout (не структурный лог) 4 раза за прогон
  — не распознаёт Status-поле части историй. Нужен structured warning + диагностика какие
  именно статусы не парсятся.
- ✅ **NEW-19 (P1) · Тип: ✨ Улучшение — replay-from-worktree режим** — DONE
  `integration/pilot_findings_closure_v6` S1: CLI `bmad-orchestrator replay --worktree <path>
  --story <id> --integration <branch>` прогоняет хвост pipeline (WORKER_COMPLETED →
  stage5→build-check→merge-gate→reconcile→merge) без `spawn_worker`; `--auto-commit-dev`
  синтезирует dev-коммит из dirty worktree; EventType #38 `REPLAY_MODE_STARTED`,
  лог `replay_mode_active`. Модуль `runtime/replay.py` + `agent.run.run_replay`;
  subscriber-wiring вынесен в `_wire_pipeline_subscribers` (общий с `_run_real_pilot_body`).
  +5 tests (`tests/test_new19_replay_worktree.py`). Источник — feedback пользователя
  (replay жжёт токены): dev-фаза не нужна для валидации фиксов merge-gate/stage5/metrics.

**Recommended next initiative:** `pilot_findings_closure_v6` — 6 items (NEW-14..19), из них
4×P1. Порядок: NEW-19 (replay-режим, разблокирует дешёвую валидацию) → NEW-14 (pre-commit
env в recovery path, NEW-12 переоткрыт) + NEW-15 (code_review error) + NEW-16 (ложный
succeeded) → NEW-17/18. **Spec:** `spec/spec_pilot_findings_closure_v6.md` (v1.0, READY для
`/auto-loop-spec-long` bootstrap — 6 items, ~3 сессии, +22 tests). Детали run #5 — memory
[[project_pilot_antares_1a_run5_2026-05-19]].

**Правила v6 (против повторения NEW-12 регрессии):** (1) каждый фикс → unit-тест,
воспроизводящий именно тот code path где баг (v5-тест проверял env воркера, а баг был в
recovery path → проехал); (2) pilot replay — один раз в конце, на одной истории (1.4), не
после каждого фикса.

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

**Last updated:** 2026-05-19 (v13.12 — pilot run #5 (Antares 1a replay after v5 merge `dbeb936`): NEW-11 validated, но NEW-12 РЕГРЕССИРОВАЛ; результат `succeeded=1 failed=1`, фактически 0 stories merged; new backlog NEW-14..19 (4×P1, +NEW-19 replay-from-worktree режим), spec `spec_pilot_findings_closure_v6.md` v1.0 READY → next initiative pilot_findings_closure_v6; v13.11 — NEW-11/12/13 closed in integration/pilot_findings_closure_v5 (S1..S2): ruff graceful-skip + pre-commit no-config env flag + security_review error→retry/escalate-story (not abort); EventType #37 SECURITY_REVIEW_ERROR; tests 2061→2078, mypy/ruff clean; v13.10 — pilot run #4 findings: FIRST integration merge success (`integration/1a`, story 1.3), NEW-7 validated, new backlog NEW-11/12/13; v4 merged `48febc0`; v13.9 — NEW-9/NEW-10/NEW-5-recheck closed in integration/pilot_findings_closure_v4 (S1..S2), tests 2038→2061, mypy/ruff clean; v13.8 — NEW-1-completion/NEW-3-completion/NEW-5/NEW-6/NEW-7/NEW-8 closed in integration/pilot_findings_closure_v3 (S1..S4), EventType #36 INTEGRATION_MERGE_SKIPPED, tests 2009→2038; v13.7 — +validation replay findings NEW-1-completion/NEW-3-completion/NEW-5..8 в §5 backlog; v13.6 — pilot findings NEW-1..NEW-4 closed in integration/pilot_findings_closure_v2; v13.5 — Phase 3 ✅ DONE, pilot findings P1/P2/P3 + R1/R2 closed in integration/pilot_findings_closure)
**Status:** v13.10 — **Phase 4 (Deploy) in progress.** `pilot_findings_closure_v4` merged в main `48febc0` (S1..S2: NEW-9 verdict source-of-truth + NEW-10 observability + NEW-5 recheck, tests 2038→2061, mypy/ruff clean). **FIRST end-to-end production success** — pilot run #4 (Antares 1a, `BMAD_AUTO_SPLIT=1`) создал ветку `integration/1a`, story 1.3 смержена автономно через verdict→reconcile→merge (NEW-7 pipeline VALIDATED). `spawned=3 succeeded=2 failed=1`. Stories 1.4/1.5 не дошли до integration — new backlog **NEW-11/12/13** (P2, fixable: ruff halt · pre-commit config missing · security_review error→circuit breaker) → next initiative `pilot_findings_closure_v5`.
**Owner:** user + Claude orchestrator
