# Initiative Tracker — Orchestrator Agent (BMad Phase 4 MVP)

## Metadata
- **Spec:** spec/spec_orchestrator_agent.md
- **Parent specs:** —
- **Integration branch:** integration/orchestrator_agent
- **Backup branch:** backup/orchestrator_agent-pre-2026-05-16
- **Created:** 2026-05-16
- **Bootstrap completed:** 2026-05-16 by /auto-loop-spec-long
- **Scope frozen:** 2026-05-16
- **Runtime:** loop_wrapper
- **Delay seconds:** 600
- **Auto merge:** false

## Scope Freeze

### In scope
- 8 сессий MVP scaffold per §22 Session Plan: Foundation, Tools(24), Runtime+DAG+Workers, Safety(3-layer+budget), 12 Skills, Telegram+Voice+PII, Memory+9retros, CLI+TUI+E2E mock pilot.
- Surface: backend-python для всех 8 сессий (greenfield Python project).
- Mock-mode end-to-end pilot на fake stories из tests/fixtures/.
- Anthropic SDK + Claude Agent SDK + prompt caching (`ttl="1h"` явно).
- Tool Search Tool + `defer_loading=True` на все 24 tools.

### Out of scope (explicit)
- Реальный run на Odyssey Wave 1a (требует Wave 0b complete в target проекте).
- TTS (voice output от агента) — defer to v2.
- LLM-driven story splitting (Phase 2 MVP только heuristic).
- Multi-project queue (Phase 5+).
- Self-modifying skill patches (v3 horizon).
- Letta / mem0 / LangMem / LangGraph / CrewAI (research §18.5 — Anthropic Memory Tool достаточно).
- 4-5 worktrees параллельно (после первой успешной wave).

### Deferred to follow-up initiative
- Реальный pilot run на Odyssey Wave 1a — отдельная инициатива после Wave 0b complete.
- LLM-driven splitting v1 — после baseline data Wave 1a.
- TTS provider abstraction — v2.

## Sessions

### Pending

- **id:** S4
  **title:** Safety — 3-layer hooks + budget guard + branch isolation
  **surface:** backend-python
  **spec_section:** 226-234
  **depends_on:** [S3]
  **acceptance:**
    - safety/hooks.py PreToolUse deny: rm -rf, git push --force, git commit --no-verify, git reset --hard main
    - safety/budget_guard.py — story alarm $30/halt $50, batch alarm $200/halt $300
    - safety/branch_isolation.py — worker может писать только в свою feature-ветку в worktree
    - runtime/liveness.py — liveness-before-kill
    - audit_event tool wires events в audit log JSONL
    - Tests: каждый PreToolUse rule reject'ит; budget hard-cap halts mock workflow
  **safety_gates:**
    - L1 PreToolUse hooks (full activation), L2 budget hard-cap (full), L3 branch isolation (full)
  **destructive_actions:** []
  **checkpoint:** false
  **estimated_retries_allowed:** 3

- **id:** S5
  **title:** 12 specialized internal skills (progressive disclosure)
  **surface:** backend-python
  **spec_section:** 844-993
  **depends_on:** [S4]
  **acceptance:**
    - 12 skills × src/bmad_orchestrator/agent/skills/<name>/SKILL.md с frontmatter
    - Names: dag-planner, worker-dispatcher, merge-gate, elicitation-router, retrospective-writer, intent-router, cost-watchdog, failure-analyst, reflexion-learner, wave-coordinator, proactive-improver, story-splitter
    - Three-level disclosure: metadata (~100 tokens always-loaded) / body / references
    - Skill dispatcher logic — выбор skill по event type
    - Reflexion loop в reflexion-learner (Actor / Evaluator / Self-Reflection per §19.3)
    - Tests: skill metadata loads <1.5K tokens total; full body loads только при match
  **safety_gates:**
    - L1 PreToolUse — sanity check что skill body load не пытается выйти за `agent/skills/`
  **destructive_actions:** []
  **checkpoint:** false
  **estimated_retries_allowed:** 3

