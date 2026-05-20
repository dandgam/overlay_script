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

- ✅ **NEW-14 (P1) · Тип: 🐛 Баг — NEW-12 регрессия: stage5_recovery git commit без env** —
  `stage5_recovery_failed: No .pre-commit-config.yaml file` на 1.4 снова. Фикс v5 прокинул
  `PRE_COMMIT_ALLOW_NO_CONFIG=1` в env *воркера* (`worker_spawn.py`), но stage5 recovery path
  вызывает `git commit` отдельным процессом без проброшенного env. **DONE** (v6 S2): общий
  helper `_git_commit_env()` в `runtime/git_env.py` (константа `GIT_COMMIT_ENV_INJECTED`
  переиспользуется и `worker_spawn.WORKER_ENV_INJECTED`); `env=_git_commit_env()` проброшен
  в `git commit` subprocess обоих recovery-путей — `stage5_completeness.py` (Patch S) и
  `commit_recovery.py` (Patch R). +4 tests.
- ✅ **NEW-15 (P1) · Тип: 🐛 Баг — code_review verdict=error блокирует merge gate** —
  `code_review_dispatched review_jsonl= verdict=error` на 1.4: review_jsonl пустой → verdict
  становится `error`, оба merge-gate stage (`spec`/`quality`) дают `verdict=error`. Аналог
  NEW-13, но для **code_review**. **DONE** (v6 S2): `code_review_subscriber` ретраит
  two-stage gate `CODE_REVIEW_ERROR_RETRY_MAX` раз (default 1), эмитит `CODE_REVIEW_ERROR`
  (EventType #39) per attempt, при исчерпании — одиночная HUMAN_QUERY (`verdict=code_review_error`),
  НЕ `CODE_REVIEW_VERDICT(error)` → не кормит circuit breaker;
  `SupervisorEngine._is_security_review_error` распознаёт и code_review-маркеры. Под-баг (b):
  fallback-verdict из `code_review_runner_log_fallback` теперь доезжает до итогового verdict
  даже при `quality stage = error`. +5 tests.
- ✅ **NEW-16 (P1) · Тип: 🐛 Баг — ложный `succeeded` для no-op story** — DONE
  (pilot_findings_closure_v6 S3). `runtime/pilot_outcomes.partition_pilot_outcomes` пересчитывает
  worker-completed список по реальным merge-событиям: `INTEGRATION_MERGE_COMPLETED` (новый
  EventType #40) → `succeeded`; `INTEGRATION_MERGE_SKIPPED` reason=`no_commits` → `no_op`; иначе
  (или вовсе нет merge-события) → `failed`. `real_pilot_done` репортит честный `succeeded` +
  `worker_succeeded`/`no_op`. `merge_to_integration_subscriber` эмитит `INTEGRATION_MERGE_COMPLETED`.
- ✅ **NEW-17 (P2) · Тип: 🐛 Баг — 1.5 worker завершился молча без коммита** — DONE
  (pilot_findings_closure_v6 S3). На zero-commit silent-failure пути `_tail_and_emit_completion`
  проверяет dirty worktree (`git status --porcelain`) + отсутствие stage5-маркера через
  `worker_silent_failure.decide_uncommitted_exit`; при «файлы написаны, не закоммичены, stage5 не
  было» эмитит loud `WORKER_EXIT_UNCOMMITTED` (новый EventType #41) — отличает «worker ничего не
  сделал» от «worker сделал работу и потерял её».
- ✅ **NEW-18 (P2) · Тип: 🐛 Баг — `bmad_format.unknown_status` ×4** — DONE
  (pilot_findings_closure_v6 S3). `_canonical_status` эмитит structured warning
  `bmad_format_unknown_status` с полями `story_id`/`raw_status`/`layout`; матчинг статусов
  case-insensitive (`Done`/`DONE` → `done`); `KNOWN_STATUSES` расширен `drafted`/`approved`.
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

### Backlog — pilot run #6 findings (Antares 1.5 после v6 merge `55825cb`, 2026-05-19)

**Валидация v6 + полный pilot story 1.5.** Replay 1.4 (`story_merged` ✅) + replay 1.5
(`--auto-commit-dev`, упал на pre-commit detect-secrets — 1.5 недоделана воркером) → полный
pilot 1.5. Результат `real_pilot_done failed=1 succeeded=0 worker_succeeded=1 no_op=0`.

Validated вживую ✅:

- **NEW-14** — `stage5_recovery_commit commit_sha=6a2f611` прошёл (в run #5 — `stage5_recovery_failed`).
- **NEW-15** — `code_review_runner_log_fallback` + `code_review_quality_error_fallback_applied
  fallback_verdict=approve` → `code_review_dispatched verdict=approve`. code_review error не
  валит merge-gate, fallback доезжает до итогового verdict.
- **NEW-16** — `succeeded=0 worker_succeeded=1` — метрика честная: worker отработал, история
  НЕ смержена → не засчитана `succeeded` (в run #5 соврала бы `succeeded=1`).
- **NEW-19** — replay-режим: 1.4 прогнан за ~3 мин без worker-dev, `story_merged` в `integration/1a`.

Почему 1.5 НЕ смержена + новые баги:

- ✅ **NEW-20 (P1) · Тип: 🐛 Баг — security_review verdict=error без fallback (асимметрия с
  code_review)** — DONE 2026-05-19 (`integration/pilot_findings_closure_v7` S2). Симметрично
  NEW-15: `security_review_subscriber` после исчерпания retry на `verdict=error` вызывает
  shared Stage-6 runner-log fallback (`parse_security_runner_fallback` → переиспользует
  `parse_runner_review_log` — общий log-reading core code_review + security_review). Holistic
  verdict восстановлен (PASS→approve / NEEDS-FIX→merge_with_fixes / BLOCKED→block): approve/
  merge_with_fixes → `SECURITY_REVIEW_PASSED` без мутации payload в reject, merge не блокируется;
  block → halt как раньше; нет сигнала вообще → одиночная HUMAN_QUERY escalate-story. +5 tests
  (`test_new20_security_review_fallback.py`). NB: после NEW-21 (`isolated_home=True` и для
  security-спавна) security review runner реально отрабатывает — этот fallback стал страховкой.
- ✅ **NEW-21 (P1, КОРЕНЬ) · Тип: 🐛 Баг — review runner систематически возвращает verdict=error**
  — DONE 2026-05-19 (`integration/pilot_findings_closure_v7` S1). **Root cause найден по
  on-disk артефакту** `1a__gate_spec_1.5/wt-1.5.events.jsonl`: единственный claude_event =
  `{type:result, subtype:error_during_execution, is_error:true, num_turns:0,
  errors:["EROFS: read-only file system, open '/home/server/.claude.json'"]}`. Reviewer
  `claude -p` **падал на старте** — sandbox не давал писать `~/.claude.json`, claude не делал
  ни одного turn → в потоке нет verdict → verdict=error. Не H1 (path) и не H2 (формат) — H3
  (read-only HOME). Dev workers выживали, т.к. спавнятся с `isolated_home=True` (writable HOME
  snapshot); review/security/merge-gate workers спавнились с дефолтом `isolated_home=False`.
  Fix — `isolated_home=True` на всех 4 review-спавнах (`_spawn_code_review_worker`,
  `_spawn_security_review_worker`, `_spawn_merge_gate_spec_worker`,
  `_spawn_merge_gate_quality_worker`). Observability — `jsonl_path` протянут через
  `_MergeGateStageResult` (`review_jsonl=` в логах был захардкожен `""`). +5 tests
  (`test_new21_review_jsonl.py`). NEW-13/15/20 теперь страховка, а не основной путь.
- ✅ **NEW-22 (P2) · Тип: 🐛 Баг — `bmad_format_unknown_status` голый stdout** — DONE
  2026-05-19 (`integration/pilot_findings_closure_v7` S2). **Гипотеза «4 вызова из top-level
  scan, отдельно от `_canonical_status`» — ОПРОВЕРГНУТА.** Callsite ровно один —
  `bmad_format._canonical_status:246`, и он уже structured через `extra={}`. Реальная причина:
  `bmad_format` использует stdlib `logging` (не structlog) — дефолтный форматтер НЕ рендерит
  `extra=` атрибуты записи, поэтому в stdout печатался голый ключ `bmad_format_unknown_status`
  («4×» = 4 истории с нераспознанным статусом за прогон, один и тот же callsite). Fix — поля
  `story_id`/`raw_status`/`token`/`layout` интерполируются в саму message-строку (`%s`-args);
  `extra=` сохранён для structured-log аггрегаторов. +2 tests (`test_new22_unknown_status_log.py`).

**Recommended next initiative:** `pilot_findings_closure_v7` — 3 items (NEW-20/21/22), 2×P1.
**NEW-21 — корневой:** закрыть первым, он объясняет всю серию NEW-13/15/20 (review runner не
пишет jsonl). После NEW-21 ревью даёт реальные verdict'ы, fallback'и становятся
страховкой. Детали — memory [[project_pilot_antares_1.5_run6_2026-05-19]].

---

### Backlog — pilot run #7 findings (replay 1.5 после v7 merge `69f02c4`, 2026-05-20)

Replay 1.5 после v7: NEW-20/21 validated вживую (все review verdict'ы → `approve` через
fallback), но merge упал на новом баге.

- ✅ **NEW-23 (P1) · Тип: 🐛 Баг — `merge --ff-only` падает на dirty integration worktree** —
  DONE 2026-05-20 (прямой фикс, hotfix вне auto-loop). `git merge feature/1.5 --ff-only`
  аварийно завершался: `error: Your local changes to sprint-status.yaml would be overwritten
  by merge`. Причина — оркестратор пишет `sprint-status.yaml` в integration-worktree через
  `mark_sprint_status_done`, оставляет незакоммиченным; tracked-residue блокирует ff-merge.
  Fix — `_ff_merge_to_integration` (`agent/run.py`) перед merge откатывает tracked
  uncommitted-residue (`repo.index.diff(None)` → `git checkout -- <files>`), логирует
  `integration_worktree_residue_discarded`. Untracked файлы не трогаются (merge их не
  блокирует). Не `reset --hard` / не history rewrite — в рамках hard rules.
  +2 tests (`test_new23_integration_dirty_residue.py`).

- ✅ **NEW-24 (P1, КОРЕНЬ) · Тип: 🐛 Баг — review-спавн падает `ConnectionRefused`** —
  DONE 2026-05-20 (прямой фикс, hotfix вне auto-loop). **Истинный корень всей серии
  NEW-13/15/20/21.** Review jsonl всех прогонов (#4..#7) содержал
  `API Error: Unable to connect to API (ConnectionRefused)` + `worker_completed exit_code=1
  status=failure` → `verdict=error` на каждом stage, маскировалось runner-log fallback'ом.
  Причина — 4 review-спавна (`_spawn_code_review_worker`, `_spawn_security_review_worker`,
  `_spawn_merge_gate_spec_worker`, `_spawn_merge_gate_quality_worker` в `agent/run.py`)
  передавали `sandbox_network="none"` (`--unshare-net`). Review-worker запускает inner
  `claude -p` reviewer, который делает LLM-вызовы к Anthropic API — ревью это LLM-операция,
  ей нужен egress, как dev-worker'у (`sandbox_network="full"`). Fix — все 4 спавна →
  `sandbox_network="full"`. NEW-13/15/20/21 (обработка `verdict=error`) после этого
  становятся настоящей страховкой, а не основным путём. +4 tests
  (`test_new24_review_network.py`).

- ✅ **NEW-25 (P1, КОРЕНЬ #2) · Тип: 🐛 Баг — review-спавн: `Unknown command: /bmad-code-review`**
  — DONE 2026-05-20 (прямой фикс). После NEW-24 (сеть появилась) review-спавн на
  spec-stage больше не падает `ConnectionRefused`, но `claude -p` отвечает
  `Unknown command: /bmad-code-review` → review не выполняется → `verdict=error` (спасает
  fallback). Причина: skill `bmad-code-review` review-спавну недоступен ниоткуда:
  (1) `<target>/.claude/skills/` gitignored в target-проекте → `git worktree add` его не
  несёт, worktree `.claude/skills/` пуст; (2) `isolated_home` overlay копирует `~/.claude/`,
  но в `~/.claude/skills/` есть только `bmad-security-review`, нет `bmad-code-review`. Skill
  реально есть в Virgil embedded `skills/upstream/bmad-code-review` (+ в
  `Antares/.claude/skills/`). Все 4 review-спавна используют `/bmad-code-review`
  (`CODE_REVIEW_SKILL_INVOCATION`/`MERGE_GATE_SPEC_SKILL`/`MERGE_GATE_QUALITY_SKILL`).
  **Fix-направление:** перед review-спавном инжектить embedded
  `skills/upstream/bmad-code-review` в `<worktree>/.claude/skills/` (project-level, claude
  резолвит из CWD) ИЛИ в isolated_home overlay. Project-agnostic — нельзя полагаться на
  `~/.claude/skills/` target-машины. **Применённый fix:** helper
  `_ensure_review_skill_in_worktree` (`agent/run.py`) копирует embedded
  `skills/upstream/bmad-code-review` в `<worktree>/.claude/skills/bmad-code-review` перед
  3 review-спавнами (code-review/merge-gate spec+quality); идемпотентно; лог
  `review_skill_injected`. +3 tests (`test_new25_review_skill_injection.py`).

- ✅ **NEW-26 (P1, КОРЕНЬ #3) · Тип: 🐛 Баг — review-спавн резолвил интерактивный skill
  target-проекта** — DONE 2026-05-20 (commit `60a6eaf`), validated replay 1.5. После
  NEW-24+NEW-25 review-спавн отрабатывал по-настоящему (сеть ✅, skill ✅) — но
  `verdict=error` всё равно. **Истинная причина** (replay показал): `claude -p
  /bmad-code-review` резолвил `/bmad-code-review` **из target-проекта**
  (`Antares/.claude/skills/`), а не из Virgil — slash-команда резолвится подъёмом по
  дереву каталогов, а worktree лежит ВНУТРИ target (`<target>/.worktrees/wt-*`), инжект
  skill'а в worktree (NEW-25) collision не выигрывает. Этот target-skill написан под
  **интерактивный** режим — step-файлы HALT'ят на numbered-choice чекпоинтах, machine-
  readable verdict не выдаётся → headless `exit 0` без `verdict:` → `verdict=error`.
  **Применённый fix:** review-спавн больше не использует slash-команду — передаёт
  self-contained headless directive-prompt (`CODE_REVIEW_DIRECTIVE`, `agent/run.py`)
  напрямую в `claude -p`, как dev-worker через `DEFAULT_SKILL_INVOCATION`. Никакого
  skill-резолва, инжекта, collision, HALT'ов. Директива требует финальную строку
  `VERDICT: approve|request_changes|reject` (матчит `_VERDICT_LINE_RE`). Удалено:
  `_ensure_review_skill_in_worktree`, `MERGE_GATE_*_SKILL`, `CODE_REVIEW_SKILL_INVOCATION`.
  Validated: replay 1.5 → spec-stage `VERDICT: approve`, quality-stage
  `VERDICT: request_changes`, merged `request_changes` — впервые pipeline дал реальный
  verdict end-to-end, БЕЗ runner-log fallback. +4 tests (`test_new26_review_headless_verdict.py`),
  tests 2124. ⚠️ **Закрыт только code-review путь.** security-review-спавн ещё
  использует slash `/bmad-security-review --auto` (тот же латентный баг) →
  см. NEW-27.

- ✅ **NEW-27 (P1, СТРУКТУРНОЕ) · Тип: 🏗️ Изоляция — воркеры видели skill'ы target-проекта**
  — DONE 2026-05-20 (S1-S4), validated replay 1.5. NEW-26 выявил структурную причину:
  worktree-копии создавались ВНУТРИ target-проекта (`<target>/.worktrees/wt-*`), поэтому
  `claude -p` любого воркера, поднимаясь по дереву каталогов в поисках slash-команд,
  доходил до `<target>/.claude/skills/` и резолвил **чужие** skill'ы. Spec —
  `spec/spec_worker_skill_isolation.md`. Закрыто 4 сессиями:
  - **S1** (`0f60773`) — `worktree_root()` возвращает корень ВНЕ дерева проекта:
    `/var/tmp/virgil-worktrees/<target>` (нет `.claude`-предка), env-override
    `ORCHESTRATOR_WORKTREE_ROOT`. 3 хардкод-сайта переведены на helper. Мёртвый
    config-флаг `worktree_layout` удалён. +4 tests.
  - **S2** (`5233c4d`) — security-review-спавн с slash `/bmad-security-review --auto` на
    `SECURITY_REVIEW_DIRECTIVE` (headless 4-hunter brief, `VERDICT: APPROVE|MERGE WITH
    FIXES|BLOCK`). +4 tests.
  - **S3** (`0732fd2`) — `_create_isolated_home` стрипает `~/.claude/skills/` из overlay
    (пустой placeholder) — чужой user-level skill оператора больше не виден воркеру.
    +2 tests.
  - **S4** — replay 1.5 с worktree в `/var/tmp/virgil-worktrees/Antares/wt-1.5`:
    review дал `VERDICT: request_changes` (реальный, не fallback), **0** упоминаний
    `bmad-code-review`/`Antares/.claude`/`Unknown command` в review jsonl — ноль
    skill-резолва. Tests 2124 → 2134.

- ✅ **NEW-28 (P0, СТРУКТУРНОЕ) · Тип: 🐛 Баг — DAG слеп к prose-зависимостям реальных
  BMad-историй** — DONE 2026-05-20 (commit `3ce473e`), выявлен pilot-прогоном wave 2a.
  DAG-планировщик выбрал `parallel=['1.3','1.4']`, хотя 1.4 жёстко зависит от 1.3
  (потребляет `core/config.py` + `core/db.py`). **Корень:** `parse_story_md` понимал
  только канон `- **depends_on:** []`; реальные BMad-истории объявляют зависимости
  прозой — `**Зависит от:** 1.1, 1.3 (...)`, `**Блокирует:** 1.4 (...)` → `depends_on`
  пустой → DAG без рёбер → всё параллелится. **Fix:** `_extract_prose_story_ids`
  (`_common.py`) парсит `**Зависит от:**`/`**Depends on:**` → `depends_on` и
  `**Блокирует:**`/`**Blocks:**` → `blocks`; скобочная проза стрипается (версии не
  путаются с id); канон-поле имеет приоритет. `build_graph` строит рёбра из обоих полей,
  id резолвятся через `normalize_story_id` (prose `2.1` → node `2-1-authentik-…`),
  нерезолвимые токены отбрасываются. Проверено на Antares 1.3/1.4/1.5 → DAG строит
  цепочку `1.3→1.4→1.5`. +9 tests, tests 2143.

- ⬜ **NEW-29 (P1, СТРУКТУРНОЕ) · Тип: 🐛 Баг — DAG не видит file-conflict реальных
  историй** — OPEN 2026-05-20, родственник NEW-28. `parse_story_md` не извлекает
  `touches_files`/`touches_shared` из реальных BMad-историй (файлы упомянуты в прозе
  Dev Notes / Tasks типа `src/antares/main.py`, не в канон-поле). → две независимые
  (без dep-ребра) истории, правящие один файл, планировщик пускает параллельно →
  конфликт на merge. NEW-28 чинит **порядок**; NEW-29 — **file-mutex**. Страховка пока:
  ff-merge падает громко (не тихая порча), `in_flight_touches` пуст. **Fix-направление:**
  консервативный regex по путям в Tasks/Dev-Notes ИЛИ извлекать список из git-diff
  worker'а пост-фактум для следующих батчей. P1 — не блокирует пилот (порядок важнее).

- ✅ **NEW-30 (P1, СТРУКТУРНОЕ) · Тип: 🐛 Баг — auto-split слеп к размеру реальных
  историй** — DONE 2026-05-20 (commit `b437f4b`), родственник NEW-28. `evaluate_split`
  читал 5 метрик размера (`estimated_minutes/tokens`, `touches_files`, `layers`,
  `ac_count`) из машинных полей, которых в реальных BMad-историях нет → все метрики 0 →
  всегда `keep`, история любого размера не дробилась (даже `BMAD_AUTO_SPLIT=1`).
  **Fix:** `parse_story_md` извлекает размер из markdown-структуры — `ac_count` (число
  distinct `AC<n>`-заголовков) и `task_count` (число чекбоксов Tasks/Subtasks); новое
  правило `tasks>=40` (`SPLIT_TASK_THRESHOLD`); `count_acceptance_criteria`
  приоритезирует explicit `ac_count`. Калибровка на Antares: 1.3 (82 подзадачи)→split,
  1.4 (74)→split, 3-2-zfs (8 AC)→split, 10-1 (21)→keep. +5 tests, tests 2150.

- ✅ **NEW-29/31/32** — DONE 2026-05-20 (субагент). NEW-29 `36e77c6` (touches_files из
  бэктик-спанов прозы), NEW-31 `883e288` (rebase feature на integration перед ff-merge,
  на конфликт — `rebase --abort` + эскалация), NEW-32 `9b66f0c` (post-merge build-check
  на integration, EventType #42 `INTEGRATION_TEST_FAILED`, defensive — exception не
  крашит мерж). Tests 2150 → 2166.

- ✅ **NEW-33 (P1, СТРУКТУРНОЕ) · Тип: 🐛 Баг — watchdog слеп к застрявшему воркеру** —
  CLOSED 2026-05-20 (4 subtasks). NEW-33.1 `ad0b578` — `liveness.is_stalled` принимает
  опциональный `start_time` → пустой JSONL после threshold = stalled (backward-compatible).
  NEW-33.2 `8e83bb2` + `36b80eb` — новый `runtime/stuck_watchdog.py` с pure
  `evaluate_stuck` и async-iterator-обёрткой `tail_with_stuck_watchdog` над
  `tail_jsonl_events`; кумулятивный stuck = нет роста коммитов + (события устарели
  > threshold OR событий нет и elapsed > threshold); порог `BMAD_STUCK_TIMEOUT_SECONDS`
  (default 1800s); на stuck — emit `HUMAN_QUERY` + `WORKER_STUCK_TIMEOUT` + синтез
  терминального `worker_completed(status=stuck_timeout)`; EventType #43.
  NEW-33.3 `dded04d` — `WATCHED_EVENT_TYPES += WORKER_STUCK_TIMEOUT`,
  `load_supervisor_engine(judge_factory=...)` env-gated hook (`BMAD_SUPERVISOR_LLM`,
  real Sonnet deferred to spec_supervisor_llm_loop M4 — wiring готов, fail-safe
  fallback на StubJudge); добавлено hard rule worker-stuck-timeout → escalate_human.
  NEW-33.4 `60de067` — `BMAD_CURRENT_WAVE` добавлен в `ALLOWED_WORKER_ENV`
  (worker_spawn) И в `_SANDBOX_DEFAULT_ENV_ALLOWLIST` (bwrap `--setenv`) — pilot 2b
  root cause: переменная не проходила через два слоя allowlist'ов, и
  `worker_events.current_wave_dir()` писал `runs/default/` вместо `runs/2b/`.
  Tests 2166→2182 (+16), ruff clean.

- ⬜ **NEW-33.4 v2 · Тип: 🐛 Баг — wave env не выставлялся в os.environ оркестратора** —
  OPEN→CLOSED 2026-05-20, найден pilot 2c. NEW-33.4 v1 добавил `BMAD_CURRENT_WAVE` в
  `ALLOWED_WORKER_ENV` + `_SANDBOX_DEFAULT_ENV_ALLOWLIST` (passthrough из env родителя),
  но **сам родитель-оркестратор переменную не ставил** — `--wave` был только CLI-аргументом.
  Результат: bwrap `--clearenv` чистил всё, и так как BMAD_CURRENT_WAVE не было в env
  оркестратора, allowlist не пропускал её внутрь. events.jsonl уходили в `runs/default/`.
  Фикс: `os.environ["BMAD_CURRENT_WAVE"] = wave` в начале `_run_real_pilot_body`
  (review/security/gate spawn helpers уже сами save+restore вокруг своих per-stage суффиксов).
  +1 unit-тест на присутствие строки в исходнике. Tests 2182→2183.

- ✅ **NEW-34 (P1) · Тип: 🐛 Баг — ff-merge diverging branch не уходит в rebase recovery** —
  CLOSED `b93a0a3`, pilot 2c. Story 3-2-zfs прошла весь pipeline (dev → stage5 →
  build_check → gate_spec approve → gate_quality approve), но финальный
  `git merge feature/3-2-zfs --ff-only --signoff` упал с `exit 128 / Not possible to
  fast-forward, aborting` потому что feature-branch создан в 2b (HEAD=1d86f83 на
  старой базе), а integration/2c свежесоздана с другого коммита — расходящиеся базы.
  Root cause: в `_ff_merge_to_integration` set `existing` захватывался ДО создания
  integration-ветки (строка 4328); условие rebase проверяло `integration_branch in existing`
  → False для свежесозданной ветки → rebase skipped → ff-merge exit-128.
  Fix: убрано `and integration_branch in existing` из guard rebase (NEW-31). Rebase
  теперь всегда выполняется когда `worktree` указывает на реальный git worktree.
  Конфликт rebase → `rebase --abort` + raise → caller's except → `HUMAN_QUERY`. +2 теста.

- ✅ **NEW-35 (P1) · Тип: 🐛 Баг — reused worktree base_sha → false silent_failure** —
  CLOSED `ff973a9`, pilot 2d. Оба воркера (1.4 + 3-2-zfs) ушли в `worker_silent_failure`
  хотя в 3-2-zfs worktree был commit `d52a32d` от 2c. Корень: `_ensure_git_worktree` для
  reused worktree возвращал `_resolve_worktree_head(worktree)` = HEAD feature-ветки →
  `base_sha == HEAD` → `git rev-list base_sha..HEAD` = 0 → silent_failure false-positive.
  Fix: новый helper `_resolve_worktree_reuse_base_sha` берёт merge-base feature vs target
  HEAD; fallback на HEAD при ошибке. +2 теста через реальный git worktree. Tests 2185→2187.

- ✅ **NEW-36 (P1) · Тип: 🏗 Архитектура — брутальный subprocess_timeout убивает воркера пишущего файлы** —
  CLOSED `69e0c1f`, pilot 2f. Worker 8-1 (story `8-1-landing-page-antares-ds`) 30 минут
  реально писал код (34 файла), но hits `BMAD_WORKER_TIMEOUT_SEC=1800` → SIGKILL →
  34 файла uncommitted потеряны. Три изменения:

  **1. Новый liveness-сигнал в `stuck_watchdog.py`:** `evaluate_stuck` получил два новых
  параметра `last_dirty_count` / `current_dirty_count`. Если dirty count растёт →
  reason=`worktree_growing`, stuck=False. Три независимых сигнала теперь сбрасывают таймер:
  `events_fresh` (JSONL), `commits_growing` (git), `worktree_growing` (файлы в worktree).
  `StuckCheckResult` получил поле `dirty_count`.

  **2. `tail_with_stuck_watchdog`:** новый optional param `dirty_counter: DirtyCounter | None`.
  На каждом тике вызывает `await dirty_counter()` (если передан), сравнивает с предыдущим —
  при росте сбрасывает start_time (как при новом коммите). `_tail_and_emit_completion`
  в `agent/run.py` передаёт `_dirty_counter_for_handle` — `git -C <worktree> status
  --porcelain` с asyncio.create_subprocess_exec.

  **3. Архитектурный сдвиг `worker_spawn.py`:** `_worker_timeout_sec()` →
  `_worker_hard_timeout_sec()`. Default raised 1800 → **14400 s (4 h)** via
  `BMAD_WORKER_HARD_TIMEOUT_SEC` env (old `BMAD_WORKER_TIMEOUT_SEC` — backward compat).
  Первичное решение «жив/застрял» — stuck_watchdog (soft 30 мин с тремя сигналами);
  hard ceiling — только last resort kill для runaway. Перед SIGKILL: best-effort
  `git add -A && git commit` (функция `_auto_stage_worktree`); emits новый
  `WORKER_AUTO_STAGE_RECOVERY` EventType с SHA (None при провале). Работа сохраняется
  даже если hard ceiling всё же сработал.

  +19 тестов (`test_new36_dirty_count_watchdog.py` + inline в `test_stuck_watchdog.py`
  + обновлён `test_canonical_patches_p1.py`). EventType #44. Tests 2187→2206.

- ✅ **M4 — AnthropicJudge (real Sonnet supervisor judge)** (2026-05-20) — заменяет StubJudge
  за флагом `BMAD_SUPERVISOR_LLM=anthropic ANTHROPIC_API_KEY=<key>`.
  - `supervisor/judges/` пакет: `AnthropicJudge` (Sonnet, async, prompt caching),
    `__init__.py` с multi-LLM extension guide (Gemini/OpenAI/Yandex/Ollama — scope documented).
  - `JudgeConfig.provider` field в `policy.py` для будущей маршрутизации.
  - Prompt caching: `cache_control={"type": "ephemeral"}` на system block → 5-мин TTL.
  - JSON parsing с 1 repair retry; все failure modes → `JudgeError` → engine fail-safe escalate.
  - `_supervisor_judge_factory` в `agent/run.py` разблокирован: возвращает реальный
    `AnthropicJudge` при наличии `ANTHROPIC_API_KEY`; StubJudge при отсутствии ключа.
  - +25 unit-тестов (`test_supervisor_anthropic_judge.py`) + 2 subscriber wiring тесты. Tests 2206→2231.

- ✅ **M4-followup: ClaudePJudge — subscription path live** (2026-05-20) — вторая реализация
  `LLMJudgeProtocol` для production-среды без `ANTHROPIC_API_KEY` (Claude Code subscription).
  - `supervisor/judges/claude_p_judge.py` — `ClaudePJudge`: asyncio subprocess `claude -p --model`,
    timeout 30 s (vs 5 s у SDK), JSON recovery: direct → fence strip → `{}`-regex → repair retry.
  - `JudgeConfig.provider` default изменён `"anthropic"` → `"claude_p"` (primary production path).
  - `_supervisor_judge_factory` расширен: `BMAD_SUPERVISOR_LLM=claude-p/subscription/cli` →
    `ClaudePJudge`; `BMAD_SUPERVISOR_LLM=1` — auto-pick: CLI есть → `ClaudePJudge`, иначе
    API key есть → `AnthropicJudge`, иначе `StubJudge`; `anthropic` без ключа → auto-fallback
    на `ClaudePJudge` если CLI доступен.
  - `supervisor/judges/__init__.py` обновлён: 2 реализации (`AnthropicJudge` + `ClaudePJudge`)
    + расширенный extension guide с примерами обеих.
  - +20 тестов (`test_supervisor_claude_p_judge.py` + 3 wiring в subscriber). Tests 2231→2251.

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

**Last updated:** 2026-05-19 (v13.13 — pilot run #6 (Antares 1.5 после v6 merge `55825cb`): NEW-14/15/16/19 validated вживую; 1.5 НЕ смержена — security_review verdict=error без fallback; new backlog NEW-20/21/22, NEW-21 корневой (review runner не пишет jsonl) → next initiative pilot_findings_closure_v7; v13.12 — pilot run #5 (Antares 1a replay after v5 merge `dbeb936`): NEW-11 validated, но NEW-12 РЕГРЕССИРОВАЛ; результат `succeeded=1 failed=1`, фактически 0 stories merged; new backlog NEW-14..19 (4×P1, +NEW-19 replay-from-worktree режим), spec `spec_pilot_findings_closure_v6.md` v1.0 READY → next initiative pilot_findings_closure_v6; v13.11 — NEW-11/12/13 closed in integration/pilot_findings_closure_v5 (S1..S2): ruff graceful-skip + pre-commit no-config env flag + security_review error→retry/escalate-story (not abort); EventType #37 SECURITY_REVIEW_ERROR; tests 2061→2078, mypy/ruff clean; v13.10 — pilot run #4 findings: FIRST integration merge success (`integration/1a`, story 1.3), NEW-7 validated, new backlog NEW-11/12/13; v4 merged `48febc0`; v13.9 — NEW-9/NEW-10/NEW-5-recheck closed in integration/pilot_findings_closure_v4 (S1..S2), tests 2038→2061, mypy/ruff clean; v13.8 — NEW-1-completion/NEW-3-completion/NEW-5/NEW-6/NEW-7/NEW-8 closed in integration/pilot_findings_closure_v3 (S1..S4), EventType #36 INTEGRATION_MERGE_SKIPPED, tests 2009→2038; v13.7 — +validation replay findings NEW-1-completion/NEW-3-completion/NEW-5..8 в §5 backlog; v13.6 — pilot findings NEW-1..NEW-4 closed in integration/pilot_findings_closure_v2; v13.5 — Phase 3 ✅ DONE, pilot findings P1/P2/P3 + R1/R2 closed in integration/pilot_findings_closure)
**Status:** v13.10 — **Phase 4 (Deploy) in progress.** `pilot_findings_closure_v4` merged в main `48febc0` (S1..S2: NEW-9 verdict source-of-truth + NEW-10 observability + NEW-5 recheck, tests 2038→2061, mypy/ruff clean). **FIRST end-to-end production success** — pilot run #4 (Antares 1a, `BMAD_AUTO_SPLIT=1`) создал ветку `integration/1a`, story 1.3 смержена автономно через verdict→reconcile→merge (NEW-7 pipeline VALIDATED). `spawned=3 succeeded=2 failed=1`. Stories 1.4/1.5 не дошли до integration — new backlog **NEW-11/12/13** (P2, fixable: ruff halt · pre-commit config missing · security_review error→circuit breaker) → next initiative `pilot_findings_closure_v5`.
**Owner:** user + Claude orchestrator
