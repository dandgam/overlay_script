# Spec — Wave 1a Pilot Wiring (Real-mode Event Loop)

**Дата:** 2026-05-16
**Версия:** 0.1
**Базовая ветка:** `main` (post-merge 4934b12 — orchestrator MVP + 5 rounds security)
**Backup branch:** `backup/wave_1a_pilot_wiring-pre-2026-05-16`
**Integration branch:** `integration/wave_1a_pilot_wiring`
**Auto merge:** false (длинная инициатива, ≥4 сессии — финальный merge через human review)

---

## 1. Контекст

MVP scaffold S1-S8 + 4 раунда security fixes завершены и слиты на `main` (4934b12). 613 tests PASS, ruff/mypy clean. Все ингредиенты production-mode'a structurally готовы:

- DAG planner (`runtime/dag_planner.py`) — извлекает ready stories из target's `sprint-status.yaml`.
- Worker spawn (`runtime/worker_spawn.py`) — sandboxed `claude -p /bmad-auto-dev` subprocess в worktree; JSONL event stream; mock fallback при отсутствии binary.
- Event loop (`runtime/event_loop.py`) — 14 event types, backstop wakeup, FIFO consumer.
- Budget guard (`agent/safety/budget_guard.py`) — atomic `enforce_and_reserve_story` под SQLite lock + daily cap (FS6 N2, FS8 NH2).
- StateDB cross-process binding (`state/db.py` + `_resolve_session` в `agent/run.py`) — shared session id между bot и agent (FS8 NH1).
- Sandbox (`runtime/sandbox.py`) — bwrap primary + prlimit rlimits (FS7, FS9). `_scan_bash` demoted до defence-in-depth.
- Token usage / pricing (`runtime/budget.py::usd_cost`) — Decimal-precision Anthropic 2026 pricing для всех 3 моделей с cache_read/cache_write tiers.
- Anthropic Messages API client готов через `claude-agent-sdk >=0.2.82` (pyproject.toml:276).

**Но:** `agent/run.py::run_orchestrator(mock=False)` на line 152 raises `NotImplementedError("real mode requires Wave 1a pilot wiring; use --mock for now")`. Это сознательный B1 blocker от FS4 — silent no-op был запрещён. Сейчас задача — реализовать full event-driven loop под real binary.

Параллельные deferred wirings:
- `agent/run.py::human_query_subscriber` (line 506-540) emits stub HUMAN_RESPONSE без вызова Anthropic API — intent-router skill body загружается но не диспатчится.
- Cost watchdog в `_run_mock_pilot` (line 449) использует `mock spend ≈ $5/story` вместо реального parsing JSONL `usage` blocks.
- Auto-merge на `integration/<wave>` ветку с code-review gate'ом не реализован — worker завершается и его коммит остаётся orphaned в worktree.
- Telegram bot (`bot/main.py`) подключается к event bus через session-id env var, но `USER_CHAT_MESSAGE` flow не доходит до реального LLM dispatch.

После этой инициативы оркестратор сможет запустить настоящий Wave 1a pilot на `/home/server/odyssey-ux/` (16 эпиков, 133 FR в planning artifacts).

---

## 2. Принципы

- **Real binary через сэндбокс — primary safety**. `BMAD_REQUIRE_SANDBOX=1` обязательно в production-env. `--unshare-net` по default (FS9 H2); pilot caller передаёт `sandbox_network="full"` явно для git операций.
- **Cost-watchdog real polling, no estimates**. Parsing JSONL `usage` blocks от worker'а → exact Decimal cost. `enforce_and_reserve_story` BEFORE spawn (C5 round 2 паттерн остаётся).
- **Auto-merge only через gate**. После `worker_completed` → spawn `bmad-code-review` skill в той же worktree → parse verdict → fast-forward merge `feature/<story>` → `integration/<wave>` при approve; на `request_changes` → emit HUMAN_QUERY, story остаётся в worktree.
- **Halt-on-fail, fail-loud**. Любой DB / SDK / sandbox / merge error в production raises вместо silent fallback. Mock-path в тестах остаётся (CI без binary / API key).
- **Prompt caching по default**. `intent-router` skill body загружается как cached system block. Cache hit rate <50% = bug (CLAUDE.md project-level rule).
- **Никаких разрушающих ops без подтверждения.** `--no-verify`, `--force`, `reset --hard` запрещены кроме explicit human ack в этой инициативе.

