# Research-Compare: BMad Auto-Dev Agent — Stack, SDK, Best Practices

**Дата:** 2026-05-15
**Scope:** L (архитектурный паттерн, влияет на весь проект)
**Цель:** выбрать SDK / фреймворк / паттерны для качественного BMad auto-dev агента, ничего не упустить
**Источники:** 3 параллельных субагента (Anthropic SDK best practices · конкуренты · фреймворки) + context7 (`anthropic-sdk-python` + `claude-agent-sdk-python`) + spec проекта

---

## §0 Coverage Pre-flight (L → минимум 5 items)

| # | Type | Что не покрыто | Если важно — verify-by |
|---|------|----------------|------------------------|
| CG1 | Coverage | Не сравнивали LangChain Agents (классические), Haystack Agents, MetaGPT, ChatDev | если кому-то знакомо лучше — отдельный 2-й проход; для нашего стиля «subprocess + DAG» эти 4 явно нерелевантны (chat-centric, GPT-first) |
| CG2 | Coverage | Не аудитили `bmad-auto-dev` skill в `~/.claude/skills/` — не знаем точно какие tools он использует | Read `~/.claude/skills/bmad-auto-dev/SKILL.md` перед началом Phase 1 pilot |
| VD1 | Verify | Claim «multi-agent burns ~15× tokens» — confirmed Anthropic engineering blog, но **для research, не для code-gen**. Для code-gen ratio может быть другим | мини-эксперимент на pilot Wave 1a: 1 sequential worker vs 3 parallel — сравнить total tokens |
| VD2 | Verify | Model IDs `claude-opus-4-7` / `claude-sonnet-4-6` — в spec'е zafixированы, но context7 snapshot'ы показывают `claude-opus-4-6` / `claude-sonnet-4-5-20250929` | `curl /v1/models` перед первым запуском, hardcode правильную snapshot-string |
| VD3 | Verify | 1-hour TTL prompt caching без beta-header — заявлено доками, но Bedrock/Vertex могут не поддерживать | irrelevant для нашего setup (direct Anthropic API), но если когда-то будем переходить на Vertex — re-check |
| VD4 | Verify | Pydantic-AI cache-budget auto-eviction в v1.96 — упомянуто в research, но в context7 не было снимка библиотеки | прочитать `https://github.com/pydantic/pydantic-ai/blob/main/docs/models/anthropic.md` перед интеграцией elicitation engine |
| VD5 | Verify | LangGraph `Send` API + `SqliteSaver` perf на 100+ stories в DAG — не измерено на нашем профиле | вручную: nominal load test с моками после Phase 3 MVP |
| CG3 | Coverage | Memory tool (beta `context-management-2025-06-27`) — не аудитили реальную имплементацию storage backend (FS vs SQLite vs S3) | irrelevant до Phase 5 multi-project queue; в Phase 3 MVP плоский `.claude/memory/` достаточен |

8 items (минимум для L = 5; превышено для перестраховки).

---

## Текущее состояние в проекте

**Greenfield-репозиторий:** только spec (`spec/spec_master_orchestrator.md`), пустой `src/bmad_orchestrator/__init__.py`, пример elicitation policy, `pyproject.toml` с dependencies (`anthropic>=0.40`, `claude-agent-sdk>=0.1`, `networkx`, `pydantic`, `typer`, `gitpython`, `structlog`).

**Решения уже зафиксированы спецификацией:**
- Master orchestrator на raw Anthropic SDK (Opus 4.7)
- Workers через **subprocess `claude -p`** (НЕ через `ClaudeSDKClient` напрямую) — каждый в `git worktree`
- Prompt caching обязателен, target hit rate ≥80%
- DAG + mutex на shared files, parallelism cap (default 3)
- Merge-gate через `bmad-code-review` + опц. `bmad-security-review`
- Cost budget hard-cap + human checkpoints каждые 10 stories

Этот research-compare **уточняет** выбор технологии для layer'ов оркестратора (master / elicitation / DAG / scheduling), не переделывает базовую архитектуру.

---

## Найдено в проекте: 4 артефакта