- **id:** S6
  **title:** Telegram bot + voice (Whisper) + PII (Presidio) (CHECKPOINT)
  **surface:** backend-python
  **spec_section:** 404-588
  **depends_on:** [S5]
  **acceptance:**
    - bot/main.py — python-telegram-bot v22+, async, Conversation+CallbackQuery handlers
    - chat_id whitelist (TELEGRAM_ALLOWED_CHAT_IDS env var), deny остальные
    - Slash: /start /help /status /stop /model /budget /projects /cancel
    - Inline buttons для destructive ops
    - bot/voice_handler.py — .ogg → STT → text routing
    - bot/voice_providers.py — STTProvider ABC + 5 concrete (WhisperLocal default + WhisperAPI + ClaudeAudio + YandexSpeechKit + GoogleSTT) + factory
    - bot/pii_detector.py — Presidio + spaCy ru_core_news_lg + custom RU patterns (паспорт, СНИЛС, ИНН)
    - PII redaction: input check + output scrubbing; safelist для technical IDs
    - audit/telegram.jsonl append-only (original + redacted)
    - Smoke: bot stub /start, voice transcribes sample .ogg, PII tests на 20-30 RU sample-фразах PASS
  **safety_gates:**
    - L1 PII redaction (deterministic) — output scrubbing prevents PII leak в Telegram
    - L1 chat_id whitelist (deterministic deny)
  **destructive_actions:** []
  **checkpoint:** true
  **estimated_retries_allowed:** 3

- **id:** S7
  **title:** Memory + Retrospective + 9 mandatory retros + reflexion
  **surface:** backend-python
  **spec_section:** 145-189
  **depends_on:** [S6]
  **acceptance:**
    - 3 уровня learning: per-story / per-wave / per-phase
    - Anthropic Memory Tool integration (beta context-management-2025-06-27)
    - read_memory / write_memory / compress_wave_lessons tools wired
    - spawn_retro_worktree(wave) — fresh claude -p subprocess
    - 9 mandatory retros: Wave 0a/0b/1a/1b/1c/1d (×6) + Epic 1 deep + Epic 7 deep + Phase 5 final
    - Hard gates: agent физически не может перейти к next wave если retro не сделан (wave_coordinator skill)
    - proactive-improver вызывается после каждого retro с inline-button suggestions
    - Mock test: write per-story lesson → wave boundary → spawn_retro produces retrospective.md → memory roll-up
  **safety_gates:**
    - L2 hard gate (deterministic) — wave promotion blocked если retrospective.md не существует
  **destructive_actions:** []
  **checkpoint:** false
  **estimated_retries_allowed:** 3

- **id:** S8
  **title:** CLI + TUI dashboard + E2E mock pilot (CHECKPOINT)
  **surface:** backend-python
  **spec_section:** 359-402,591-642
  **depends_on:** [S7]
  **acceptance:**
    - cli/main.py — typer commands: run, status, stop, model, budget per §14.1
    - TUI dashboard cli/tui.py — rich.Live, refresh 2s, panels per §14.2
    - Russian primary localization per §14.3
    - 3 launch modes per §14.4
    - Model selection (CLI + Telegram + per-role) per §16
    - _config/orchestrator-models.yaml per-role defaults persistence
    - E2E mock pilot: bmad-orchestrator run --project mock-odyssey --wave 1a --max-parallel 2 runs to completion в mock-mode без падений
    - Все предыдущие тесты still PASS (regression matrix)
  **safety_gates:**
    - L1+L2+L3 full activation — final integration test
  **destructive_actions:** []
  **checkpoint:** true
  **estimated_retries_allowed:** 3

### Current

- **id:** S3
  **title:** Core runtime — event loop, DAG planner, worktree, worker spawn (CHECKPOINT)
  **surface:** backend-python
  **spec_section:** 15-79,106-142
  **depends_on:** [S2]
  **acceptance:**
    - runtime/event_loop.py — 13 event types из §4 + backstop polling 5min
    - runtime/ratelimit.py — per-worktree token bucket
    - DAG planner (networkx 3.x) — build_dag, find_ready_stories с shared-files mutex
    - create_worktree + spawn_worker — claude -p subprocess, Sonnet 4.6 default
    - JSONL event tail из _bmad-output/runs/<wave>/<story>.events.jsonl
    - Heartbeat / liveness (psutil)
    - Mock pilot: 3 fake stories → DAG → ready → worker spawned → JSONL streamed → completed
  **safety_gates:**
    - L3 branch isolation — worker должен быть в worktree feature-branch
  **destructive_actions:** []
  **checkpoint:** true
  **estimated_retries_allowed:** 3
  **started:** 2026-05-16 08:30 UTC
  **workflow:** .claude/skills/auto-loop-spec/workflows/backend-python.md
  **retry_count:** 0
  **worker_branches:** []