---

## 3. Stack / Constraints

- Python 3.11+, `asyncio` (no threads).
- `claude-agent-sdk >=0.2.82` уже в pyproject.toml — используем для real-mode SDK options + Messages API client; mock через monkeypatch в тестах.
- `aiosqlite` для StateDB binding — уже wired через `_resolve_session`.
- `gitpython` для fast-forward merge — уже в deps.
- Сохранить все 613 PASS из main + добавить ~80-120 тестов (распределено по 5 сессиям).
- `ruff check` + `mypy --strict` зелёные на каждой сессии.
- Не модифицировать `runtime/sandbox.py`, `runtime/worker_spawn.py`, `agent/safety/budget_guard.py` (security-critical, freeze после round 5) кроме каллер-side кода в `agent/run.py`.

---

## 4. Session Plan

5 сессий, surface=`backend-python` для всех, code-only (нет destructive_actions). Каждая ≤500 LOC core + tests. Дefault delay 300s между sessions (стандарт проекта).

### W1 — Real-mode event loop core (CHECKPOINT)

- **surface:** backend-python
- **spec_section:** lines 100-180 ниже
- **depends_on:** []
- **destructive_actions:** []
- **checkpoint:** true
- **acceptance:**

  **W1.1 — Replace `raise NotImplementedError` в `agent/run.py:152`**

  Реализовать `async def _run_real_pilot(bus, *, project, wave, max_parallel, max_stories, max_spend_usd, budget, state_db, session_id, models, options)`:
  1. Build DAG: `DagPlanner.from_target()` — same as mock pilot.
  2. Loop: пока есть ready stories AND `len(spawned) < max_stories` AND `daily_spent < max_spend_usd` AND нет `BUDGET_THRESHOLD_HIT(halt)` в bus history:
     - `ready = [s for s in planner.find_ready(max_n=max_parallel * 2) if s["id"] not in spawned]`
     - `batch = ready[:max_parallel]`
     - Для каждой story в batch: `await budget.enforce_and_reserve_story(...)`, если allowed — `await runtime_spawn_worker(worktree, story_id, branch, mock=False, sandbox_network="full")`.
     - После batch'а: `await asyncio.gather(*[tail_jsonl_events(h.jsonl_path) → emit WORKER_COMPLETED on terminal event])`. Tail-tasks эмитятся в bus.
     - `planner.reload()` после применения completion'ов к `sprint-status.yaml`.
  3. Backstop: `bus.start_backstop_task()` каждые 300s emits SCHEDULED_WAKEUP — гарантирует завершение даже при тихих workers.
  4. Завершение: emit `WAVE_BOUNDARY_REACHED` когда DAG исчерпан ИЛИ daily cap reached.

  **W1.2 — CLI flags `--max-stories N` + `--max-spend-usd N`** (`cli/main.py:104`)

  Добавить в `def run(...)`:
  ```python
  max_stories: int = typer.Option(50, "--max-stories", help="Hard cap на число story spawns в этом запуске (default 50)"),
  max_spend_usd: float = typer.Option(50.0, "--max-spend-usd", help="Soft cap на дневной spend в USD (default 50.0)"),
  ```
  Прокинуть в `run_orchestrator(..., max_stories=..., max_spend_usd=...)`. Update help text `--mock/--real` — убрать «currently raises NotImplementedError until Wave 1a».

  **W1.3 — `BMAD_REQUIRE_SANDBOX=1` production guard в real path**

  В начале `_run_real_pilot` вызвать `detect_sandbox()` — если возвращает `NoSandbox` AND `os.environ.get("BMAD_REQUIRE_SANDBOX") in ("1", "true", "yes")` → raise `RuntimeError` (loud). Mock path не trip'ает guard.

  **Tests:** `tests/test_w1_real_pilot.py` — ~25 тестов:
  - Real pilot spawns N workers под fake claude binary (PATH shim в conftest), tail_jsonl_events emits WORKER_COMPLETED.
  - `--max-stories=2` blocks третий spawn даже если DAG ready.
  - `--max-spend-usd=10` halts когда daily reserve пересекает cap.
  - `BMAD_REQUIRE_SANDBOX=1` + no bwrap → RuntimeError.
  - Mock path unchanged (existing 613 tests green).

  **Grep validation:**
  - `grep -c "raise NotImplementedError" src/bmad_orchestrator/agent/run.py` == 0 (после fix).
  - `grep -c "async def _run_real_pilot" src/bmad_orchestrator/agent/run.py` == 1.
  - `grep -c "max-stories\|max-spend-usd" src/bmad_orchestrator/cli/main.py` ≥ 2.

  **LOC budget:** ~350 core + ~150 tests.

  **Definition of done:**
  - `pytest tests/test_w1_real_pilot.py -v` — 25 PASS.
  - `pytest tests/ -q` — 613+25 = 638 PASS.
  - `ruff check src tests` clean.
  - `mypy --strict src` clean.
  - `bmad-orchestrator run --project odyssey --wave 1a --real --max-stories 0` — exits 0 без ошибок (dry-run path).