| # | Артефакт | Файл | Роль |
|---|----------|------|------|
| 1 | Architecture spec | `spec/spec_master_orchestrator.md` (679 строк) | Источник истины. §7 SDK Choices, §4 Architecture, §9 Safety Gates |
| 2 | Elicitation policy example | `examples/elicitation-policy.example.yaml` (169 строк) | Шаблон для harvest в Phase 2 |
| 3 | Dependencies declaration | `pyproject.toml` (88 строк) | Hatchling + ruff + mypy strict + pytest-asyncio |
| 4 | Project rules | `CLAUDE.md` (~140 строк) | Autonomy, commit discipline, stack defaults |

---

## Альтернативы из интернета (10 SDK best practices + 10 конкурентов + 8 frameworks сравнено, ~26 verified)

### 10a. SDK best practices — закреплены через context7 + Anthropic docs (2025-2026)

| # | Practice | Verify | Применение для bmad-orchestrator |
|---|----------|--------|-----------------------------------|
| 1 | `cache_control: {"type":"ephemeral", "ttl":"5m" \| "1h"}` на блоках tools→system→messages, до 4 breakpoints | ✅ context7 + docs | Кэшировать: CLAUDE.md target + epics.md + architecture.md (~30K токенов) с ttl="1h" на старте сессии, текущая story с "5m" |
| 2 | Tool loop: `stop_reason: tool_use → execute → append tool_result → resend`, exit на `end_turn / pause_turn / max_tokens / refusal` | ✅ Anthropic docs | Использовать в Merge Gate (adversarial review) и DAG planner — там нужны custom tools |
| 3 | `beta_tool` decorator + `client.beta.messages.tool_runner(...)` — готовый agent loop | ✅ context7 anthropic SDK | Для DAG planner: одноразовый вызов с custom tools `load_story_file`, `parse_frontmatter`, `add_dag_edge` — НЕ городить свой loop |
| 4 | Extended thinking: `thinking={"type":"adaptive", "display":"summarized"}` (4.5/4.6+) ИЛИ `{"type":"enabled", "budget_tokens":N}` (мин 1024) | ✅ context7 + docs | Включить **только** для DAG planner и merge-gate adversarial. Для elicitation engine — отключить (одношаговое решение). Сохранять `thinking` блоки в истории — иначе signature broken |
| 5 | Memory tool (beta `context-management-2025-06-27`) — клиент исполняет команды, storage owned by us | ⚠️ beta, schema может измениться | Defer до Phase 5; в MVP — плоский `.claude/memory/` файл-based |
| 6 | Multi-agent: supervisor Opus + workers Sonnet даёт +90% vs single, но 15× токенов | ✅ Anthropic engineering blog | Уже в spec'е — confirmed by industry. Hard cap `max_budget_usd` обязателен |
| 7 | Streaming **обязателен** для requests >10 мин (большой `thinking` + длинный tool loop) | ✅ Anthropic docs | Worker subprocess — всегда streaming через stdout JSONL. Supervisor short calls — non-streaming ок |
| 8 | Headless `claude -p --output-format stream-json --verbose --include-partial-messages` → NDJSON с типами `system / assistant / tool_use / tool_result / result` | ✅ Anthropic docs | Сразу проектируем JSONL parser под этот формат, не свой |
| 9 | Hook exit codes: **0** success, **2** block (stderr feeds back to Claude), other = warning. JSON-stdout для `permissionDecision: allow/deny/ask` | ✅ Claude Code docs | В worker'ах — hook `PreToolUse(Bash)` блокирует `git push --force`, `rm -rf`, `--no-verify`. Layer 1 из 3-layer safety |
| 10 | Error taxonomy: `429 rate_limit_error` (honor `retry-after`) vs `529 overloaded_error` (heavy backoff, route to fallback model). Cached tokens **не** считаются в ITPM | ✅ Anthropic docs | `ClaudeAgentOptions(fallback_model="claude-haiku-4-5-...")` обязательно. SDK сам делает backoff — не оборачивать |

### 10b. Конкуренты — 10 проанализированы, паттерны для steal