### Completed

- **id:** S1
  **title:** Foundation & SDK scaffold (pyproject.toml, system_prompt.py, beta headers, state.db, mock fixtures)
  **completed:** 2026-05-16 01:10 UTC
  **commit:** dcaadfb
  **files_changed:** 21
  **tests_passed:**
    - ruff check src tests — PASS
    - mypy --strict src — PASS (45 source files)
    - pytest — 13 passed (7 новых S1 + 6 smoke)
  **decisions_made:**
    - ANTHROPIC_BETA_HEADERS вынесен в отдельный модуль agent/betas.py (single source of truth) — Settings.beta_headers ссылается на него.
    - state/db.py: 3 таблицы + UNIQUE(session_id, scope, scope_target_id) на budget_tracker для idempotent UPSERT.
    - claim_next_event single-writer race-free под допущением одного orchestrator-процесса на DB (spec §6 уже это утверждает).
    - Ruff RUF001/RUF002/RUF003 (ambiguous Cyrillic) добавлены в ignore — project convention «Russian for prose, English for identifiers» (CLAUDE.md).
    - mypy overrides для tools/, bot/, cli/, imports/from_bad/ — scaffold-стабы из v0.7; будут переписаны в S2-S8 с полной типизацией.
    - .gitignore исправлен `state/` → `/state/` чтобы не глотать src/bmad_orchestrator/state/.
  **deferred_items:**
    - Реальный install claude-agent-sdk + pydantic-ai + python-telegram-bot + openai-whisper — в S2 (для full type-check tools layer).
    - system_prompt._load_project_context() остался placeholder — наполняется в S5 (skills metadata) и S8 (CLAUDE.md ingest).

- **id:** S2
  **title:** Tools layer (34 tools, 9 categories, defer_loading wired via tool-search-tool beta)
  **completed:** 2026-05-16 08:30 UTC
  **commit:** a1762c0
  **files_changed:** 17
  **tests_passed:**
    - ruff check src tests — PASS (all checks)
    - mypy --strict src — PASS (46 source files, agent.tools.* теперь полностью типизирован — убран из override)
    - pytest — 48 passed (7 S1 + 35 S2 + 6 smoke)
  **decisions_made:**
    - 34 tools, не 22/24: spec §17 list + 4 бонусных уже в v0.7 scaffold (set_voice_provider, schedule_reminder — §15.8; detect_wave_boundary — §6.1 hard gate; gen_wave2_prd_draft — Phase 5 final). Все референсятся за пределами §17 — kept.
    - Mock-mode default по всем destructive tools (spawn_worker.mock=True, git_merge.mock=True для non-repo, pause/resume_worker.real_signal=False по умолчанию). Реальные subprocess + signals — в S3.
    - agent/tools/_common.py — shared helpers (json_ok/error reply shape, parse_story_md, read/write_sprint_status_yaml). Path-traversal защита в memory tools (root-relative resolve).
    - Tool catalog auto-generated из @tool registry — agent/tools/__init__.ALL_TOOLS + tool_names() + tool_descriptions(); system_prompt._tool_and_skill_metadata теперь читает registry.
    - defer_loading=True per-tool decorator parameter не expose в claude-agent-sdk@0.2.82. Beta-уровень включение через ANTHROPIC_BETA_HEADERS (tool-search-tool-2025-10-19) — это API-level defer.
    - ASYNC230/ASYNC240 в ruff ignore — мы на asyncio + stdlib pathlib; нет non-blocking equivalent в stdlib, FS-вызовы в tool handlers тривиальны.
    - bot/ files остаются в mypy override с disable_error_code=union-attr/arg-type/misc/func-returns-value — pre-existing scaffold noise, fix в S6 (полная типизация python-telegram-bot Update.message narrowing).
    - splitter heuristic per §21: AC count from story markdown, minutes >= 240, layers >= 3 (touches_files prefix mapping). LLM-driven split — v1 после Wave 1a baseline.
    - update_sprint_status statuses расширены до {backlog, ready-for-dev, in-progress, review, done, superseded, blocked} — superseded для splitter, blocked для escalations.
  **deferred_items:**
    - Реальный claude -p subprocess spawn — S3 (runtime/worker_spawn.py).
    - Реальные SIGSTOP/SIGCONT на arbitrary PID — S3 (когда worker PID известен).
    - Real git_merge (не только mock-mode) — будет hit от S3 (worker worktrees — реальные git репозитории).
    - LLM-driven story splitting (Phase 2 v1) — пост Wave 1a baseline.