---

### W2 — Intent-router LLM dispatch (CHECKPOINT)

- **surface:** backend-python
- **spec_section:** lines 200-280
- **depends_on:** [W1]
- **destructive_actions:** []
- **checkpoint:** true
- **acceptance:**

  **W2.1 — Wire `human_query_subscriber` к Anthropic Messages API** (`agent/run.py:506`)

  Заменить `log.warning("human_query_intent_router_deferred", ...)` + stub echo на real dispatch:
  1. Load skill body: `body = load_skill_body("intent-router")` — уже работает.
  2. Build messages:
     ```python
     system_blocks = [
         {"type": "text", "text": INTENT_ROUTER_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
         {"type": "text", "text": body, "cache_control": {"type": "ephemeral"}},
     ]
     messages = [{"role": "user", "content": payload["text"]}]
     ```
  3. Call Claude через SDK:
     ```python
     from anthropic import AsyncAnthropic
     client = AsyncAnthropic()  # picks up ANTHROPIC_API_KEY from env
     response = await client.messages.create(
         model=models.routine,  # haiku — intent classification = cheap
         max_tokens=512,
         system=system_blocks,
         tools=[t for t in ALL_TOOLS if t.name in {"start_wave", "stop_orchestrator", "escalate_to_human", "read_sprint_status"}],
         messages=messages,
     )
     ```
  4. Parse response.content: если есть `tool_use` block — диспатчить через `mcp_server.handle_tool_call(tool_name, tool_input)` (claude-agent-sdk API); если только `text` — emit `HUMAN_RESPONSE` с `text=response.content[0].text`.

  **W2.2 — Prompt-caching invariant**

  Прочитать `response.usage.cache_read_input_tokens` после первого вызова в той же сессии — должно быть >0 (cache hit). Log structured: `log.info("intent_router_dispatched", cache_read_tokens=..., input_tokens=..., output_tokens=..., cost_usd=...)`. Test asserts cache_read >0 на втором вызове.

  **W2.3 — Cost accounting**

  Apply `usd_cost(models.routine, TokenUsage(...))` → `budget.attribute_usd(scope="intent_router", spent=cost_usd)`. Если `budget.cfg.daily_limit_usd` пересекается — emit `BUDGET_THRESHOLD_HIT(halt)`, return stub response, не делать second call.

  **Tests:** `tests/test_w2_intent_router.py` — ~20 тестов:
  - Real dispatch routes «запусти wave 1a» → tool_use block `start_wave({"wave": "1a"})`.
  - Cache hit on second call (`cache_read_input_tokens > 0`).
  - Daily cap halt blocks dispatch + emits BUDGET_THRESHOLD_HIT.
  - Stub mock path (no ANTHROPIC_API_KEY) emits old-style HUMAN_RESPONSE без crash'а.

  **Mock strategy:** monkeypatch `anthropic.AsyncAnthropic` через `respx` или ручной class stub (lookup AsyncAnthropic в conftest, провайдим `messages.create` async function возвращающую Mock response).

  **Grep validation:**
  - `grep -c "AsyncAnthropic\|client.messages.create" src/bmad_orchestrator/agent/run.py` ≥ 1.
  - `grep -c "cache_control" src/bmad_orchestrator/agent/run.py` ≥ 1.
  - `grep -c "intent_router_dispatched" src/bmad_orchestrator/agent/run.py` ≥ 1.

  **LOC budget:** ~250 core + ~120 tests.

  **Definition of done:**
  - `pytest tests/test_w2_intent_router.py -v` — 20 PASS.
  - `pytest tests/ -q` — 638+20 = 658 PASS.
  - ruff/mypy clean.
  - Real smoke (если `ANTHROPIC_API_KEY` в env): «привет» → HUMAN_RESPONSE с ненулевым text + cache_read >0 на втором вызове.

