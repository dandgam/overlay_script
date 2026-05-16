# Spec — Orchestrator Agent (consolidated)

**Дата:** 2026-05-15
**Версия:** 0.2 (consolidated из handoff + research-compare + design discussion)
**Источник истины** для scaffold'а Phase 2 MVP.

---

## 1. Что это в одной фразе

LLM-агент-«прораб» (Opus 4.7 + Claude Agent SDK) который **параллелит N исполнителей** `bmad-auto-dev` skill'а в git worktrees, **контролирует merge через ревью-gates**, **учится на каждой story** и **просыпается только на событиях** — освобождая твой контекст от daily ops.

---

## 2. Архитектура

```
ТВОЯ session (1-2 раза в день):
  • wave checkpoint approval
  • skill engineering (патчи runner.sh)
  • escalations от агента
  • Phase 5 co-design

        │ только на эскалации/checkpoint
        ▼
┌────────────────────────────────────────────────────────┐
│  ORCHESTRATOR-AGENT (Claude Opus 4.7 + Agent SDK)      │
│                                                         │
│  Спит ⏸ → событие → читает state → думает → tools     │
│                                                         │
│  ┌─ tools/state ──────┐  ┌─ tools/dag ──────────┐     │
│  │ read_sprint_status  │  │ build_dag            │     │
│  │ list_worktrees      │  │ find_ready (DAG +    │     │
│  │ get_worker_status   │  │   shared-files mutex)│     │
│  │ tail_worker_jsonl   │  │ predict_conflicts    │     │
│  │ get_budget          │  └──────────────────────┘     │
│  │ read_memory         │                               │
│  └─────────────────────┘                               │
│                                                         │
│  ┌─ tools/spawn ──────┐  ┌─ tools/merge ────────┐     │
│  │ create_worktree     │  │ run_code_review (always)   │
│  │ spawn_worker        │  │ run_security_review (cond.)│
│  │ sync_skill_patches  │  │ git_merge (+ flock)        │
│  │ cleanup_worktree    │  └──────────────────────┘     │
│  └─────────────────────┘                               │
│                                                         │
│  ┌─ tools/control ────┐  ┌─ tools/escalate ────┐      │
│  │ pause_worker        │  │ to_human (TG/email) │      │
│  │ resume_worker       │  │ update_sprint       │      │
│  │ respond_elicitation │  │ audit_event         │      │
│  │ spawn_fixer         │  └─────────────────────┘      │
│  │ kill (if !alive)    │                                │
│  └─────────────────────┘                                │
│                                                         │
│  ┌─ tools/retro+learning ─────────────────────┐        │
│  │ detect_wave_boundary                        │        │
│  │ spawn_retro_worktree                        │        │
│  │ write_memory (per-story / per-wave)         │        │
│  │ compress_wave_lessons                       │        │
│  │ gen_wave2_prd_draft (Phase 5, co-design)    │        │
│  └─────────────────────────────────────────────┘        │
│                                                         │
│  HOOKS (deterministic safety, не агентское решение):   │
│  • PreToolUse deny: rm -rf, push --force, --no-verify  │
│  • Budget hard-cap interceptor                          │
│  • Branch isolation check (worker ≠ main)               │
│  • Liveness check before kill                           │
└────────────┬─────────────────────────────────────┬─────┘
             │ spawns                              │ spawns fresh
             ▼                                     ▼ claude -p
   ┌────────────────────┐               ┌────────────────────┐
   │ WORKERS × N=2..5   │               │ SUBAGENTS (ephemeral)│
   │ /odyssey-wt-1..N/  │               │ • code-review        │
   │ /bmad-auto-dev     │               │ • security-review    │
   │  skill (11 stages) │               │ • retrospective      │
   │ JSONL events out   │               │ • fixer (when Patch I│
   └────────────────────┘               │   не справился)      │
                                         └─────────────────────┘
```

---

## 3. 13 возможностей (что агент делает)

| # | Возможность | Tool / Hook | Источник |
|---|-------------|-------------|----------|
| 1 | Параллелизм 3 worktrees (default, matches BAD; на Tier 1 → 2; scale до 5 после validation). **Pipeline = 11 stages**: preflight → select → branch → gauntlet → create-story → **ATDD** → dev-story → **test-review** → code-review → merge → batch-gate (последние 2 BAD-вдохновлены, §20.4) | `create_worktree` + `spawn_worker` | handoff §6.2 + BAD §20 |
| 2 | DAG dep-resolution + **shared-files mutex pre-spawn** | `build_dag`, `find_ready` (учитывает `touches_files`) | spec §8.4 |
| 3 | Conflict prevention двумя слоями (predictive + pre-merge dry-run) | `predict_conflicts` (pre-spawn) + `run_code_review` (pre-merge) | handoff §5.2 |
| 4 | Auto retrospective на wave/epic boundary + Phase 5 (co-design) | `detect_wave_boundary` + `spawn_retro_worktree` + `gen_wave2_prd_draft` | handoff §3.2 |
| 5 | Two-tier budget: alarm $30/$200 → halt $50/$300 + `decide_dont_halt` rule | `get_budget` + Budget hard-cap hook | handoff §7.3 + memory |
| 6 | **Skill patches sync между worktrees** (file-watch + auto-pull) | `sync_skill_patches` | handoff §5.2 |
| 7 | **Per-worktree token bucket** для Anthropic ratelimit | runtime/ratelimit.py (deterministic, не tool) | handoff §5.2 |
| 8 | **Learning / memory** per-story → per-wave → cross-wave | `write_memory`, `compress_wave_lessons`, `read_memory` | discussion |
| 9 | **5 ролей распределены** (fixer/retro/security = fresh `claude -p`) | `spawn_fixer`, `spawn_retro_worktree`, `run_security_review` | handoff §1, §3 |
| 10 | **Event-driven wakeup**, не polling | runtime/event_loop.py + Anthropic streaming | research §10a |
| 11 | **Code-review всегда** + **security-review для critical эпиков** | `run_code_review`, `run_security_review` | spec §9 |
| 12 | **PreToolUse hooks** = deterministic safety (deny dangerous combos) | safety/hooks.py | research §10a #9 |
| 13 | **JSONL replay timeline** — worker events структурированы | `tail_worker_jsonl` + `_bmad-output/runs/<wave>/<story>.events.jsonl` | steal from Devin |
| 14 | **Natural language intent recognition** — пользователь пишет свободно по-русски, агент понимает | system prompt с tool catalog + few-shot + clarification rules | discussion §17 ниже |
| 15 | **Voice control (STT)** — голосовые сообщения в Telegram → текст → агент | `whisper local` STT в `bot/voice_handler.py` | §15.8 ниже |
| 16 | **Story splitting Stage 3.6** — большие stories автоматически делятся на sub-stories для +40pp first-try PASS rate + 2-4× wall-clock speedup | `check_should_split`, `split_story` tools + `story-splitter` skill | §21 ниже |

---

## 4. События которые будят агента

```python
events = [
    'worker_completed',       # JSONL stream ended with status
    'worker_halt_file',       # halt-reason.txt появился
    'worker_elicitation',     # worker пишет в pipe / спец-маркер в JSONL
    'budget_threshold_hit',   # deterministic guard triggered alarm
    'wave_boundary_reached',  # последняя story wave'а done
    'epic_boundary_reached',  # последняя story эпика done
    'user_chat_message',      # ← новое: ты написала в Telegram свободный текст
    'monthly_review_scheduled', # 1-го числа в 10:00 — deep self-review proactive-improver (§19)
    'voice_message_received',   # ← voice → STT (Whisper) → text → routed как user_chat_message
    'story_split_triggered',    # ← Stage 3.6: AC≥7 OR hours≥4 → spawn story-splitter skill
    'phase4_complete',        # все waves done → trigger Phase 5
    'human_response',         # ответ на эскалацию пришёл
    'scheduled_wakeup_5min',  # backstop, на случай если события потеряны
]
```

**Между событиями** — `ClaudeSDKClient` спит, токены не жгутся.

---

## 5. State (файлы)

| Файл | Назначение | Lock |
|------|------------|------|
| `<target>/_bmad-output/implementation-artifacts/sprint-status.yaml` | story status (single source of truth) | `flock` |
| `<target>/_bmad-output/implementation-artifacts/deferred-work.md` | defer list (часто пишут reviewers) | `flock` |
| `<target>/_bmad/auto-dev-state/state.json` | per-run state | per-worktree (⚠ local skill state, NOT BMad-canonical — путь определён нашим `bmad-auto-dev` skill в handoff §6.1) |
| `<target>/_bmad/auto-dev-state/current-batch.json` | batch progress | per-worktree (same — local skill, gitignored) |
| `<target>/_bmad/auto-dev-state/halt-reason.txt` | halt indicator | per-worktree (same) |
| `<target>/_bmad-output/runs/<wave>/<story>.events.jsonl` | replay timeline | append-only |
| `<target>/_bmad-output/runs/<wave>/memory/*.md` | learning artifacts | append-only |
| `<orchestrator>/state.db` | SQLite: agent session, budget tracker, event queue | один writer |

---

## 6. Learning — три уровня

| Уровень | Когда пишется | Что | Куда |
|---------|---------------|-----|------|
| **Тактика** (per story) | После завершения story | tokens, $, время, какие elicitation решены auto, какие escalate | `<wave>/<story>.lesson.md` + auto-grow `policy.yaml` |
| **Стратегия** (per wave) | На wave boundary, через retro subagent | паттерны падений, оптимальный parallelism, story cost distribution | `<wave>/wave-lessons.md` |
| **Архитектура** (per phase) | На phase boundary | какие типы stories требуют security review, cross-cutting risks | `<orchestrator>/memory/architectural-patterns.md` |

**Следующий wave** агент загружает lessons предыдущей wave как часть system prompt (с cache_control ttl="1h") — стартует умнее.

### 6.1 Mandatory Retrospective Schedule (hard gates — нельзя пропустить)

**9 обязательных retros за весь Phase 4 → Phase 5.** Detection через events, выполнение через `retrospective-writer` skill в эфемерной worktree.