| # | Конкурент | Главный steal-this | Применение |
|---|-----------|---------------------|------------|
| 1 | **Devin 2.0** | Replay timeline как first-class artifact | Worker эмитит deterministic event log в JSONL → возможность re-merge / re-run failed story |
| 2 | **Cursor 2.0** | Worktree-per-worker + "N parallel attempts, pick best" UI | Уже worktree в spec; "pick best" — оставить как опциональный CLI flag для hard stories |
| 3 | **Cline** | Plan→Act separation (Plan mode = no Edit/Write/Bash) | Перед dev-story worker запускает read-only план в `story-plan.md`, supervisor его аппрувит дешёвой LLM-проверкой |
| 4 | **Aider** | **PageRank-ranked repo map** через tree-sitter | DAG planner получает компактный repo map вместо embeddings RAG — дешевле и проверенно |
| 5 | **OpenHands** | **CodeAct** + **AgentSkills library** | Worker не вызывает raw bash — вместо этого typed commands: `run_tests`, `apply_patch`, `read_story`, `submit_for_review` |
| 6 | **SWE-agent** | **ACI principle** — кастомные команды beat raw shell | Воркер харнесс = 6-10 curated tools, не `bash` + 30 cd/ls/cat шагов |
| 7 | **Claude Code** | Hooks как **детерминистичные gates** + Skills progressive disclosure | PreToolUse hook на каждом worker'е блокирует `git push` пока ruff+mypy+pytest не PASS |
| 8 | **Copilot Coding Agent** | "Proposed specification" артефакт перед planning | Worker эмитит one-page "what this story will produce" — дешёвый checkpoint для human approval |
| 9 | **Replit Agent 3** | Auto-screenshot running app как evidence | Для UI-touching stories — Playwright screenshot прикладывается к merge request (актуально если pilot Odyssey имеет UI epics) |
| 10 | **Bolt / v0 / Lovable** | (anti-pattern) single-turn generator не масштабируется | Не наш use case — skip |

### 10c. Frameworks — 8 сравнено (плюс 2 stale-flagged ниже)

