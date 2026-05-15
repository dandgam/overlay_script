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

- **id:** S1
  **title:** Foundation & SDK scaffold (pyproject.toml, system_prompt.py, beta headers, state.db, mock fixtures)
  **surface:** backend-python
  **spec_section:** 270-326
  **depends_on:** []
  **acceptance:**
    - pyproject.toml со всеми deps из §11 (anthropic, claude-agent-sdk, networkx, pydantic v2, pydantic-ai, pyyaml, typer, rich, gitpython, structlog, aiosqlite, psutil, watchdog, python-telegram-bot, jinja2, presidio-analyzer, openai-whisper)
    - src/bmad_orchestrator/agent/ skeleton + system_prompt.py с cache_control={"type":"ephemeral","ttl":"1h"} explicit
    - ANTHROPIC_BETA_HEADERS константа со всеми 4 beta-флагами
    - state.db aiosqlite schema (agent_session, budget_tracker, event_queue)
    - tests/fixtures/ с mock projects + mock stories
    - ruff check, mypy --strict, pytest PASS
  **safety_gates:**
    - L2 budget guard (deterministic) — wired stub
  **destructive_actions:** []
  **checkpoint:** false
  **estimated_retries_allowed:** 3

- **id:** S2
  **title:** Tools layer (24 tools, 9 categories, defer_loading на всех)
  **surface:** backend-python
  **spec_section:** 643-712
  **depends_on:** [S1]
  **acceptance:**
    - 24 tools реализованы по §17: state(5)/DAG(3)/spawn(4)/control(4)/merge(3)/memory+retro(4)/operational(4)/splitting(2)/escalate(2)
    - Все с defer_loading=True (anti-pattern #1 §18.3)
    - Pydantic v2 schemas для inputs/outputs
    - Mock implementations: DB writes к state.db, file I/O к tests/fixtures/
    - Tool Search Tool integration (beta tool-search-tool-2025-10-19)
    - Unit tests на каждый tool (mock invocations PASS)
  **safety_gates:**
    - L1 PreToolUse hooks — wired stub (deny rules не активируются в S2)
  **destructive_actions:** []
  **checkpoint:** false
  **estimated_retries_allowed:** 3

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
(none — next wake promotes S1 from Pending)

### Completed
(empty — initiative not yet started)

## Safety Gates Triggered
(none)

## Blockers / Pauses
(none)

## Decisions Log
(none)

## Journal

[2026-05-16 04:10 UTC] bootstrap: tracker + backup branch (backup/orchestrator_agent-pre-2026-05-16) + integration branch (integration/orchestrator_agent) created via /auto-loop-spec-long. 8 сессий запланировано (все surface=backend-python, code-only). Runtime=loop_wrapper, Delay=600s, Auto merge=false. Helper-скрипты скопированы из /home/server/crm/.claude/scripts/. Spec обновлён до v0.8 (добавлен §22 Session Plan).

## Final Report
(empty — last session not yet completed)