---

### W3 — Cost watchdog real polling (CHECKPOINT)

- **surface:** backend-python
- **spec_section:** lines 300-360
- **depends_on:** [W1]
- **destructive_actions:** []
- **checkpoint:** true
- **acceptance:**

  **W3.1 — Parse `usage` blocks из worker JSONL** (`runtime/worker_spawn.py` consumers, не сам spawn)

  Worker subprocess эмитит `claude_event` JSONL lines с `usage` payload (Anthropic CLI streams это per turn). Создать `runtime/cost_tracker.py::WorkerCostTracker`:
  ```python
  class WorkerCostTracker:
      def __init__(self, model: str) -> None:
          self.model = model
          self.cumulative = TokenUsage()

      def feed(self, event: dict) -> Decimal:
          """Returns delta cost from this event (0 if no usage)."""
          usage_dict = event.get("usage") or event.get("message", {}).get("usage")
          if not usage_dict:
              return Decimal(0)
          delta_usage = TokenUsage(
              input_tokens=usage_dict.get("input_tokens", 0),
              cache_creation_input_tokens=usage_dict.get("cache_creation_input_tokens", 0),
              cache_read_input_tokens=usage_dict.get("cache_read_input_tokens", 0),
              output_tokens=usage_dict.get("output_tokens", 0),
          )
          delta_cost = usd_cost(self.model, delta_usage)
          self.cumulative = TokenUsage(
              input_tokens=self.cumulative.input_tokens + delta_usage.input_tokens,
              cache_creation_input_tokens=self.cumulative.cache_creation_input_tokens + delta_usage.cache_creation_input_tokens,
              cache_read_input_tokens=self.cumulative.cache_read_input_tokens + delta_usage.cache_read_input_tokens,
              output_tokens=self.cumulative.output_tokens + delta_usage.output_tokens,
          )
          return delta_cost
  ```

  **W3.2 — Wire в `_run_real_pilot` event loop**

  Per worker handle: создать `tracker = WorkerCostTracker(model=models.dev)`. В tail_jsonl_events generator'е после parse каждой строки: `delta = tracker.feed(event); await budget.attribute_usd(scope=f"worker:{story_id}", spent=float(delta))`. После `worker_completed` event: log structured `log.info("worker_cost_final", story_id=..., total_usd=str(usd_cost(model, tracker.cumulative)), cache_hit_ratio=...)`.

  **W3.3 — Real story cost feed в `enforce_and_reserve_story`**

  Заменить `reserve = budget.cfg.story_alarm_usd / 6.0  # mock $5` (line 449 mock path; в real path аналогично) на adaptive `reserve = min(budget.cfg.story_alarm_usd, last_3_stories_p95_cost)`. Хранить last_3_stories_costs в `BudgetGuard` (новое поле `_recent_story_costs: deque[Decimal] maxlen=3`). При первом запуске без истории → `reserve = budget.cfg.story_alarm_usd / 2` (conservative half).

  **Tests:** `tests/test_w3_cost_tracker.py` — ~25 тестов:
  - `WorkerCostTracker.feed` parsing: SDK-style `{"usage": {...}}`, message-wrapped `{"message": {"usage": {...}}}`, missing usage → 0.
  - Cumulative accumulation across 5 events.
  - Cache_hit_ratio computation: `cache_read / (input + cache_read)`.
  - `enforce_and_reserve_story` использует adaptive reserve.
  - Synthetic JSONL stream: 3 stories, reserves адаптируются.

  **Grep validation:**
  - `grep -c "class WorkerCostTracker" src/bmad_orchestrator/runtime/cost_tracker.py` == 1.
  - `grep -c "WorkerCostTracker\|feed.*usage" src/bmad_orchestrator/agent/run.py` ≥ 2.
  - `grep -c "_recent_story_costs" src/bmad_orchestrator/agent/safety/budget_guard.py` ≥ 1.

  **LOC budget:** ~200 core + ~150 tests.

  **Definition of done:**
  - `pytest tests/test_w3_cost_tracker.py -v` — 25 PASS.
  - `pytest tests/ -q` — 658+25 = 683 PASS.
  - ruff/mypy clean.