| Framework | Last release | DAG+parallel+mutex | Subprocess JSONL | Anthropic caching | Checkpoint/resume | Cost tracking | Pydantic v2 first-class | Lock-in escape |
|-----------|--------------|--------------------|-----------------|--------------------|--------------------|---------------|------------------------|----------------|
| `claude-agent-sdk` 0.2.82 | 2026-05-15 | N (linear) | Y | Y (built-in) | P (session resume) | Y (`max_budget_usd`) | Y | **Y (zero lock-in)** |
| `langgraph` 1.2.0 | 2026-05-12 | **Y (Send API)** | Y | P (manual) | **Y (Sqlite/PG/Redis)** | P (callbacks) | Y | P (abstraction в дороге) |
| `pydantic-ai` 1.96 | 2026-05-15 | P (`pydantic-graph`) | Y | **Y (`anthropic_cache=True`)** | P (beta) | **Y (`result.usage` w/ cache)** | **Y (raison d'être)** | **Y (model-agnostic)** |
| `smolagents` 1.25 | 2026-05-14 | N | Y | P | N | N | N | Y |
| `crewai` 1.14 | 2026-04-30 | P (no DAG) | Y | P | P | P | P | P |
| `autogen-agentchat` 0.7.5 | 2025-09-30 | P (GroupChat) | Y | P | P | P | Y | P |
| `openai-agents` 0.17 | 2026-05-12 | N | Y | N | N | P | Y | P |
| `agentscope` 1.0 | 2026-05-15 | P (workflow graph) | Y | P | P | P | Y | P |

**Stale flagged:** `pyautogen` 0.10 (10 мес), `swarm` 0.0.2 (мёртв с 2018, replaced by `openai-agents`).

---

## Сравнительная таблица: рекомендованный stack vs spec-baseline

| Layer | Spec v0.1 | Research-compare рекомендация | Δ обоснование |
|-------|-----------|-------------------------------|----------------|
| Worker (subprocess `claude -p`) | asyncio + subprocess | **= то же самое** | Spec прав. Любая обёртка (CrewAI tool, LangGraph node-as-worker) скрывает JSONL `cache_read_input_tokens` и hooks |
| Master loop (DAG, scheduling, mutex, resume) | raw asyncio + manual checkpointing | **LangGraph 1.2 `StateGraph` + `Send` API + `SqliteSaver`** | Reinventing checkpointer = 2-3 недели работы которая сломается. LangGraph `interrupt()` маппится 1:1 на elicitation 80/20 |
| LLM decisions (DAG planner, elicitation, merge-gate) | raw `anthropic` SDK | **Pydantic-AI 1.96 + `AnthropicModelSettings(anthropic_cache=True)`** | Typed structured outputs (pydantic v2 native), cache-tokens в `result.usage`, drop-to-raw в 5 минут |
| Worker harness reference | claude-agent-sdk (only as wrapper) | **= использовать только для hook taxonomy / settings_sources reference** | Сам worker = CLI subprocess, не Python embed |
| DAG ops | networkx | **= то же самое** | Spec прав |
| CLI | typer + rich | **= то же самое** | Spec прав |
| Telemetry | structlog | **= то же самое + ResultMessage.usage parsing** | Spec прав, расширить парсинг кэш-метрик |

**Итог:** **3 дополнительные deps** (`langgraph` + `langgraph-checkpoint-sqlite` + `pydantic-ai[anthropic]`), удалить ничего. Worker layer **не трогать**.

---

## Step 4.5: Anti-Gap Check

Каждый «нужно добавить» проверен:

1. **LangGraph вместо ручного asyncio?** — Можно ли решить существующим? Нет: spec предполагал «mutex implementation: в orchestrator memory, NOT файловые locks» + ручной resume. Это и есть «handroll checkpointer» — gap легит.
2. **Pydantic-AI вместо raw `anthropic`?** — Можно ли? Да, можно raw. **НО** структурированные outputs (DAG edges, ElicitationResolution, MergeGateResult — все уже Pydantic models по §5.2 spec) требуют parsing JSON-from-text → Pydantic-AI делает это типобезопасно out-of-box. Gap легит.
3. **PageRank repo map (Aider) — отдельная фича?** — Можно ли existing `bmad-create-story` skill достать context? Не покрывает: BMad story файлы статичны, не привязаны к актуальной структуре кода в worktree. Gap легит (но low priority — Phase 4+).
4. **claude-agent-sdk дублирует raw anthropic?** — Унифицирует ли? Да: hook system + `permission_mode` + `max_budget_usd` — это паттерны которые мы будем переизобретать. **НО**: для master orchestrator мы НЕ запускаем Claude Code embedded — only subprocess CLI. Поэтому claude-agent-sdk оставляем как **reference docs**, не runtime dep. Можно даже убрать из `pyproject.toml`.
5. **Memory tool beta — добавлять сейчас?** — Решает ли существующее? Да: per-worktree `.claude/memory/` + supervisor-level SQLite state — достаточно для Phase 3 MVP. Gap НЕ легит для текущей фазы — отложить.

**Удалено из gap-list:** Memory tool (Phase 5+), PageRank repo map (Phase 4+, опционально), claude-agent-sdk runtime dep (заменить ссылкой в docs).

---

## Step 4.6: Counter-Example Gate

| Claim | Counter-example | Где проверить | Status |
|-------|-----------------|---------------|--------|
| «LangGraph лучше чем ручной asyncio» | Может ли простой `asyncio.gather + Semaphore + manual SQLite` дать 80% выгоды за 20% сложности? | Прототип на 1-2 story Phase 3 MVP | ⚠️ Open — measure после MVP |
| «Pydantic-AI cache budget работает» | Verified только в research-subagent claim, без context7 snapshot | Прочитать `pydantic-ai/docs/models/anthropic.md` | ⚠️ VD4 в Pre-flight |
| «multi-agent 15× tokens transferable to code-gen» | Anthropic blog был про research, не code-gen | Mini-experiment Wave 1a pilot | ⚠️ VD1 в Pre-flight |
| «PageRank repo map дешевле embeddings RAG» | На BMad-проектах с готовыми story файлами roi может быть отрицательным | Опционально, после baseline | ✅ Defer to Phase 4 |
| «Worker через `claude -p` лучше чем embed `ClaudeSDKClient`» | Cold start 5s × 100 stories = 8 минут оверхеда | Acceptable, story всё равно 15+ мин | ✅ confirmed в spec |
| «Все конкуренты используют worktree-per-worker» | Cursor — да; Devin — VM; OpenHands — Docker; Cline — local | Worktree — middle ground, оправдан для Git-native flow | ✅ |
| «claude-agent-sdk не нужен в runtime» | Без него теряем `permission_mode='acceptEdits'` авто-управление | Worker запускается CLI — CLI имеет свой permission flag (`--dangerously-skip-permissions` или per-tool allowlist) | ⚠️ verify-by — прочитать `claude -p --help` |

---

## Step 5.7: Independent Review (deferred)

В греенфилде пока **нечего ревьювить кодом-аудитором**. Если бы был MVP — запустил бы `code-auditor` на DAG planner + state machine. **Action:** запустить независимый review **после** Phase 3 MVP реализации (см. Next Actions внизу).

---

## Где могу ошибиться (Step 5.8 — L: минимум 5 items)

### 1. LangGraph overkill для нашего масштаба
**Уверенность:** средняя. **Что предполагаю:** DAG из 100+ stories достаточно сложен чтобы оправдать framework. **Что НЕ проверял:** какова реальная сложность DAG на Odyssey Wave 1a (может быть 80% линейный). **Если ошибся:** ~2 недели на интеграцию LangGraph, потом выкинуть → потеря времени. **Как уменьшить:** Phase 3 MVP в spec'е == **sequential worker** — там LangGraph не нужен. Подключить только в Phase 4 parallel pool, когда need действительно подтвердится.

### 2. Pydantic-AI ещё молодая (1.96 в мае 2026)
**Уверенность:** средняя. **Предполагаю:** v1 GA → API стабильно. **НЕ проверял:** breaking changes в minor versions, реальный production-track record. **Если ошибся:** будет boilerplate миграции на v2. **Как уменьшить:** все вызовы к pydantic-ai через тонкий adapter `src/bmad_orchestrator/llm/adapter.py` — drop-down к raw `anthropic` за час.

### 3. Multi-agent ratio в research blog ≠ ratio для code-gen
**Уверенность:** низкая. **Предполагаю:** 15× — upper bound. **НЕ проверял:** реальные числа на coding tasks. **Если ошибся:** cost budget hard-cap сработает раньше чем ожидается, will trigger frequent halt. **Как уменьшить:** Phase 1 pilot **обязательно** измеряет tokens-per-story baseline до Phase 4 parallel.

### 4. Stack compatibility — Python 3.11 vs newer features
**Уверенность:** высокая. **Предполагаю:** все 6 deps работают на 3.11. **НЕ проверял:** требуют ли некоторые pydantic-ai features 3.12+. **Если ошибся:** upgrade на 3.12 — несколько часов. **Как уменьшить:** `python -c "import langgraph, pydantic_ai, anthropic"` smoke test после `pip install`.

### 5. Model IDs hardcoded в CLAUDE.md ≠ реальный API
**Уверенность:** средняя. **Предполагаю:** `claude-opus-4-7` и `claude-sonnet-4-6` существуют. **НЕ проверял:** model ID через `/v1/models`. **Если ошибся:** runtime 404. **Как уменьшить:** в `src/bmad_orchestrator/config.py` — `models = {"planner": os.environ.get("PLANNER_MODEL", "claude-opus-4-7"), ...}` + sanity-check на старте через `client.models.list()`.

### 6. Worker через CLI subprocess vs embedded SDK
**Уверенность:** высокая. **Предполагаю:** CLI subprocess лучше для bmad-orchestrator (изоляция, kill-able, JSONL out-of-box). **НЕ проверял:** что произойдёт при 10+ parallel CLI processes — RAM/FD limits на Linux. **Если ошибся:** OOM или `EMFILE`. **Как уменьшить:** `ulimit -n 4096` в systemd unit + memory profiling на Phase 4 pilot.

---

## Вердикт

**Рекомендация: ГИБРИД — модифицировать spec'овый stack на 3-слойную архитектуру.**

| Layer | Решение |
|-------|---------|
| Worker (claude -p subprocess) | **Оставить как есть** — spec прав |
| Master loop / DAG / scheduling | **Добавить LangGraph 1.2** + `langgraph-checkpoint-sqlite` (Phase 4, не MVP) |
| LLM decisions (planner, elicitation, gates) | **Добавить Pydantic-AI 1.96** [anthropic] — заменяет ручной `anthropic` SDK + JSON parsing |
| claude-agent-sdk runtime dep | **Убрать из `pyproject.toml`** (использовать только как reference для hook taxonomy и settings sources в docs) |
| Всё остальное (networkx, pydantic, typer, structlog, gitpython) | **Оставить** |

**Почему:** Spec выбрал правильную основу (CLI subprocess + worktree + cost cap + gates), но недооценил cost'ы handroll'инга checkpointing/resume и потерял возможность бесплатно получить type-safe LLM calls. Изменения **additive**, не breaking — раздел §7 spec'а остаётся актуальным, добавляется новый §7.5 для Pydantic-AI и §7.6 для LangGraph.

**Если мигрировать — план:**

1. **Сейчас (документация-only):** обновить `spec/spec_master_orchestrator.md` §7 SDK Choices — добавить Pydantic-AI и LangGraph в таблицу, отметить claude-agent-sdk как reference-only.
2. **Phase 3 (MVP):** реализовать `src/bmad_orchestrator/llm/` adapter поверх Pydantic-AI для всех Anthropic-вызовов. DAG planner + elicitation engine + merge-gate — через него. Master loop **остаётся** ручной asyncio (sequential, без LangGraph).
3. **Phase 4 (parallel):** ввести LangGraph для master state graph: nodes = `pick_ready_batch`, `spawn_workers`, `await_completion`, `merge_gate`, `human_checkpoint`. Checkpointer = `SqliteSaver('state.db')`. `interrupt()` для elicitation escalation.
4. **Phase 5 (production hardening):** добавить Memory tool (beta) для cross-wave обучения supervisor'а. Подключить PageRank repo map (опционально).

**Steal-this паттерны, которые внедряем независимо от выбора framework:**

| # | Откуда | Что | В каком файле |
|---|--------|-----|----------------|
| 1 | SWE-agent + OpenHands | Curated ACI: 8 typed worker commands (`read_story`, `apply_patch`, `run_tests`, `git_status`, `request_review`, `escalate`, `report_done`, `read_repo_map`) вместо raw bash | `src/bmad_orchestrator/worker/aci.py` |
| 2 | Cline | Plan-only mode (read-only tools) перед dev-story | `bmad-auto-dev` skill modification — flag `--plan-first` |
| 3 | Claude Code | Hooks as gates: PreToolUse(Bash) deny на `git push --force`, `rm -rf`, `--no-verify` | `_bmad/_config/orchestrator-hooks.yaml` в target проекте |
| 4 | Devin | Replay timeline — NDJSON событий per-story в `_bmad/_runs/wave-1a/story-X.Y.events.jsonl` | `src/bmad_orchestrator/telemetry.py` |
| 5 | Copilot Coding Agent | One-page «proposed spec» артефакт от worker'а до edit | Часть Plan-mode output: `story-plan.md` |
| 6 | Aider | Tree-sitter repo map + PageRank ranking | `src/bmad_orchestrator/repo_map.py` (Phase 4+) |

**Риски:**

- LangGraph minor-version breaking change → mitigate тонким adapter layer.
- Pydantic-AI cache budget не работает как заявлено → fallback на raw `anthropic` с ручным `CacheControlEphemeral` placement.
- Worker subprocess RAM/FD limit → `ulimit` + monitoring + max_parallel=3 cap.
- Memory tool schema breakage до GA → defer полностью до Phase 5.

---

## Next Actions

1. **Обновить spec** (`spec/spec_master_orchestrator.md` §7) — добавить Pydantic-AI + LangGraph, пометить claude-agent-sdk как reference.
2. **Обновить `pyproject.toml`** — добавить `langgraph>=1.2`, `langgraph-checkpoint-sqlite`, `pydantic-ai[anthropic]>=1.96`. Убрать `claude-agent-sdk`.
3. **Создать adapter** `src/bmad_orchestrator/llm/adapter.py` (Phase 3 MVP старт).
4. **Verify VD2** — реальные model IDs через `claude models list` или `/v1/models`.
5. **Verify VD4** — прочитать pydantic-ai Anthropic caching docs до начала кодинга.
6. **Phase 1 pilot обязательно измеряет**: tokens-per-story, parallel-vs-sequential ratio (VD1), cache hit rate.
7. **Аудит `bmad-auto-dev` skill** (CG2 в Pre-flight) — Read `~/.claude/skills/bmad-auto-dev/SKILL.md` (или target-local в `/home/server/odyssey-ux/.claude/skills/`) перед Phase 1 pilot; зафиксировать какие tools он использует, чтобы worker harness ACI был совместим.