| # | Уровень | Когда | Trigger event | Action |
|---|---------|-------|---------------|--------|
| 1 | **Wave 0a** retro | После 7/7 stories done | `wave_boundary_reached(wave=0a)` | `spawn_retro_worktree(wave=0a)` — что работает, корректируем план Phase 1 patches |
| 2 | **Wave 0b** retro | После 15/15 stories | `wave_boundary_reached(wave=0b)` | Те же + harvest для Wave 1 entry policy |
| 3 | **Wave 1a** retro | После всех stories 1a | `wave_boundary_reached(wave=1a)` | Lessons + first parallel-mode harvest |
| 4 | **Wave 1b** retro | Wave 1b complete | `wave_boundary_reached(wave=1b)` | Mid-Phase 4 calibration |
| 5 | **Wave 1c** retro | Wave 1c complete | `wave_boundary_reached(wave=1c)` | Pre-final calibration |
| 6 | **Wave 1d** retro | Wave 1d complete | `wave_boundary_reached(wave=1d)` | Last wave wrap-up |
| 7 | **Epic 1 deep** retro | Epic 1 (Platform Shell) closed — все его stories done | `epic_boundary_reached(epic=1)` | Deep retro — critical epic, отдельный артефакт |
| 8 | **Epic 7 deep** retro | Epic 7 closed — все stories done | `epic_boundary_reached(epic=7)` | Deep retro — second critical epic |
| 9 | **Phase 5 final** retro | Phase 4 complete (вся Wave 1d merged) | `phase4_complete` | `spawn_retro_worktree(phase=5)` + `gen_wave2_prd_draft` — **co-design с тобой**: агент draft, ты strategic review для Wave 2 Thor PRD |

**Hard gate semantics:**
- Агент **физически не может** перейти к следующей wave если retro предыдущей не сделан (check в `wave_coordinator` skill).
- Retro skip = automatic escalation к человеку: «retro для wave X не выполнен, причина?»
- Каждый retro производит artifact: `<target>/_bmad-output/runs/<wave>/retrospective.md` + memory updates (§6).
- **ОБЯЗАТЕЛЬНО:** каждый retro заканчивается **proactive push** через `proactive-improver` skill (§19) — Telegram-сообщение с предложениями улучшений (policy / config / code) и inline buttons. Без явного approve — изменения не применяются, но улучшения зафиксированы в audit log.

**Артефакты retros:**
| Retro | Output файлы |
|-------|--------------|
| Wave retro (×6) | `_bmad-output/runs/<wave>/retrospective.md`, `_bmad-output/runs/<wave>/policy-deltas.yaml` (proposed policy updates), `_bmad-output/runs/<wave>/lessons.md` |
| Epic deep (×2) | `_bmad-output/retrospectives/epic-<N>-deep.md` — архитектурные patterns, cross-cutting concerns, security learnings |
| Phase 5 final (×1) | `_bmad-output/retrospectives/phase4-final.md` + `_bmad-output/planning-artifacts/wave-2-thor-prd-draft.md` (для твоего review) |

**Skills involved per retro:**
- `retrospective-writer` — основной автор
- `reflexion-learner` — извлекает patterns для policy.yaml deltas
- `memory-curator` — решает что сжать, что забыть, что промотать в long-term memory
- `prd-drafter` — только для Phase 5 final (генерит Wave 2 PRD)

---

## 7. 5 ролей → 5 процессов

| Роль | Phase 1 (твоя session) | Phase 2 | Запуск |
|------|-------------------------|---------|--------|
| **Role 1** Orchestrator | runner + monitor + halt | **LLM-агент в 2 режимах:** (a) autonomous loop (event-driven), (b) chat mode (свободный текст ru через Telegram) | `bmad-orchestrator run --project odyssey --wave 1a` ИЛИ через Telegram «запусти одиссей 1a» |
| **Role 2** Manual fixer | Patch I не справился (Story 1.5 23-patch) | `fresh claude -p /bmad-code-fix` | tool `spawn_fixer(wt, findings)` |
| **Role 3** Skill engineer | патчи A-L в runner.sh | **остаётся у тебя** (human-only) | агент эскалирует «нужен patch M», не пишет сам |
| **Role 4** Meta-writer | retro, handoff, memory | `fresh claude -p /bmad-retrospective` | tool `spawn_retro_worktree(wave)` |
| **Role 5** Security reviewer | spawn bmad-security-review | `fresh claude -p /bmad-security-review` | tool `run_security_review(wt)` |

Каждый spawn = **fresh context**. Контекст оркестратора не делится с подагентами.

---

## 8. Budget model

```
Per story:
  $30  → alarm   (notification, продолжаем)
  $50  → halt    (стоп story, escalate)

Per batch (одна wave):
  $200 → alarm
  $300 → halt

Per wave total estimated (Wave 1a, 12 stories): ~$120-180
Per phase total estimated (Wave 0b → Phase 5): ~$3500-4000
Orchestrator overhead (event-driven wakeup, prompt caching): ~$10-15/wave
```

Tracking: parse `usage.input_tokens` + `usage.cache_read_input_tokens` + `usage.output_tokens` × model price из JSONL workers. Sum per story / batch / wave в SQLite.

---

## 9. Safety (3-layer)

| Layer | Что | Где |
|-------|-----|------|
| **1. PreToolUse hooks** | Deny `rm -rf`, `git push --force`, `git commit --no-verify`, `git reset --hard main` | `safety/hooks.py` — runs **before** агент даже видит tool результат |
| **2. Deterministic interceptors** | Budget hard-cap, branch-isolation check, liveness-before-kill | `safety/budget_guard.py`, `safety/branch_isolation.py`, `runtime/liveness.py` |
| **3. Branch isolation** | Worker физически не может тронуть main — он в worktree на feature-ветке | git mechanic + check в `git_merge` tool |

---

## 10. MVP scope (что входит / что defer)

### IN — Phase 2 MVP (Wave 1a первые 3 stories parallel)