---

### W4 — Code-review gate + auto-merge to integration (CHECKPOINT)

- **surface:** backend-python
- **spec_section:** lines 380-460
- **depends_on:** [W1, W3]
- **destructive_actions:** [«git merge feature/story → integration/<wave>»]
- **checkpoint:** true
- **acceptance:**

  **W4.1 — Spawn `bmad-code-review` skill после WORKER_COMPLETED** (новый subscriber в `agent/run.py`)

  Добавить `async def code_review_subscriber(event: Event, bus: EventLoop)`:
  1. `if event.type != WORKER_COMPLETED or event.payload.get("status") != "success": return`.
  2. Получить worktree path + story_id из payload.
  3. Spawn `claude -p /bmad-code-review` в той же worktree через `runtime_spawn_worker(..., skill_invocation="/bmad-code-review", sandbox_network="none")`.
  4. Wait for terminal event в новом JSONL stream — pattern «verdict: approve» / «verdict: request_changes» / «verdict: reject» в финальном `claude_event` блоке.
  5. Emit `CODE_REVIEW_VERDICT` event (новый EventType, добавить в event_loop.py).

  **W4.2 — Auto-merge при approve** (новый subscriber `merge_to_integration_subscriber`)

  При `CODE_REVIEW_VERDICT(verdict="approve")`:
  1. Open `git.Repo(target_project)`.
  2. `repo.git.checkout(f"integration/{wave}")` (если не существует — создать от `main`).
  3. `repo.git.merge(f"feature/{story_id}", "--ff-only", "--signoff")` — НИ В КАКОМ СЛУЧАЕ не `--no-verify` (CLAUDE.md hard rule).
  4. На MergeError (non-fast-forward, конфликт) → emit HUMAN_QUERY с diff summary, story остаётся в worktree, branch не trogan.
  5. На success: emit story_merged audit event, delete worktree через `runtime/worktree.py::cleanup_worktree(path)` (safety: verify path под `_root/.worktrees/` prefix перед удалением).

  **W4.3 — Escalation при request_changes / reject**

  Emit `HUMAN_QUERY` с payload:
  ```python
  {
      "chat_id": settings.bot.escalation_chat_id,
      "text": f"Code-review {verdict} для {story_id}:\n\n{review_summary}\n\nWorktree: {worktree_path}",
      "story_id": story_id,
      "verdict": verdict,
      "actions": ["approve_override", "abandon", "edit_in_human_loop"],
  }
  ```
  Story остаётся в worktree, branch не merg'ится, `sprint-status` story status → `under_review`.

  **Tests:** `tests/test_w4_code_review_gate.py` — ~30 тестов:
  - Subscriber spawn'ит bmad-code-review skill (через fake claude PATH shim, JSONL прединсталлен).
  - Approve → fast-forward merge в integration branch (assert `git log integration/<wave>` содержит коммит).
  - Reject → no merge, HUMAN_QUERY emitted с правильным payload.
  - Conflict → MergeError caught, HUMAN_QUERY emitted, integration branch не двинут.
  - Worktree cleanup только под `.worktrees/` prefix — попытка delete `/etc` raises (safety).

  **Grep validation:**
  - `grep -c "code_review_subscriber\|merge_to_integration_subscriber" src/bmad_orchestrator/agent/run.py` == 2.
  - `grep -c "CODE_REVIEW_VERDICT" src/bmad_orchestrator/runtime/event_loop.py` ≥ 1.
  - `grep -c '"--ff-only"' src/bmad_orchestrator/agent/run.py` ≥ 1.
  - `grep -c "no-verify\|--force\|reset --hard" src/bmad_orchestrator/agent/run.py` == 0.

  **LOC budget:** ~400 core + ~200 tests.

  **Definition of done:**
  - `pytest tests/test_w4_code_review_gate.py -v` — 30 PASS.
  - `pytest tests/ -q` — 683+30 = 713 PASS.
  - ruff/mypy clean.