## Safety Gates Triggered
(none)

## Blockers / Pauses
(none)

## Decisions Log
(none)

## Journal

[2026-05-16 04:10 UTC] bootstrap: tracker + backup branch (backup/orchestrator_agent-pre-2026-05-16) + integration branch (integration/orchestrator_agent) created via /auto-loop-spec-long. 8 сессий запланировано (все surface=backend-python, code-only). Runtime=loop_wrapper, Delay=600s, Auto merge=false. Helper-скрипты скопированы из /home/server/crm/.claude/scripts/. Spec обновлён до v0.8 (добавлен §22 Session Plan).
[2026-05-16 01:01 UTC] S1 promote: Pending → Current. Audit prior v0.7 scaffold (commit 03d3e29) covers pyproject.toml + agent/system_prompt.py (ttl="1h" ✓). Gaps to close in this wake: (a) add 4th beta header `interleaved-thinking-2025-05-14` + module-level ANTHROPIC_BETA_HEADERS constant; (b) state.db aiosqlite schema (agent_session, budget_tracker, event_queue); (c) tests/fixtures/ mock-odyssey project + mock stories; (d) green ruff+mypy+pytest gates.
[2026-05-16 01:10 UTC] S1 execution: agent/betas.py создан с 4 beta-флагами, Settings.beta_headers ссылается на canonical. state/db.py — aiosqlite schema (agent_session, budget_tracker, event_queue) + StateDB façade с create_session/end_session/upsert_budget/enqueue_event/claim_next_event. tests/fixtures/mock-odyssey/_bmad-output/planning-artifacts/ с 4 mock stories (1-1, 1-2, 1-3, 2-1) + epics.md + sprint-status.yaml. Migration (str, Enum) → StrEnum для UP042. pyproject.toml: добавлены RUF001/2/3 в ignore (русский prose), per-file-ignores для imports/from_bad/**, mypy overrides для scaffold-стабов. Все 3 gate'а green: ruff PASS, mypy --strict PASS (45 files), pytest 13/13 PASS.
[2026-05-16 01:10 UTC] S1 completed (commit dcaadfb). S2 promoted Pending → Current. Runtime=loop_wrapper: no ScheduleWakeup, wrapper handles next iteration.
[2026-05-16 08:30 UTC] S2 execution: 34 tools реализованы по §17 (5 state + 3 DAG + 4 spawn + 4 control + 3 merge + 3 memory + 3 retro + 5 operational + 2 splitter + 2 escalate). Mock-mode by default — destructive ops (spawn_worker, signals, git_merge на non-repo) emit intent в JSONL + state.db без реального side-effect. Path-traversal защита в memory tools. Catalog auto-generated через ALL_TOOLS + tool_names()/tool_descriptions(). bmad_orchestrator.agent.tools.* убран из mypy override — полная типизация. ASYNC230/240 в ruff ignore (asyncio + stdlib pathlib).  Все gate'ы green: ruff PASS, mypy --strict PASS (46 files), pytest 48/48 PASS (35 новых S2).
[2026-05-16 08:30 UTC] S2 completed (commit a1762c0). S3 promoted Pending → Current. Runtime=loop_wrapper: no ScheduleWakeup, wrapper handles next iteration.

## Final Report
(empty — last session not yet completed)