- Все 14 возможностей из §3
- 3 worktrees параллельно (`MAX_PARALLEL=3` matches BAD default)
- Event-driven wakeup + chat-mode wakeup
- Hybrid cost routing: Sonnet 4.6 routine, Opus 4.7 для DAG-planning + merge-conflict resolution
- **Anthropic Memory Tool** (beta 23.04.2026) + per-worktree `.claude/memory/` файлы ← **promoted из OUT** после research §18.5
- **Telegram bot — IN MVP**: свободный русский текст + slash shortcuts + PII detector + inline confirmations (§15)
- **Русская локализация — IN MVP** (§14)
- **Свободно-формальный диалог** с агентом по-русски, без знания команд (§15)
- **Выбор модели через Telegram/CLI/per-role** (§16)
- **10 specialized skills** с progressive disclosure (§19)
- **Tool Search Tool + `defer_loading: true`** на все 24 tools ← critical per research §18.3 (anti-pattern #1)
- **Reflexion loop** для self-improvement post-wave (§19.3)
- **`proactive-improver`** skill — push улучшений автоматически после каждого retro (§19)
- **`story-splitter`** skill (Phase 2 MVP — heuristic only, без LLM): алерт + manual decision; LLM-driven splitting в v1 после 5-10 stories (§21.6)
- **Voice control** через Whisper local STT (§15.8) — личный проект, 152-ФЗ skip per AABIT
- Phase 5 PRD generation = **co-design** (агент draft, ты review)

### OUT — defer

- 3-5 worktrees (после первой успешной wave)
- Multi-project queue (Phase 5+)
- Self-modifying skill patches (v3 horizon, всегда через human)
- Cross-account spend distribution (single account достаточно)
- TTS (voice output от агента) — IN v2 если нужно
- LLM-driven splitting (Phase 2 v1 — после baseline data Wave 1a)
- Letta / mem0 / LangMem — Anthropic Memory Tool достаточно (research §18.5)
- LangGraph / CrewAI — agent сам управляет loop'ом, framework redundant (research §18.5)

---

## 11. Stack (финальный, из research-compare + handoff)

```toml
# pyproject.toml deps
[dependencies]
anthropic            = ">=0.40"          # raw SDK для DAG planner + retro subagent invocations
claude-agent-sdk     = ">=0.2.82"        # runtime орк-агента (вернули!)
networkx             = ">=3.2"           # DAG operations
pydantic             = ">=2.6"           # data contracts
pydantic-ai          = ">=1.96"          # typed LLM calls внутри tools (DAG planner)
pyyaml               = ">=6.0"           # sprint-status, policy
typer + rich         = "..."             # CLI
gitpython            = ">=3.1"           # git worktree ops
structlog            = ">=24.1"          # JSONL logs
aiosqlite            = ">=0.20"          # state.db
psutil               = ">=5.9"           # liveness check
watchdog             = ">=4.0"           # skill-patch file-watch
python-telegram-bot  = ">=22"            # Telegram interface (§15)
jinja2               = ">=3.1"           # email templates, mock fixtures
presidio-analyzer    = ">=2.2"           # PII detector (en/multi-lang)  TBD: confirm Russian quality
openai-whisper       = ">=20240930"      # voice STT, локально (Apache 2.0), §15.8
```

**После `pip install`**: загрузить spaCy RU model для PII detector:
```bash
python -m spacy download ru_core_news_lg
```

И Whisper medium model скачается автоматически при первом использовании (~5GB).

**Удалено vs research-compare v0.1:** LangGraph (агент сам управляет loop'ом, framework не нужен), отдельный SqliteSaver.

### 11.1 Anthropic API beta headers (обязательны)

```python
ANTHROPIC_BETA_HEADERS = [
    "tool-search-tool-2025-10-19",       # tool search + defer_loading (§18.2 #2)
    "advanced-tool-use-2025-11-20",      # advanced tool use patterns
    "context-management-2025-06-27",     # Memory tool (§18.5)
    "interleaved-thinking-2025-05-14",   # thinking между tool calls (опц.)
]
```

### 11.2 Cache TTL — ОБЯЗАТЕЛЬНО explicit

⚠ С 6 марта 2026 Anthropic silently dropped default cache TTL с 1h → 5min. **Каждый** `cache_control` блок ДОЛЖЕН явно указывать `"ttl": "1h"` или `3600` для часового кэша. Иначе cache hit rate = 0.

```python
# CORRECT
cache_control = {"type": "ephemeral", "ttl": "1h"}

# WRONG (will fall back to 5min)
cache_control = {"type": "ephemeral"}
```

Tests должны проверять `cache_creation_input_tokens > 0` только на первом вызове сессии, `cache_read_input_tokens` на всех последующих.

---

## 12. Открытые вопросы (нужны решения до scaffold)

| # | Вопрос | Default (если не ответишь) |
|---|--------|----------------------------|
| 1 | Стартуем scaffold сейчас или ждём конца Wave 0b? | **Сейчас**, на моках; реальный запуск после Wave 0b |
| 2 | Hybrid cost routing OK? (Sonnet routine + Opus hard decisions) | **Yes** |
| 3 | ~~Telegram defer~~ → **Telegram IN MVP** со свободным текстом + PII detector | resolved |
| 4 | Memory tool defer до GA OK? | **Yes** (plain .md files) |
| 5 | Phase 5 co-design (не fully auto) OK? | **Yes** |
| 6 | Где живёт policy.yaml — в target или в orchestrator repo? | **target** `<proj>/_bmad-output/_config/orchestrator-policy.yaml` |
| 7 | Worktree layout `/home/server/odyssey-wt-N/` (sibling) | **Yes**, sibling (handoff §6.2) |
| 8 | PII detector библиотека — `presidio-analyzer` или custom regex для русского? | **`presidio-analyzer` + расширения для RU** (паспорт, СНИЛС, российские имена). Если quality плохой — fallback на custom |
| 9 | Telegram chat_id whitelist — только твой ID или допускаем 2-3 ID? | **Только твой** в MVP, расширение через CLI config позже |

---

## 13. Next actions

1. Подтвердить open questions §12 (если defaults не подходят).
2. Обновить `research_agent_stack.md` §Вердикт — переписать под Claude Agent SDK runtime (был deferred, теперь primary).
3. Обновить `pyproject.toml` — добавить `pydantic-ai`, `aiosqlite`, `psutil`, `watchdog`, `python-telegram-bot`, `jinja2`, `presidio-analyzer`.
4. Скаффолд `src/bmad_orchestrator/agent/`: structure из §2 диаграммы + `bot/` подпакет.
5. Написать `system_prompt.py` с cache_control ttl="1h" на project context + ttl="5m" на текущий wave snapshot + few-shot для intent recognition (§17).
6. Mock-mode для разработки: tools работают на fake stories из `tests/fixtures/`.
7. Telegram bot smoke test: `/start` → echo → free-text → tool call → response.
8. PII detector validation на 20-30 русских sample-фраз перед production.
9. После Wave 0b complete → реальный pilot run на Wave 1a первые 3 stories.

---

## 14. UX & Operator Interface

### 14.1 CLI команды (typer + rich)

```bash
bmad-orchestrator run --project odyssey --wave 1a --max-parallel 2
bmad-orchestrator status [--live | --watch]   # snapshot или TUI dashboard
bmad-orchestrator pause                       # приостановить агента
bmad-orchestrator resume
bmad-orchestrator stop --graceful             # дождаться workers
bmad-orchestrator budget [--wave 1a]          # текущий $/токены
bmad-orchestrator logs --worker 1.8a [--tail 50]
bmad-orchestrator dag --wave 1a               # ASCII DAG
bmad-orchestrator retro --wave 0b             # ручной retro trigger
bmad-orchestrator validate-policy             # проверить policy.yaml
bmad-orchestrator memory --wave 0b            # показать lessons
bmad-orchestrator bot start                   # запустить Telegram bot daemon
bmad-orchestrator bot stop
```

### 14.2 TUI dashboard (rich.Live)

Содержит 5 секций: header status (running/budget/progress) · workers table · DAG ready next · agent thinking state · events log. Hotkeys `[q]uit [p]ause [r]etro [l]ogs [d]ag [b]udget [/?]`.

Дизайн-палитра: active=green 🟢, review=yellow 🟡, halt=red 🔴, done=dim green ✓, alarm=bold yellow ⚠, halt=bold red ⛔, sleeping=cyan ⏸, thinking=magenta 💭.

### 14.3 Локализация — русская primary

- Все user-facing сообщения (TUI, Telegram, email) — **русский**, через `locale/ru.yaml` (i18n.t dict-lookup).
- CLI `--help` — русский, `--lang en` опционально.
- Технические логи / structlog / identifiers / commit messages — **английский** (project convention из CLAUDE.md).
- Agent LLM responses — `system prompt: «отвечай по-русски, кратко, conversational»`.

### 14.4 Запуск — 3 режима

| Режим | Команда | Когда |
|-------|---------|-------|
| Foreground TUI | `bmad-orchestrator run --wave 1a --watch` | Тестирование, наблюдение живьём |
| Background daemon | `bmad-orchestrator run --wave 1a --daemon` | Запустил, мониторишь иногда |
| systemd service | `systemctl --user start bmad-orchestrator@odyssey-wave-1a` | Production, auto-restart, journalctl |

Конфиг через env vars (CLAUDE.md): `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `ORCHESTRATOR_DAILY_BUDGET`, `ORCHESTRATOR_TARGET_PROJECT`.

---

## 15. Telegram Bot Interface

### 15.1 Архитектура

```
Telegram Bot (отдельный daemon, systemd unit)
        │
        ├─ Whitelist check (chat_id == твой)
        ├─ PII detector (input scrubbing)
        │
        ├─► Свободный текст (PRIMARY)
        │        │
        │        ▼  
        │  Forwards → Orchestrator Agent's chat queue
        │        │
        │        ▼
        │  Agent thinks → tools → response
        │        │
        │        ▼
        │  Bot sends back to Telegram (с inline buttons где нужно)
        │
        └─► Slash commands (опциональные shortcuts)
             /start /help /status /stop /model /budget
```

Bot **сам без LLM** — прокси. Все intent recognition в orchestrator agent.

### 15.2 Основной режим — свободный русский текст

Примеры (см. §3 #14):

| Ты | Агент действие | Tool |
|----|---------------|------|
| «запусти одиссей 1a, два воркера» | parse intent → start | `start_wave(project=odyssey, wave=1a, max_parallel=2)` |
| «что сейчас?» | status snapshot | `read_sprint_status + list_workers + get_budget` |
| «почему 1.10a долго?» | analyze | `tail_worker_jsonl + analyze` |
| «дорого, на сонет» | switch model | `set_model(routine=sonnet)` |
| «останови всё» | clarify | inline buttons `[graceful] [hard kill]` |
| «покажи lessons 0b» | memory | `read_memory("wave-0b/lessons.md")` |
| «продолжи после Wave 0b» | schedule | install pre-check loop |
| «напомни в 10 завтра» | scheduler | install reminder |

### 15.3 Slash commands (optional shortcuts)

```
/start            init bot, whitelist check
/help             список возможностей + примеры свободного текста
/status           short status (равноценно «что сейчас?»)
/stop             то же что «останови всё» — но без LLM, instant
/model            показать текущую раскладку моделей
/budget           показать текущий $
/projects         список доступных target проектов
/cancel           отменить текущий dialog
```

### 15.4 Inline buttons — для destructive ops

| Команда | Confirmation |
|---------|--------------|
| «останови всё» | `[мягко: дождаться workers] [жёстко: kill сейчас]` |
| «удали wave 1a данные» | `[подтвердить — необратимо] [отмена]` |
| «откатить merge» | `[откатить + halt] [отмена]` |
| Auto-suggested follow-ups | `[запустить fixer] [показать findings] [игнорировать]` |

### 15.5 PII detector (input + output scrubbing)

**Стек:** `presidio-analyzer` (Microsoft, open source, GA) + custom Russian patterns (паспорт, СНИЛС, ИНН).

**Места проверки:**
- **Input** от тебя → перед отправкой в agent: «обнаружено похожее на email/имя, продолжить? [да] [редактировать]»
- **Output** от agent → перед отправкой в Telegram: автоматическая redaction `john@x.com → [EMAIL]`, `+7-999... → [PHONE]`. Логируется alert «PII redacted in response».
- **Audit log** `audit/telegram.jsonl` — original (на диске, не пересылается) + redacted (что ушло).

**Защита от false positives:** для технических identifiers (PIDs, paths, commit hashes) — `safelist` чтобы они не воспринимались как PII.

### 15.6 Безопасность Telegram bot

| Угроза | Mitigation |
|--------|------------|
| Чужой узнал token → запуск от твоего имени | Whitelist `chat_id` в env var, deny остальные |
| Token leak с диска | `chmod 600 .env`, systemd `LoadCredential`, не в коде |
| Перехват сообщений (Telegram MITM) | Bot API использует HTTPS; для high-sensitivity ops — confirm только через CLI |
| Случайные destructive ops в чате | Inline button confirmation, без auto-execute destructive |
| Логирование команд | Все команды + responses → `audit/telegram.jsonl` |
| Rate-limit от Telegram | python-telegram-bot v22+ автоматически handle'ит |

### 15.8 Voice control (STT) — pluggable providers

User-разрешено (личный проект, 152-ФЗ skip per AABIT decision 2026-05-16). Voice → STT provider → text → existing chat-mode flow.

```
Voice message в Telegram (.ogg, 30 sec)
        ↓
bot/voice_handler.py downloads to /tmp/<uuid>.ogg
        ↓
STT provider (configurable, см. 15.8.1)
        ↓
post-process technical terms (typo fixes)
        ↓
Result text → free_text_handler (как обычный chat)
        ↓
.ogg файл DELETED immediately (no persistent storage of audio)
```

#### 15.8.1 Pluggable STT providers

Switchable через CLI (`--voice-stt yandex`), Telegram («слушай через яндекс»), config file (`voice.stt_provider`).

| Provider | Cost | RU quality | Cross-border | Notes |
|----------|------|------------|--------------|-------|
| **`whisper_local`** (default) | $0 | ⭐⭐⭐⭐ | ❌ no | Apache 2.0, 5GB модель, локально |
| `whisper_api` (OpenAI) | $0.006/мин | ⭐⭐⭐⭐ | ✅ USA | Fast, no local GPU нужна |
| `claude_audio` (Anthropic beta 2026) | ~$0.003/мин | ⭐⭐⭐⭐ | ✅ USA | Native для нашего стека |
| `yandex_speechkit` | ~$0.015/мин | ⭐⭐⭐⭐⭐ | RU only | Best для русского, в РФ серверы |
| `google_stt_v2` | $0.024/мин | ⭐⭐⭐⭐ | ✅ USA | Premium quality |
| `disabled` | — | — | — | Voice handler выключен |

#### 15.8.2 Provider abstraction

`bot/voice_providers.py`:

```python
class STTProvider(ABC):
    name: str
    @abstractmethod
    async def transcribe(audio_path: Path, language: str) -> str: ...
    @abstractmethod
    def estimate_cost_usd(duration_seconds: float) -> float: ...

# 5 concrete: WhisperLocalSTT, WhisperAPISTT, ClaudeAudioSTT,
#             YandexSpeechKitSTT, GoogleSTT
# factory: make_stt_provider(name, **kwargs)
```

Аналогичный pattern для TTS (v2 feature): OpenAI TTS, ElevenLabs, Yandex SpeechKit, Coqui local.

#### 15.8.3 Switch через Telegram (свободный текст)

| Ты пишешь | Tool call |
|-----------|-----------|
| «слушай через яндекс» | `set_voice_provider(channel="stt", provider="yandex_speechkit")` |
| «голос на whisper» | `set_voice_provider(channel="stt", provider="whisper_local")` |
| «попробуй клод аудио» | `set_voice_provider(channel="stt", provider="claude_audio")` |
| «отключи голос» | `set_voice_provider(channel="stt", provider="disabled")` |
| «какой голос?» | Returns current `voice.stt_provider` |

#### 15.8.4 Fallback chain

При ошибке primary provider → автоматически fallback на `voice.stt_fallback` (default `whisper_local`). Уведомление в Telegram: «Yandex недоступен, переключилась на Whisper local».

#### 15.8.5 Cost tracking

Каждая транскрипция → audit log:
```json
{"event": "voice_transcribed", "provider": "yandex_speechkit",
 "duration_s": 28, "cost_usd": 0.007, "text_len": 142}
```

В Telegram сводка раз в неделю: «Голос: 47 сообщений, Yandex — $1.20, Whisper local — $0».

**Стек по умолчанию:** `openai-whisper>=20240930` (Apache 2.0), модель `medium` ~5GB. Остальные providers — optional, активируются установкой их credentials в `.env`.

**Post-processing** для technical terms:
- Whisper иногда mis-hears: `Cargo.toml` → «карго тошнол», `worker` → «вокер»
- LLM-correction шаг: если transcription содержит подозрительные транслитерации — спрашивает уточнение

**Latency budget:**
- Download: ~1-2s
- Whisper STT: ~3-5s (CPU medium model на современном CPU)
- Agent thinks + tools: ~2-5s
- **Total: 6-12s** от send до response

**TTS (response → voice)** — **deferred to v2**: текстовый ответ удобнее читать.

### 15.7 Стоимость chat-mode

| Тип запроса | Tokens | $ (Sonnet) | На день (50 msgs) |
|-------------|--------|------------|-------------------|
| Read-only («что сейчас?») | ~3K | $0.03 | $1.50 |
| Action («запусти X») | ~5K | $0.05 | $2.50 |
| Сложный анализ | ~8K | $0.10 | $5 |
| **Итого** | | | **~$3-7/день** |

<0.5% от phase total budget ($3500-4000). Приемлемо.

---

## 16. Model Selection

### 16.1 Через CLI

```bash
bmad-orchestrator run --wave 1a --model opus
bmad-orchestrator run --wave 1a \
    --planner-model opus \
    --reviewer-model opus \
    --dev-model sonnet \
    --routine-model haiku \
    --fallback-model haiku
```

### 16.2 Через Telegram (свободный текст или команды)

| Свободный текст | Команда | Действие |
|------------------|---------|----------|
| «какие сейчас модели?» | `/model` | Показать текущую раскладку |
| «всё на сонет» | `/model sonnet` | `set_model(all=sonnet)` |
| «планировщик опус, остальное сонет» | `/model planner=opus dev=sonnet routine=sonnet` | per-role |
| «сохрани как default» | `/model save` | Записать в `<proj>/_bmad-output/_config/orchestrator-models.yaml` |
| «fallback на хайку» | `/model fallback haiku` | При 529 overloaded |

### 16.3 Per-role defaults (recommended hybrid)

| Роль | Default | Альтернатива | Зачем |
|------|---------|--------------|-------|
| **Planner** (DAG, merge decisions) | Opus 4.7 | Sonnet 4.6 | Strategic — качество критично |
| **Reviewer** (code-review, security) | Opus 4.7 | Sonnet 4.6 | Adversarial depth |
| **Dev** (worker bmad-auto-dev) | Sonnet 4.6 | Opus 4.7 | Per spec routing |
| **Routine** (status, chat answers) | Sonnet 4.6 | Haiku 4.5 | Дешёвый поток |
| **Mechanical** (rename, format) | Haiku 4.5 | — | Mass cheap ops |
| **Fallback** | Haiku 4.5 | — | При overloaded |

### 16.4 Конфиг файл

```yaml
# <target>/_bmad-output/_config/orchestrator-models.yaml
models:
  planner: claude-opus-4-7
  reviewer: claude-opus-4-7
  dev: claude-sonnet-4-6
  routine: claude-sonnet-4-6
  mechanical: claude-haiku-4-5
  fallback: claude-haiku-4-5
```

Per-project preference. CLI/Telegram override на сессию.

---

## 17. Operational Tools Catalog (для chat-mode intent recognition)

Эти tools агент использует когда ты пишешь свободный текст. **22 tools всего** — на грани оптимума по research (>30 деградирует routing).

### State (5 tools)
```python
@tool read_sprint_status(project: str) -> SprintStatus
@tool list_worktrees() -> list[Worktree]
@tool get_worker_status(worktree: str) -> WorkerStatus
@tool tail_worker_jsonl(worktree: str, n: int = 50) -> list[Event]
@tool get_budget(scope: Literal["story","batch","wave","day"]) -> Budget
```

### DAG (3 tools)
```python
@tool build_dag(wave: str) -> DAG
@tool find_ready_stories(max_n: int) -> list[Story]   # включает shared-files mutex
@tool predict_conflicts(story_ids: list[str]) -> list[Conflict]
```

### Spawn (4 tools)
```python
@tool create_worktree(story_id: str, branch: str) -> Worktree
@tool spawn_worker(worktree: str, model: str, budget_cap_usd: float) -> WorkerPid
@tool sync_skill_patches(worktree: str) -> SyncResult
@tool cleanup_worktree(worktree: str) -> None
```

### Control (4 tools)
```python
@tool pause_worker(pid: int) -> None              # SIGSTOP
@tool resume_worker(pid: int) -> None             # SIGCONT
@tool respond_to_elicitation(pid: int, answer: str) -> None
@tool spawn_fixer(worktree: str, findings: list[Finding]) -> SubagentPid
```

### Merge (3 tools)
```python
@tool run_code_review(worktree: str) -> ReviewResult
@tool run_security_review(worktree: str) -> ReviewResult
@tool git_merge(worktree: str, target_branch: str, message: str) -> MergeResult
```

### Memory + Retro (4 tools)
```python
@tool read_memory(path: str) -> str
@tool write_memory(path: str, content: str, mode: Literal["append","overwrite"]) -> None
@tool spawn_retro_worktree(wave: str) -> SubagentPid
@tool compress_wave_lessons(wave: str) -> CompressionResult
```

### Operational (4 tools — для Telegram chat-mode)
```python
@tool start_wave(project: str, wave: str, max_parallel: int = 3, model: str | None = None) -> RunHandle
@tool stop_orchestrator(mode: Literal["graceful","hard"]) -> None
@tool set_model(role: str, model: str) -> None
@tool schedule_reminder(when: datetime, message: str) -> ReminderId
```

### Story splitting (2 tools — Stage 3.6, §21)
```python
@tool check_should_split(story_id: str) -> SplitDecision
@tool split_story(story_id: str, sub_stories: list[SubStorySpec]) -> SplitResult
```

### Escalate (2 tools)
```python
@tool escalate_to_human(reason: str, context: dict) -> HumanResponse
@tool update_sprint_status(story_id: str, status: str) -> None
```

### Каталог в system prompt — структура

```
SYSTEM PROMPT (cached, ttl="1h" — ⚠ explicit, см. §11.2):
[Project context: CLAUDE.md, epics.md, architecture] (~25K tokens)
[Operational rules from §17.X below]
[Tool metadata: 22 tools — только names + descriptions, не full schemas]
[Skill metadata: 10 skills (§19) — name + when activates, ~100 tokens each]
[Few-shot examples — 10-15 пар «свободный текст → tool call»]
[Personality: «отвечай по-русски, кратко, без preamble; уточняй destructive ops»]
```

### Tool Search Tool + defer_loading (CRITICAL, research §18.3)

⚠ С 22 tools мы на границе **anti-pattern #1 (tool bloat >30)**. Решение:

```python
# Все 22 tools регистрируются с defer_loading=True
@tool("read_sprint_status", "...", schema, defer_loading=True)
@tool("start_wave", "...", schema, defer_loading=True)
...

# Плюс tool-search-tool (Anthropic beta)
options = ClaudeAgentOptions(
    betas=["tool-search-tool-2025-10-19", "advanced-tool-use-2025-11-20"],
    tools=[...],  # все 22
)
```

Эффект: в base context только names + 1-line description (~2K tokens total). Full schema подгружается только когда агент явно ищет «нужен tool для X». **Token reduction: 80-90% на tool overhead.**

### Few-shot example (для intent recognition)

```
EXAMPLES:
─────────────────────────────────────────
USER: запусти одиссей 1a
ASSISTANT: <thinking>Запуск wave. Параметры: project=odyssey, wave=1a.
max_parallel не указан — использую default 2.</thinking>
→ start_wave(project="odyssey", wave="1a", max_parallel=2)
→ "✓ Запустила Wave 1a, 2 worker'а. Оценочно $120-180. Скажу когда первая story закончит."

USER: что 1.8a делает
→ tail_worker_jsonl(worktree="odyssey-wt-1", n=20)
→ "Stage 5 (dev-story), 12 мин в работе. Сейчас правит src/auth/signup.rs."

USER: останови
ASSISTANT: <thinking>Ambiguous — нужно уточнить graceful vs hard.</thinking>
→ "Уточни как: graceful (дождаться текущих 2 workers, ~15 мин) или hard kill (потеря работы)?
   [мягко] [жёстко]"

USER: дорого
→ "Текущая раскладка: planner=opus, reviewer=opus, dev=sonnet, routine=sonnet.
   Самое дорогое — planner+reviewer на Opus. Переключить routine на haiku?
   Это ~30% экономии без потери качества на review-gates."
```

### Правила disambiguation (в system prompt)

1. **Read-only ops (status, show, list)** — выполняй сразу, без подтверждения.
2. **Destructive ops (stop, kill, delete, rollback, push)** — всегда inline button confirmation.
3. **Action ops (start, spawn, set)** — выполняй, но покажи параметры до выполнения если они угаданы.
4. **Ambiguous query** — задай ОДИН уточняющий вопрос, не предполагай.
5. **Unknown** — скажи «не знаю» + предложи близкие команды.

---

## 18. Industry Patterns & References (2025-2026)

> Источник: research subagent на 25+ источниках Anthropic / Composio / Atlan / OWASP / Fundesk / Vectorize / Replit / Vercel (2025-2026). Сводка ниже.

### 18.1 Executive summary — что индустрия converged

1. **Orchestrator-workers с worktree isolation** — канонический паттерн для parallel code agents (Composio Agent Orchestrator, Claude Code operator pattern, ccswarm). Типично 2-5 параллельных. **Наша архитектура совпадает.**
2. **Tool count >30 degrades routing** (measurably). Anthropic beta `tool-search-tool-2025-10-19` + `defer_loading: true` дают 34-85% token reduction. **У нас 22 tools — на грани, нужен defer_loading.**
3. **Memory разделилась на 2 лагеря:** bolt-on (mem0, LangMem) vs full-runtime (Letta). **Anthropic Memory Tool** (public beta 23 апреля 2026) — путь наименьшего сопротивления для Claude-native стека. Файлы на диске → git-friendly, exportable.
4. **Все major 2026 ChatOps агенты** (Datadog Bits, PagerDuty SRE, AWS DevOps, incident.io) **gate destructive ops через human approval**. Никто не делает auto-merge to main без подтверждения. Наш inline-button confirmation совпадает с industry-standard.
5. **Skills + Subagents** (Anthropic open standard, 18 декабря 2025; adopted OpenAI, Google, Cursor, Copilot) заменили плоские prompt-файлы. **Three-level progressive disclosure** (metadata → body → references) — каждый skill ~100 токенов dormant.

### 18.2 5 Patterns to Adopt

| # | Pattern | Where in our spec | Why |
|---|---------|---------------------|-----|
| 1 | **Orchestrator-workers, 2-5 worktrees** | §2, §3 #1 | Production-proven scale ceiling; matches наш дизайн 1:1 |
| 2 | **Tool Search Tool + `defer_loading: true`** на все 22 tools | §17 (новое) | Keeps tools out of base context, 80-90% reduction на tool overhead, сохраняет prompt cache |
| 3 | **Skills с three-level progressive disclosure** | §19 (refined) | 10 specialized skills × ~100 tokens dormant вместо ~2K каждый — ~20K экономии в context window |
| 4 | **Anthropic Memory Tool** (file-based, beta 23.04.2026) | §10 reconsidered | Файлы на диске = export/edit/git-track friendly; matches наш `_bmad-output/_memory/` mental model; Claude-native, без отдельного сервиса |
| 5 | **HITL approval via Telegram inline keyboards** for every mutating op | §15.4 | Industry-universal: ни один 2026 ops agent не делает destructive auto-execute. Approve/Reject buttons = de-facto UX |

### 18.3 3 Anti-Patterns to AVOID

| # | Anti-pattern | Mitigation в нашем spec |
|---|--------------|---------------------------|
| 1 | **Tool bloat** (30-50 tools когда нужно <10) — measurably degrades selection (Atlan harness failures #1) | У нас 22, **граничный случай**. Tool Search Tool + defer_loading обязательны для MVP |
| 2 | **Write-only memory без management layer** — saving everything = retrieving nothing. Memory poisoning per OWASP Top 10 for Agents 2026 | `memory-curator-skill` (§19) + retention policy + compaction после каждой wave |
| 3 | **Recursive tool-call loops + over-permissive defaults** — resource exhaustion, документированы 4k fake-account incidents. OWASP "tool misuse" | `max_turns` per session + budget hard-cap + PreToolUse hooks + branch isolation (§9 layer 1-3) |

### 18.4 ⚠ Критичный gotcha — Cache TTL change

**6 марта 2026 Anthropic silently dropped default cache TTL с 1h на 5min.** Нужно **explicitly** ставить `"ttl": "1h"` (или `3600` секунд) в каждом `cache_control` блоке. Иначе cache hit rate уходит в ноль.

**Action:** в `system_prompt.py` ВСЕГДА указывать ttl явно. Tests должны проверять что cache_creation_input_tokens > 0 только на первом вызове сессии.

### 18.5 Library / Framework Recommendations (verified 2026)

| Concern | Use | Status |
|---------|-----|--------|
| Agent loop (master) | `claude-agent-sdk` Python + Opus 4.7 + `tool-search-tool-2025-10-19` + `advanced-tool-use-2025-11-20` betas + explicit `"ttl": "1h"` | SDK renamed март 2026; stable |
| Worker | `claude -p` headless subprocess + Sonnet 4.6 + worktree isolation | Production-standard 2026 |
| DAG ops | `networkx` 3.x | Stable, no LLM coupling |
| **Memory** | **Anthropic Memory Tool** (beta 23.04.2026) + per-worktree file `.claude/memory/` | **PROMOTE из defer в IN MVP** — file-based, git-friendly |
| Telegram | `python-telegram-bot v22.7` + `ConversationHandler` + `CallbackQueryHandler` | Current май 2026, async-native |
| **PII redaction** | **Microsoft Presidio** + spaCy `ru_core_news_lg` (custom Russian recognizer) | MIT, multi-lang built-in; ⚠ RU accuracy не verified — нужен test на нашем корпусе |
| Policy config | `pydantic v2` + `pyyaml` | Без изменений |
| **Self-improvement** | Roll-your-own **Reflexion** loop (Actor / Evaluator / Self-Reflection) writing to Memory Tool | Pattern «1.0», match наш use case. Skip DSPy/GEPA — overkill для MVP |
| **Avoid в MVP** | Letta (full runtime конфликтует с master/worker split), LangGraph (overhead без stateful graph beyond DAG), CrewAI (role abstraction redundant когда есть skills) | Все viable позже если outgrow custom code |

### 18.6 Что НЕ удалось верифицировать (open verification debt)

| # | Item | Action |
|---|------|--------|
| 1 | Russian Presidio NER accuracy на conversational ops chatter | Test on собственном корпусе перед production (152-ФЗ compliance) |
| 2 | Memory Tool — GA или ещё beta в мае 2026? | Treat как beta; регулярные backup memory files |
| 3 | python-telegram-bot v22 + Python 3.11+ TaskGroup compatibility | 10-min spike до commit |
| 4 | `tool-search-tool-2025-10-19` compatibility с `claude -p` subprocess workers | Verified для SDK, unclear для headless CLI — test |
| 5 | Точный tool count threshold для Claude 4.7 specifically | Generic "30-50" widely cited; Anthropic specific number не опубликован |
| 6 | Cost numbers для full Odyssey Wave 1a run | Нет comparable public case study; нужен pilot data |

---

## 19. Specialized Internal Skills (финальный список после research §18)

Финализировано после industry research + §21 story splitting. **12 skills** = sweet-spot per Anthropic Skills standard (Dec 2025).

Каждый skill — отдельный `.claude/skills/<name>/SKILL.md` с **three-level progressive disclosure**:
1. **Metadata** (~100 tokens) — name + description, always in context
2. **Body** — full SKILL.md, loaded только при dispatch
3. **References** — дополнительные файлы (templates, examples), loaded on-demand

| Skill | Когда активируется | Что специализирует |
|-------|---------------------|---------------------|
| **`dag-planner`** | Старт wave / epic boundary / story re-prioritization | Reads `_bmad-output/planning-artifacts/`, строит DAG, identifies parallel-safe sets через shared-file analysis |
| **`worker-dispatcher`** | Per ready DAG node | Spawns `claude -p` subprocess в worktree, sets cost cap + tool harness, monitors heartbeat |
| **`merge-gate`** | Worker completion | Runs `bmad-code-review` + security review, decides merge / reject / escalate |
| **`elicitation-router`** | Worker emits clarification | Maps incoming question → policy YAML → auto-answer / escalate / batch-defer |
| **`retrospective-writer`** | Wave boundary | Wave summary, обновляет `progress.md`, harvests new policy entries |
| **`intent-router`** | Каждый inbound Telegram msg (chat-mode) | Free-text RU/EN → tool call. Disambiguation, clarification, destructive-op confirmation |
| **`cost-watchdog`** | Continuous (cheap polling) | Tracks token spend per worker/batch/wave, halts at hard cap, escalates 80% threshold |
| **`failure-analyst`** | Worker exit ≠ 0 | Diagnose: test fail / merge conflict / API err / context drift; suggest fix или escalate |
| **`reflexion-learner`** | Async post-wave | Scans elicitation logs + failures, proposes policy YAML deltas. Reflexion pattern (Actor/Evaluator/Self-Reflection) |
| **`wave-coordinator`** | Wave & checkpoint events | Manages Wave 1a→1b→2 boundaries, gates human checkpoint каждые 10 stories, integration→main merge proposal |
| **`proactive-improver`** | После каждого retro + monthly cron + chat запрос | **Proactive** push улучшений (policy/config/code) в Telegram с inline buttons. Без проактива агент только накапливает знания и не предлагает применять — это закрывает gap |
| **`story-splitter`** | Stage 3.6 (AC≥7 OR hours≥4) — между Gauntlet и create-story | Структурная декомпозиция большой story на atomic sub-stories (AC≤5, один слой). Phase 2 MVP — heuristic-only; v1 — Opus LLM. Эффект: +40pp first-try PASS, 2-4× wall-clock через parallel sub-stories. См. §21 |

### 19.1 Skill dispatch — как агент выбирает

```
1. Agent receives wake event (e.g. worker_completed)
2. Loads metadata of all 10 skills (~1K tokens cached)
3. Decides which skill matches event type
4. Loads chosen skill body (~3-5K tokens) via Read tool
5. Executes per skill instructions
6. Skill body unloads after completion → context освобождён
```

### 19.2 Размещение

```
src/bmad_orchestrator/agent/skills/
├── dag-planner/
│   ├── SKILL.md
│   └── references/
│       ├── networkx-patterns.md
│       └── conflict-detection.md
├── worker-dispatcher/
│   └── SKILL.md
├── merge-gate/
│   ├── SKILL.md
│   └── references/
│       └── review-criteria.md
├── proactive-improver/
│   ├── SKILL.md
│   └── references/
│       └── proposal-templates.md
... (11 skills total)
```

### 19.3 Reflexion loop (для reflexion-learner skill)

```
Actor (LLM): «Вот что произошло на wave 1a: 12/12 stories, 3 escalations»
   │
   ▼
Evaluator (LLM): «Из escalations: 2 были auto-resolvable если бы policy.yaml имел rule X»
   │
   ▼
Self-Reflection (LLM): «Предлагаю добавить rule X в policy. Pattern для future waves: пре-валидировать Y перед spawn»
   │
   ▼
Write to Memory Tool: `lessons/wave-1a-reflections.md`
   │
   ▼
Next wave loads these lessons → starts smarter
```

---

---

## 20. Related Work — BAD comparison + cherry-picks

> Subagent research (2026-05-15) на `stephenleo/bmad-autonomous-development` (BAD).

### 20.1 Что такое BAD

- **Claude Code skill** (markdown-as-program в Claude Code REPL), НЕ Python package
- ~1500 LOC total: 13 .md reference files + 3 setup .py scripts
- Solo author (Marie Stephen Leo), MIT, v1.1.0 (11.04.2026), 80 stars, 0 open issues
- 14 дней разработки apr 5-19 → не "battle-tested" в смысле проверки сообществом, но **хорошо спроектировано** опытным автором

### 20.2 Decision: **inspired-by + cherry-pick** (90% confidence)

| Вариант | Решение | Reason |
|---------|---------|--------|
| ❌ Fork | not chosen | Runtime mismatch (markdown vs Python); 70% spec'а не покрыто |
| ❌ Hard depend | not chosen | Нет Python package surface, нет import API |
| ✅ Inspired-by + cherry-pick | **CHOSEN** | Берём 5 проверенных паттернов + 7-step pipeline + env vars; всё остальное наше |
| ❌ Полная независимость | not chosen | Глупо игнорировать готовые решения |

### 20.3 Feature coverage сравнение

BAD покрывает ~30% нашего spec'а. Полная карта в task output `a5b098a21a97a1a7e.output` (subagent report).

**BAD HAS:** worktree parallelism, story-level DAG, code-review gate (Opus), watchdog pattern, push-only Telegram, activity logs.

**BAD MISSING:** cost-budget ($), memory/Reflexion, 5-role distribution, intent recognition (inbound chat), 9 mandatory retros, PreToolUse hooks, file-level mutex, security review, PII detector, Russian localization.

### 20.4 Cherry-picked patterns (5 файлов)

В `src/bmad_orchestrator/imports/from_bad/`:

| Файл | Source BAD | Adapted as |
|------|------------|------------|
| `activity_hook.py` | `skills/bad/scripts/setup-activity-hook.py` | PostToolUse hook + jq filter для structured logs |
| `gh_client.py` | `pattern-gh-curl-fallback.md` | gh CLI с curl fallback для sandboxes |
| `watchdog_fsm.py` | `pattern-watchdog.md` | STALE detection FSM ([K]/[R]/[S]/[A]) |
| `merge_gate_prompt.py` | `subagents/phase3-merge.md` | System prompt для merge-gate skill |
| `dag_planner_prompt.py` | `subagents/phase0-graph.md` | System prompt для dag-planner skill (+ наше file-mutex дополнение) |

Дополнительно adopted (не как файлы):
- **7-step pipeline shape** → стало 11 stages у нас (см. §3 cap #1)
- **Env var names**: `MAX_PARALLEL_STORIES`, `MODEL_STANDARD`, `MODEL_QUALITY`, `WORKTREE_BASE_PATH` как aliases
- **Squash-merge rule** «always keep origin/main for sprint-status.yaml» — load-bearing

### 20.5 Что НЕ берём (anti-patterns)

| BAD | Почему отказываемся |
|-----|---------------------|
| One-shot epic retro by timer + keypress | Нам нужны 9 mandatory (§6.1) |
| Push-only Telegram (`mcp__plugin_telegram_telegram__reply`) | Нам нужен inbound free-text (§15.2) |
| PostToolUse-only hooks (только activity log) | Нам нужны PreToolUse deny-list (§9) |
| Story-level DAG **без file collision check** | Наш file-level mutex предотвращает merge-кошмар (§3 cap #2) |
| `gh pr merge --auto` без retest в target tree | Нам нужен retest на integration branch перед merge |
| Markdown-as-program runtime | Несовместимо с нашей Python daemon архитектурой |

### 20.6 Наши differentiator'ы которых нет ни у BAD, ни у BMad core

| # | Differentiator | Где в spec'е |
|---|----------------|---------------|
| 1 | Cost-budget $-cap с two-tier alarm/halt | §8 |
| 2 | Anthropic Memory Tool + Reflexion learning | §6, §19 reflexion-learner |
| 3 | 5-role distribution на 5 fresh `claude -p` процессов | §7 |
| 4 | Свободный русский текст в Telegram + intent router | §15.2, §19 intent-router |
| 5 | 9 mandatory retrospectives как hard gates | §6.1 |
| 6 | 3-layer safety (PreToolUse + interceptors + branch isolation) | §9 |
| 7 | PII detector (presidio + RU patterns) + 152-ФЗ compliance | §15.5 |
| 8 | Phase 5 co-design PRD auto-draft | §6.1 #9 |
| 9 | File-level mutex в DAG | §3 cap #2-3 |
| 10 | Tool Search Tool + defer_loading для 22 tools | §17, §18.2 |

### 20.7 Attribution

Когда мы используем cherry-picked файлы — credit MIT-лицензии в headers (already done в `imports/from_bad/*.py`). При публикации нашего проекта — README mentions BAD как inspiration.

### 20.8 Verification debt (open)

| # | Item | Verify by |
|---|------|-----------|
| 1 | BAD SKILL.md verbatim contents | curl raw + manual read |
| 2 | PR #6 "Codex edits" content | gh pr view 6 |
| 3 | BAD Monitor tool версии vs claude-agent-sdk совместимость | Test in sandbox |
| 4 | Real cost numbers их пользователей | BAD Discord если есть |

---

---

## 21. Story Splitting (Stage 3.6 Pre-split check)

> Источник: `/home/server/odyssey/spec/story-splitting-principles-2026-05-16.md` (AABIT, 2026-05-16) — основано на эмпирике Wave 0a + Wave 0b (Story 1.5 — 24 patches, 3 review rounds, ~3 часа; Story 1.8a — 13 patches + N1 regression; против атомарных stories 1.3/1.4 — first-try PASS).

### 21.1 Зачем — измеренный эффект

| Параметр | Без splitting | С splitting (Phase 2) |
|----------|---------------|------------------------|
| First-try PASS rate на больших stories | ~30% | **~70%** (+40pp) |
| Avg cycle time на сложной story | 60-90 мин + retry | 25-45 мин per sub-story |
| Wall-clock speedup (Phase 2 + split) | 1× | **2-4×** |
| Token cost overhead | baseline | +15-25% |

**Главный insight:** splitting сам по себе **не ускоряет** (даже немного замедляет на токенах) — но **раскрывает потенциал параллельного оркестратора**. Без orchestrator splitting приносит только quality. С orchestrator — quality × parallelism.

### 21.2 Когда делить — сигналы

**Hard signals** (без сомнений — split):
1. **AC count ≥ 7**
2. **Estimated hours ≥ 4** во frontmatter
3. **Spec упоминает 3+ слоёв** архитектуры (trait + impl + DB schema + conventions doc)
4. **Story bundles unrelated concerns** (X functionality + Y observability + Z documentation)
5. **Anticipated >300 lines нового кода** в одном feature commit

**Soft signals** (часто — split):
- Gauntlet 5-lens баки 5+ convergent findings
- AC text содержит «AND» в acceptance clauses
- Story требует обновления 2+ конфигов (`conventions.md`, `lint-migrations.sh`)

**Anti-signals** (НЕ делить):
- Story = один артефакт (одна миграция, один endpoint, один helper)
- AC образуют атомарную единицу (RLS policy + test of policy нельзя разделить)
- Split добавит дублирование контекста (две stories будут читать те же файлы)
- Foundation story (атомарна по архитектурным причинам)

### 21.3 Правила атомарности sub-story

| Правило | Описание |
|---------|----------|
| AC ≤ 5 | Sub-story проверяет ≤5 инвариантов |
| Один слой | trait OR impl OR migration OR tests OR conventions — не комбинация |
| Self-contained merge | После merge integration branch в работоспособном состоянии (build green, tests pass) |
| Explicit deps | Если B нужна A — `deps` в split JSON |
| Атомарность | Не split если разрушает критический инвариант |
| Parallel-friendly | Без deps → можно параллельно в разных worktree |

### 21.4 Mechanism — Stage 3.6 Pre-split check

**Где в pipeline:** между Stage 3.5 (Gauntlet lens validation) и Stage 4 (create-story).

**Pipeline стало 12 stages** (было 11 после v0.5 с ATDD + test-review):
```
1. preflight
2. select
3. branch
3.5 gauntlet 5-lens validation
3.6 pre-split check         ← НОВОЕ
4. create-story
5. ATDD (testarch-atdd)
6. dev-story
7. test-review (testarch-test-review)
8. code-review
9. merge
10. batch-gate
```

**Gate condition:** AC count ≥ 7 OR estimated_hours ≥ 4.

**LLM:** Phase 2 v1+ — Opus 4.7 (структурная декомпозиция). Phase 2 MVP — **heuristic only** (без LLM, просто алерт «AC≥7, рассмотри split» + manual decision).

**Input/Output:**
```python
# Input ~4000 tokens
{
  "story_outline": "<from epics.md>",
  "ac_list": [...],
  "gauntlet_findings": {...}
}

# Output ~500 tokens
{
  "decision": "split" | "keep",
  "rationale": "<один абзац>",
  "sub_stories": [
    {"id": "X.a", "scope": "...", "ac": ["AC1", "AC2"], "estimated_hours": 2, "deps": []},
    {"id": "X.b", "scope": "...", "ac": ["AC3", "AC4"], "estimated_hours": 3, "deps": ["X.a"]}
  ]
}
```

**Действия orchestrator'а после split JSON:**
- `decision: "keep"` → continue to Stage 4
- `decision: "split"` → write sub-stories в sprint-status.yaml (flock-safe), mark parent `superseded`, dispatch first sub-story (без deps) в Stage 4, остальные в DAG planner для parallel dispatch

### 21.5 Token cost math

| Item | Cost |
|------|------|
| Per check (Opus 4.7): 4000 in × $15/M + 500 out × $75/M | **$0.10** |
| Wave 1 (316 stories × 50% triggered = 158 checks) | $16 |
| ~30% triggered → ~50 splits → +100 sub-stories | — |
| Дополнительный dev cycle cost (Sonnet + Opus per sub-story) | +$1000 |
| Saved retry cost (-30% retry на больших stories) | −$400 |
| **Net на Wave 1 baseline ($3500)** | **+$600 ≈ +17%** |

Окупается через wall-clock speedup 2-4× = часы человеческого времени.

### 21.6 Phasing внедрения

| Фаза | Когда | Что |
|------|-------|-----|
| **Phase 2 MVP** | Wave 1a start | **Heuristic only** — алерт «AC≥7 OR hours≥4», manual decision AABIT |
| **Phase 2 v1** | После 5-10 stories Wave 1a | LLM-driven Opus auto-split, orchestrator забирает sub-stories параллельно |
| **Phase 2 v2** | После Epic 1 closed | Tuning thresholds на основе observed first-try PASS rate; consider Haiku 4.5 для pre-split |

### 21.7 Failure modes

1. Malformed JSON → fallback `keep`, log warning
2. Sub-story B depends on A не в batch → orchestrator переносит A первым
3. Split decision oscillates → cache per story_id
4. estimated_hours некорректен в epics.md → fallback на AC count alone
5. Split разделил атомарность ошибочно (merge tests падают) → red flag: revert split, rerun monolith
6. Split overhead > benefit → tracking metric, выключить если KPI плохой

### 21.8 KPIs

```json
{
  "splitting_metrics": {
    "wave": "1a",
    "checks_performed": 50,
    "checks_returned_split": 15,
    "preflight_cost_usd": 5.0,
    "additional_subs_total_cost_usd": 180,
    "saved_retry_cost_usd": 95,
    "net_overhead_usd": 90,
    "first_try_pass_rate_pre_split": 0.32,
    "first_try_pass_rate_post_split_subs": 0.71,
    "wall_clock_speedup_observed": 2.3
  }
}
```

**Go/no-go rules:**
- `net_overhead_usd / saved_retry_cost_usd > 2` AND `speedup < 1.5` → выключить
- `pass_rate_post_split > pre_split × 1.5` → продолжать

### 21.9 Storage

- Split decisions: `<target>/_bmad-output/runs/<wave>/split-decisions/<story_id>.json`
- Metrics: `<orchestrator>/state.db` table `splitting_metrics`
- Audit: `<target>/_bmad-output/runs/<wave>/<story>.events.jsonl` (Stage 3.6 events)

### 21.10 Manual override

- Frontmatter в epics.md: `splittable: false` → агент НИКОГДА не split эту story
- Telegram: «не дели 1.5» → сохраняется в session memory

---

---

## 22. Session Plan (для /auto-loop-spec-long, добавлено 2026-05-16)

> 8 сессий, surface=`backend-python` для всех (greenfield Python project; ближайший аналог в dispatch table). Все сессии code-only, `destructive_actions: []`. Checkpoints на S3, S6, S8.

### S1 — Foundation & SDK scaffold
- **surface:** backend-python
- **spec_section:** lines 270-326 (§11 Stack + §11.1 beta headers + §11.2 cache TTL)
- **depends_on:** []
- **destructive_actions:** []
- **checkpoint:** false
- **acceptance:**
  - `pyproject.toml` со всеми deps из §11 (anthropic, claude-agent-sdk, networkx, pydantic v2, pydantic-ai, pyyaml, typer, rich, gitpython, structlog, aiosqlite, psutil, watchdog, python-telegram-bot, jinja2, presidio-analyzer, openai-whisper)
  - `src/bmad_orchestrator/agent/__init__.py` + `system_prompt.py` с `cache_control={"type":"ephemeral","ttl":"1h"}` явно
  - `ANTHROPIC_BETA_HEADERS` константа: tool-search-tool-2025-10-19, advanced-tool-use-2025-11-20, context-management-2025-06-27, interleaved-thinking-2025-05-14
  - `state.db` aiosqlite schema: agent_session, budget_tracker, event_queue
  - `tests/fixtures/` с mock projects + mock stories
  - `ruff check`, `mypy --strict`, `pytest` PASS

### S2 — Tools layer (24 tools, 9 categories, defer_loading)
- **surface:** backend-python
- **spec_section:** lines 643-712 (§17 Operational Tools Catalog)
- **depends_on:** [S1]
- **destructive_actions:** []
- **checkpoint:** false
- **acceptance:**
  - 24 tools реализованы: state(5), DAG(3), spawn(4), control(4), merge(3), memory+retro(4), operational(4), splitting(2), escalate(2)... wait это 31 — re-count: 5+3+4+4+3+4+4+2+2 = **31**. Note: §17 утверждает 22, но фактически перечислено больше; уточнить в S2 review.
  - **Все** с `defer_loading=True` (anti-pattern #1 mitigation, §18.3)
  - Pydantic v2 schemas для inputs/outputs каждого tool
  - Mock implementations: DB writes к state.db, file I/O к tests/fixtures/
  - Tool Search Tool integration (beta header `tool-search-tool-2025-10-19`)
  - Unit tests на каждый tool (mock-mode invocations PASS)

### S3 — Core runtime: event loop + DAG + worker spawn (CHECKPOINT)
- **surface:** backend-python
- **spec_section:** lines 15-79 (§2 Architecture), 106-127 (§4 events), 130-142 (§5 state)
- **depends_on:** [S2]
- **destructive_actions:** []
- **checkpoint:** true
- **acceptance:**
  - `runtime/event_loop.py` — event-driven wakeup (13 event types из §4), backstop polling 5min
  - `runtime/ratelimit.py` — per-worktree token bucket (Anthropic ratelimit)
  - DAG planner (networkx 3.x) — `build_dag`, `find_ready_stories` с shared-files mutex
  - `create_worktree` + `spawn_worker` — `claude -p` subprocess, Sonnet 4.6 default
  - JSONL event tail: `_bmad-output/runs/<wave>/<story>.events.jsonl`
  - Heartbeat / liveness check через psutil
  - Mock pilot: на 3 fake stories DAG → ready set → worker spawned → JSONL streamed → completed
  - **Checkpoint review:** прежде чем S4 — human reviews integration branch

### S4 — Safety: 3-layer + budget + branch isolation
- **surface:** backend-python
- **spec_section:** lines 226-234 (§9 Safety), 206-222 (§8 Budget)
- **depends_on:** [S3]
- **destructive_actions:** []
- **checkpoint:** false
- **acceptance:**
  - `safety/hooks.py` PreToolUse deny: `rm -rf`, `git push --force`, `git commit --no-verify`, `git reset --hard main`
  - `safety/budget_guard.py` — story alarm $30/halt $50, batch alarm $200/halt $300; tracks `usage.input_tokens + cache_read + output × price`
  - `safety/branch_isolation.py` — worker может писать только в свою feature-ветку в worktree
  - `runtime/liveness.py` — liveness-before-kill (psutil)
  - Tests: каждый PreToolUse rule reject'ит запрещённую команду; budget hard-cap halts mock workflow на $50/$300
  - `audit_event` tool wires events в audit log JSONL

### S5 — 12 specialized internal skills (progressive disclosure)
- **surface:** backend-python
- **spec_section:** lines 844-993 (§19 Specialized Internal Skills + §19.1 dispatch + §19.2 placement + §19.3 reflexion loop)
- **depends_on:** [S4]
- **destructive_actions:** []
- **checkpoint:** false
- **acceptance:**
  - 12 skills × `src/bmad_orchestrator/agent/skills/<name>/SKILL.md` с frontmatter (name, description, when-activates)
  - Names: dag-planner, worker-dispatcher, merge-gate, elicitation-router, retrospective-writer, intent-router, cost-watchdog, failure-analyst, reflexion-learner, wave-coordinator, proactive-improver, story-splitter
  - Three-level disclosure: metadata (~100 tokens always-loaded) / body (loaded on dispatch) / references (on-demand)
  - Skill dispatcher logic — выбор skill по event type
  - Reflexion loop в `reflexion-learner` (Actor / Evaluator / Self-Reflection per §19.3)
  - Tests: skill metadata loads <1.5K tokens total; full body loads только при match

### S6 — Telegram bot + voice (Whisper) + PII (Presidio) (CHECKPOINT)
- **surface:** backend-python
- **spec_section:** lines 404-588 (§15 Telegram bot + §15.1-15.8 voice + PII)
- **depends_on:** [S5]
- **destructive_actions:** []
- **checkpoint:** true
- **acceptance:**
  - `bot/main.py` — python-telegram-bot v22+, async, ConversationHandler + CallbackQueryHandler
  - chat_id whitelist (env var `TELEGRAM_ALLOWED_CHAT_IDS`), deny остальные
  - Slash commands: /start, /help, /status, /stop, /model, /budget, /projects, /cancel
  - Inline buttons для destructive ops (graceful/hard kill, confirm/cancel)
  - `bot/voice_handler.py` — .ogg download → STT → text routing
  - `bot/voice_providers.py` — STTProvider ABC + 5 concrete (WhisperLocal default, WhisperAPI, ClaudeAudio, YandexSpeechKit, GoogleSTT) + factory
  - `bot/pii_detector.py` — Presidio + spaCy `ru_core_news_lg` + custom RU patterns (паспорт, СНИЛС, ИНН)
  - PII redaction: input check + output scrubbing; safelist для technical IDs
  - `audit/telegram.jsonl` append-only (original + redacted)
  - Smoke test: bot stub отвечает на `/start`, voice stub транскрибирует sample .ogg, PII tests на 20-30 RU sample-фразах PASS
  - **Checkpoint review:** human reviews integration branch перед S7

### S7 — Memory + Retrospective + 9 mandatory retros
- **surface:** backend-python
- **spec_section:** lines 145-189 (§6 Learning + §6.1 mandatory retros + §6 артефакты)
- **depends_on:** [S6]
- **destructive_actions:** []
- **checkpoint:** false
- **acceptance:**
  - 3 уровня learning: per-story (`<wave>/<story>.lesson.md`), per-wave (`<wave>/wave-lessons.md`), per-phase (`memory/architectural-patterns.md`)
  - Anthropic Memory Tool integration (beta `context-management-2025-06-27`)
  - `read_memory`, `write_memory`, `compress_wave_lessons` tools wired
  - `spawn_retro_worktree(wave)` — fresh `claude -p` subprocess
  - 9 mandatory retros: Wave 0a/0b/1a/1b/1c/1d (×6) + Epic 1 deep + Epic 7 deep + Phase 5 final
  - Hard gates: agent физически не может перейти к next wave если retro не сделан (`wave_coordinator` skill)
  - `proactive-improver` skill вызывается после каждого retro с inline-button suggestions в Telegram
  - Mock test: write per-story lesson → wave boundary → spawn_retro produces retrospective.md → memory roll-up

### S8 — CLI + TUI dashboard + E2E mock pilot (CHECKPOINT)
- **surface:** backend-python
- **spec_section:** lines 359-402 (§14 UX & Operator Interface), 591-642 (§16 Model Selection)
- **depends_on:** [S7]
- **destructive_actions:** []
- **checkpoint:** true
- **acceptance:**
  - `cli/main.py` — typer commands: `run`, `status`, `stop`, `model`, `budget` per §14.1
  - TUI dashboard `cli/tui.py` — rich.Live, refresh 2s, panels (workers/budget/events/eta) per §14.2
  - Russian primary localization (русские tooltips, error messages, status labels) per §14.3
  - 3 launch modes: foreground tmux, systemd unit, detached daemon per §14.4
  - Model selection (CLI + Telegram + per-role config) per §16
  - `_config/orchestrator-models.yaml` per-role defaults persistence
  - **E2E mock pilot:** `bmad-orchestrator run --project mock-odyssey --wave 1a --max-parallel 2` runs to completion в mock-mode без падений
  - All previous tests still PASS (regression matrix)
  - **Final checkpoint:** ready for real pilot run на Wave 1a после `_bmad-output/` artifacts появятся в Odyssey

### Out of scope (defer to v1)
- Реальный run на Odyssey Wave 1a (требует Wave 0b complete)
- TTS (voice output)
- LLM-driven story splitting (только heuristic в MVP)
- Multi-project queue
- Self-modifying skill patches

---

## 22.7 Sandbox layer (FS7, round 3 — 2026-05-16)

**Primary safety для worker subprocess** = OS-level isolation через `bwrap`
(Bubblewrap). Заменил pattern-based `_scan_bash` как primary safety floor
после того как 3 round'а fix-loop'ов произвели по 5-6 новых P0 bash-bypass'ов
каждый (NC1 `bash <<<`, NC2 `(rm -rf x)`, NC3 brace expansion, NC4 xargs,
NC5 pipe-to-interpreter…). Blacklist на bash text фундаментально неисчерпаем.

### Архитектура

`src/bmad_orchestrator/runtime/sandbox.py`:

| Класс | Назначение |
|---|---|
| `Sandbox` (Protocol) | Backend-агностичный интерфейс: `wrap_command(cmd, *, worktree, readonly_paths, network, env) → list[str]` |
| `BwrapSandbox` | `bwrap`-backed impl. Default policy ниже |
| `NoSandbox` | Fallback. Возвращает `cmd` без изменений + loud audit warning |
| `detect_sandbox()` | Factory: `BMAD_SANDBOX` env override → `shutil.which("bwrap")` → fallback |

### Default bwrap policy

```text
bwrap \
  --die-with-parent --new-session \
  --ro-bind / / \            # entire host FS read-only
  --proc /proc --dev /dev \  # minimal proc/dev
  --tmpfs /tmp \             # fresh empty /tmp per worker
  --bind {worktree} {worktree} --chdir {worktree} \  # writable: worktree only
  --unshare-pid --unshare-uts --unshare-ipc --unshare-cgroup-try \
  --unshare-net \            # network="none" (default) — drops netns
  --clearenv \               # NO host env inheritance
  --setenv K V ...           # only allow-listed env vars forwarded
```

Allow-list для `--setenv`: `PATH HOME USER LANG LC_ALL TZ PWD SHELL TERM` +
caller-supplied (e.g. `ORCHESTRATOR_WORKER_STORY_ID`). Никаких
`ANTHROPIC_API_KEY` / `TELEGRAM_*` / `*_TOKEN` — physically не наследуются.

### Wiring points

- `runtime/worker_spawn.py::spawn_worker(use_sandbox=True, sandbox_network="github_only")` — wraps `claude -p /bmad-auto-dev`. Audit event записывает `sandbox_used` + `sandbox_kind`.
- `agent/tools/retro.py::spawn_retro_worktree` — wraps `claude -p /bmad-retrospective`. Default `network="none"` (retro работает с локальными артефактами).

### Network policy

- `"none"` (default) — `--unshare-net`. Worker полностью без сети.
- `"full"` — share host netns. Используется когда worker должен делать `git clone` / `pip install`.
- `"github_only"` — currently treated as `"full"`. nftables-based whitelist (только GitHub + Anthropic endpoints) — **deferred** к v1.

### `_scan_bash` роль после FS7

`agent/safety/hooks.py::_scan_bash` — **defence-in-depth**, не primary:

- Catch'ит known patterns (rm -rf, git push --force, subshells, `git -c core.hooksPath=...`, etc).
- Audit severity = `info` если sandbox активен (вместо `warning`).
- Новые bash bypass'ы НЕ добавлять сюда unless они также bypass'ят sandbox.
- Если bypass'ит sandbox — fix в `runtime/sandbox.py`, не в scanner.

### Fallback behaviour

Если `bwrap` отсутствует:

1. `log.error()` warning.
2. Audit event `sandbox_unavailable` с `severity="warning"`.
3. `NoSandbox` возвращается → worker запускается без OS-level изоляции.
4. `_scan_bash` остаётся как только slabый барьер.
5. Prod deployment должен иметь `bubblewrap` package установлен (`apt install bubblewrap`).

### Override

`BMAD_SANDBOX=none` — force disable (для CI без bwrap или для отладки).
`BMAD_SANDBOX=bwrap` — force bwrap (fails-soft к NoSandbox если binary missing + audit).

### Limitations / deferred (FS7-A..FS7-E fast-follows)

- `.env`-style files в `/home/...` visible read-only через `--ro-bind /`. Sandbox защищает от **write** outside worktree, но не от **read** — secret hygiene должен обеспечиваться tooling'ом (FS1 secret scrubbing audit log).
- nftables whitelist для `network="github_only"` — deferred.
- Seccomp filter для дополнительной syscall restriction — deferred.
- Multi-process orchestrator (несколько orchestrator daemon'ов на одной DB) — out of scope.

### Tests

`tests/test_fs7_sandbox.py` — 32 теста:
- Abstraction unit tests (Protocol, wrap_command flags, validation).
- Factory tests (detect_sandbox priority, audit emission on fallback).
- Real-bwrap PoC tests (skip-if-no-bwrap): worker не пишет в /etc, пишет в worktree, network unreachable, NC1/NC2/NC4 bypass'ы blocked на FS уровне, env isolation verified.

---

**End of consolidated spec v0.9** — Источник истины для scaffold'а.
**Changelog:**
- **v0.9 (2026-05-16, FS7):** §22.7 Sandbox layer — `runtime/sandbox.py` (bwrap-backed) primary safety для worker subprocess. `_scan_bash` demoted до defence-in-depth. 32 new sandbox tests, all 573 PASS.
- **v0.8 (2026-05-16):** §22 Session Plan — 8 сессий для /auto-loop-spec-long bootstrap.
- **v0.7 (2026-05-16):** §21 Story Splitting (Stage 3.6 pre-split check) — +40pp first-try PASS, 2-4× wall-clock. §15.8 Voice control через Whisper local. §3 capabilities 14→16. §19 12-й skill `story-splitter`. §4 events `+voice_message_received`, `+story_split_triggered`. Pipeline 11→12 stages. §11 stack +openai-whisper. §10 MVP scope IN: voice + splitting heuristic. §17 +2 tools (`check_should_split`, `split_story`).
- **v0.6 (2026-05-16):** §19 11-й skill `proactive-improver` (closes gap «накапливает знания но не предлагает применять»). §4 event `monthly_review_scheduled`. §6.1 mandatory proactive push после каждого retro.
- **v0.5 (2026-05-15):** §20 Related Work (BAD comparison + cherry-picks). Path fix `_bmad/` → `_bmad-output/` (BMad-canonical). Story ID slug format `1-2-tenant-signup`. StoryStatus 5-state enum. MAX_PARALLEL=3. Pipeline 9→11 stages (ATDD + test-review).
- **v0.4 (2026-05-15):** §14-19 (UX, Telegram, Models, Operational tools, Industry refs, Internal skills).
- **v0.3:** §6.1 mandatory retros hard gates.
- **v0.2:** consolidated после handoff §3.