---

### W5 — Telegram bot real-mode + e2e smoke test (FINAL)

- **surface:** backend-python
- **spec_section:** lines 480-560
- **depends_on:** [W2, W4]
- **destructive_actions:** []
- **checkpoint:** false (final session — финальный merge через human review)
- **acceptance:**

  **W5.1 — Bot `USER_CHAT_MESSAGE` → intent-router flow**

  В `bot/handlers.py` (текущий 582 LOC): найти `_handle_text_message` (или эквивалентный handler). Replace local echo на:
  1. `corr_id = secrets.token_hex(8); future = bus.subscribe_one_correlation(corr_id)`.
  2. `await bus.emit(EventType.USER_CHAT_MESSAGE, chat_id=update.effective_chat.id, corr_id=corr_id, text=message_text)`.
  3. `response = await asyncio.wait_for(future, timeout=60.0)`.
  4. `await update.message.reply_text(response.payload["text"])`.

  Bus интерфейс `subscribe_one_correlation(corr_id)` — добавить в `event_loop.py`: возвращает asyncio.Future, который resolve'ится при первом `HUMAN_RESPONSE` с матчинг'ом `corr_id`.

  **W5.2 — End-to-end smoke test**

  `tests/test_w5_e2e_smoke.py` — синтетический Odyssey wave с 1 story (`wt-test-1.1`):
  1. Fake target project в `tmp_path/odyssey/` с минимальным `_bmad-output/planning-artifacts/sprint-status.yaml` и одной mock story в `_bmad-output/planning-artifacts/stories/1.1.md`.
  2. Fake claude PATH shim (uses fixture from W1) returning success JSONL + bmad-code-review approve JSONL.
  3. Spawn orchestrator: `await run_orchestrator(project="test", wave="1a", mock=False, ...)` с `BMAD_SANDBOX=none + BMAD_SANDBOX_DISABLE_CONFIRMED=yes-i-accept-risk` (CI без bwrap).
  4. Assert events sequence: `worker_spawned → worker_completed → code_review_verdict(approve) → story_merged → wave_boundary_reached`.
  5. Assert integration branch имеет 1 коммит (story merge).

  **W5.3 — Production launcher docs**

  Создать `docs/production-launcher.md`:
  - Systemd unit example (Environment vars: `BMAD_REQUIRE_SANDBOX=1`, `BMAD_REQUIRE_DB_BRIDGE=1`, `ANTHROPIC_API_KEY` из secret store).
  - Pre-deployment checklist (apt install bubblewrap util-linux + StateDB init + linger для systemd user).
  - Recovery runbook: «как остановить рогающий orchestrator», «как resume после budget halt», «как поднять очередь stories после ребута».

  **Tests:** `tests/test_w5_e2e_smoke.py` — ~15 тестов (1 full e2e + 14 unit вокруг bot flow + correlation).

  **Grep validation:**
  - `grep -c "subscribe_one_correlation" src/bmad_orchestrator/runtime/event_loop.py` ≥ 1.
  - `grep -c "USER_CHAT_MESSAGE\|asyncio.wait_for.*future" src/bmad_orchestrator/bot/handlers.py` ≥ 1.
  - `test -f docs/production-launcher.md` succeeds.

  **LOC budget:** ~250 core + ~200 tests + ~150 doc lines.

  **Definition of done:**
  - `pytest tests/test_w5_e2e_smoke.py -v` — 15 PASS.
  - `pytest tests/ -q` — 713+15 = 728 PASS.
  - ruff/mypy clean.
  - E2E smoke test reliably green в CI (no flakes на 5 повторов).

---

## 5. Risks & deferred

### Risks

| Риск | Митигация |
|---|---|
| Anthropic SDK API change между sessions | Pin `claude-agent-sdk` в pyproject.toml; W2 ловит TypeError в `_validate_sdk_options`. |
| Real claude binary footprint на CI runners | Use PATH shim (fake binary) в всех тестах кроме explicit `@pytest.mark.requires_claude` opt-in. |
| Sandbox EAGAIN на busy hosts | Round 5 hotfix nproc=16384 покрывает типичные хосты. Если pilot ловит EAGAIN burst — open follow-up для cgroup migration (отдельная инициатива из backlog). |
| Auto-merge race с другим orchestrator instance | StateDB `_resolve_session` уже даёт shared session id (FS8 NH1). Single-instance assumption per project+wave; multi-instance в backlog post-MVP. |
| Cost watchdog underestimates from cache_read pricing | `usd_cost` уже корректно учитывает все 4 tier'а (input/output/cache_write_1h/cache_read). |

### Deferred (post-W5 follow-ups, не в этой инициативе)

- **Parallelism presets menu** — backlog item 2 (`project_backlog_parallelism_presets.md`).
- **Multi-key failover** — backlog item 3.
- **Sandbox cgroup migration** — переоценить после первых 10 stories pilot'а.
- **Round-3 fast-follows (FS7-A..FS7-E)** — открыть отдельный мини-PR после W5.

---

## 6. Acceptance — initiative level

После W5 финального merge'а на integration branch (auto_merge=false → human review → manual merge to main):

- ✅ `bmad-orchestrator run --project odyssey --wave 1a --real --max-stories 2 --max-spend-usd 5.0` запускается без NotImplementedError, spawn'ит 2 worker'а, ждёт их completion, делает code-review для каждого, сливает approved в `integration/1a`.
- ✅ Telegram bot отвечает на «привет» через real intent-router (cache hit на 2-м запросе).
- ✅ Daily cap пересечение → BUDGET_THRESHOLD_HIT(halt) → orchestrator graceful exit.
- ✅ 728 tests PASS, ruff/mypy clean.
- ✅ `docs/production-launcher.md` опубликован, pre-deployment checklist actionable.

После approve human review'а — manual `git merge integration/wave_1a_pilot_wiring` → main → tag `v0.2-pilot-ready` → запуск **реального** Wave 1a pilot на Odyssey.

---

**End of spec v0.1.**
