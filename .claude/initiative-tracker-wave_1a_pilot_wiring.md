# Initiative Tracker — Wave 1a Pilot Wiring (Real-mode Event Loop)

## Metadata
- **Spec:** spec/spec_wave_1a_pilot_wiring.md
- **Parent specs:** spec_orchestrator_agent.md, spec_orchestrator_agent_security_fixes_4.md
- **Integration branch:** integration/wave_1a_pilot_wiring
- **Base branch:** main (post-merge 4934b12 — orchestrator MVP + 5 rounds security)
- **Backup branch:** backup/wave_1a_pilot_wiring-pre-2026-05-16
- **Created:** 2026-05-16
- **Bootstrap completed:** 2026-05-16 by auto-loop-spec-long
- **Scope frozen:** 2026-05-16
- **Runtime:** loop_wrapper
- **Delay seconds:** 300
- **Auto merge:** false

## Scope Freeze

### In scope
- W1: Real-mode event loop core — replace `raise NotImplementedError` в `agent/run.py:152` с `_run_real_pilot` (DAG → spawn N workers → tail JSONL → emit events → wave_boundary)
- W1: CLI flags `--max-stories` + `--max-spend-usd` в `cli/main.py::run`
- W1: `BMAD_REQUIRE_SANDBOX=1` production guard в real path (raise если NoSandbox при requirement)
- W2: Wire `human_query_subscriber` → Anthropic Messages API через `AsyncAnthropic`; intent-router skill body как cached system block
- W2: Prompt-caching invariant (cache_read_input_tokens >0 на 2-м вызове)
- W2: Cost accounting через `budget.attribute_usd(scope="intent_router", ...)`
- W3: `runtime/cost_tracker.py::WorkerCostTracker` — parse worker JSONL `usage` blocks → Decimal cost
- W3: Wire в `_run_real_pilot` event loop — per worker handle feed cost'ы в budget
- W3: Adaptive `_recent_story_costs` в BudgetGuard для realistic reserve в `enforce_and_reserve_story`
- W4: `code_review_subscriber` — spawn `claude -p /bmad-code-review` после WORKER_COMPLETED
- W4: `merge_to_integration_subscriber` — fast-forward merge feature/<story> → integration/<wave> при approve
- W4: HUMAN_QUERY escalation при request_changes / reject / merge conflict
- W4: Worktree cleanup только под `.worktrees/` prefix (safety verify)
- W5: Bot `USER_CHAT_MESSAGE` flow через intent-router (W2 dispatch); `subscribe_one_correlation` в event_loop
- W5: End-to-end smoke test (`tests/test_w5_e2e_smoke.py`) с синтетическим Odyssey wave
- W5: `docs/production-launcher.md` (systemd unit + pre-deployment checklist + recovery runbook)
- ~110 новых tests; финал ~728 PASS

### Out of scope (deferred)
- Parallelism presets menu (off/gentle/balanced/aggressive/max) — backlog item 2
- Multi-key failover — backlog item 3 (нужен паттерн исчерпания из реального pilot)
- Sandbox cgroup migration (systemd-run --scope -p TasksMax) — переоценить после первых 10 stories pilot'a
- Round-3 fast-follows (FS7-A..FS7-E) — отдельный мини-PR после W5
- nftables network whitelist для `github_only` — defer до W6
- Self-modifying skills — v3 horizon
- TTS voice response — backlog item 6

### Deferred to follow-up initiative
- Multi-project queue (Odyssey + CRM-hotfix параллельно) — backlog item 4
- H-deferred (H1, H2 symlinks, H9 PID registry, M1-M9) — активируется при production wave

## Sessions

### Pending

- **id:** W1
  **title:** Real-mode event loop core — replace NotImplementedError + CLI flags + sandbox guard (CHECKPOINT)
  **surface:** backend-python
  **spec_section:** 100-180
  **depends_on:** []
  **destructive_actions:** []
  **checkpoint:** true
  **estimated_retries_allowed:** 3
  **acceptance:**
    - Implement `async def _run_real_pilot(bus, *, project, wave, max_parallel, max_stories, max_spend_usd, budget, state_db, session_id, models, options)` in `src/bmad_orchestrator/agent/run.py`
    - Replace `raise NotImplementedError` at line 152 with call to `_run_real_pilot(...)`
    - Add CLI flags `--max-stories N` (default 50) + `--max-spend-usd N` (default 50.0) to `cli/main.py::run`
    - Production guard: `BMAD_REQUIRE_SANDBOX=1` + NoSandbox → RuntimeError in real path
    - `tests/test_w1_real_pilot.py` — 25 tests (fake claude PATH shim, max-stories cap, max-spend cap, sandbox guard, mock path unchanged)
    - `grep -c "raise NotImplementedError" src/bmad_orchestrator/agent/run.py` == 0
    - `grep -c "async def _run_real_pilot" src/bmad_orchestrator/agent/run.py` == 1
    - `pytest tests/ -q` — 638 PASS (613 baseline + 25 new); ruff/mypy clean
  **safety_gates:**
    - L1: No `--no-verify`, no `git push --force`, no `git reset --hard` — git commit discipline (project CLAUDE.md hard rule)
    - L2: deny-list `runtime/sandbox.py`, `runtime/worker_spawn.py`, `agent/safety/budget_guard.py` — security-critical, frozen after round 5
    - L3: branch isolation — integration/wave_1a_pilot_wiring only

- **id:** W2
  **title:** Intent-router LLM dispatch — wire human_query_subscriber to Anthropic Messages API (CHECKPOINT)
  **surface:** backend-python
  **spec_section:** 200-280
  **depends_on:** [W1]
  **destructive_actions:** []
  **checkpoint:** true
  **estimated_retries_allowed:** 3
  **acceptance:**
    - Replace stub `log.warning("human_query_intent_router_deferred", ...)` at `agent/run.py:506` with real `AsyncAnthropic().messages.create(...)` call
    - Load intent-router skill body as cached system block (`cache_control: {"type": "ephemeral"}`)
    - Parse `tool_use` blocks from response → dispatch through `mcp_server.handle_tool_call(...)`
    - Cost accounting: `usd_cost(models.routine, TokenUsage(...))` → `budget.attribute_usd(scope="intent_router", spent=...)`
    - Daily cap halt blocks dispatch + emits BUDGET_THRESHOLD_HIT(halt)
    - Stub fallback when no `ANTHROPIC_API_KEY` (CI / tests без secret)
    - `tests/test_w2_intent_router.py` — 20 tests (real dispatch routes tool_use, cache hit on 2nd call, daily cap halt, stub fallback)
    - `grep -c "AsyncAnthropic\|client.messages.create" src/bmad_orchestrator/agent/run.py` ≥ 1
    - `grep -c "cache_control" src/bmad_orchestrator/agent/run.py` ≥ 1
    - `pytest tests/ -q` — 658 PASS; ruff/mypy clean
  **safety_gates:**
    - L1: Никаких hardcoded API keys (CLAUDE.md security baseline)
    - L2: deny-list freeze (как W1)
    - L3: branch isolation

- **id:** W3
  **title:** Cost watchdog real polling — WorkerCostTracker + adaptive story reserve (CHECKPOINT)
  **surface:** backend-python
  **spec_section:** 300-360
  **depends_on:** [W1]
  **destructive_actions:** []
  **checkpoint:** true
  **estimated_retries_allowed:** 3
  **acceptance:**
    - Create `src/bmad_orchestrator/runtime/cost_tracker.py::WorkerCostTracker` (parse SDK + message-wrapped usage blocks → Decimal cost; cumulative accumulation)
    - Wire WorkerCostTracker into `_run_real_pilot` tail_jsonl_events generator — per-event delta → `budget.attribute_usd`
    - Add `_recent_story_costs: deque[Decimal] maxlen=3` to `BudgetGuard` for adaptive reserve
    - `enforce_and_reserve_story` uses `min(cfg.story_alarm_usd, p95(last_3_costs))` (or `cfg.story_alarm_usd / 2` if empty history)
    - Log `worker_cost_final` structured event with story_id, total_usd, cache_hit_ratio
    - `tests/test_w3_cost_tracker.py` — 25 tests (parse variants, cumulative accumulation, cache_hit_ratio, adaptive reserve)
    - `grep -c "class WorkerCostTracker" src/bmad_orchestrator/runtime/cost_tracker.py` == 1
    - `grep -c "_recent_story_costs" src/bmad_orchestrator/agent/safety/budget_guard.py` ≥ 1
    - `pytest tests/ -q` — 683 PASS; ruff/mypy clean
  **safety_gates:**
    - L1: No mutation of frozen sandbox/worker_spawn (deny-list); BudgetGuard modification limited to additive `_recent_story_costs` field
    - L2: deny-list freeze
    - L3: branch isolation

- **id:** W4
  **title:** Code-review gate + auto-merge to integration branch (CHECKPOINT)
  **surface:** backend-python
  **spec_section:** 380-460
  **depends_on:** [W1, W3]
  **destructive_actions:**
    - `git merge --ff-only feature/<story_id>` → integration/<wave> branch
    - Worktree cleanup via `runtime/worktree.py::cleanup_worktree` (path-prefix verified)
  **checkpoint:** true
  **estimated_retries_allowed:** 3
  **acceptance:**
    - Implement `code_review_subscriber(event, bus)` — на WORKER_COMPLETED(success) spawn `claude -p /bmad-code-review` в той же worktree
    - Parse verdict (approve/request_changes/reject) из JSONL → emit `CODE_REVIEW_VERDICT` event (new EventType)
    - Implement `merge_to_integration_subscriber` — fast-forward merge feature/<story> → integration/<wave>; никакого `--no-verify`/`--force`/`reset --hard`
    - HUMAN_QUERY escalation при request_changes / reject / MergeError; story остаётся в worktree, branch не двинут
    - Worktree cleanup — safety verify path под `_root/.worktrees/` prefix
    - `tests/test_w4_code_review_gate.py` — 30 tests (skill spawn, approve merge, reject escalation, conflict handling, cleanup safety)
    - `grep -c "code_review_subscriber\|merge_to_integration_subscriber" src/bmad_orchestrator/agent/run.py` == 2
    - `grep -c "CODE_REVIEW_VERDICT" src/bmad_orchestrator/runtime/event_loop.py` ≥ 1
    - `grep -c '"--ff-only"' src/bmad_orchestrator/agent/run.py` ≥ 1
    - `grep -c "no-verify\|--force\|reset --hard" src/bmad_orchestrator/agent/run.py` == 0
    - `pytest tests/ -q` — 713 PASS; ruff/mypy clean
  **safety_gates:**
    - L1: ANY use of `--no-verify`, `--force`, `reset --hard` halts — checked via grep validation in DoD
    - L1: cleanup_worktree path-prefix check; попытка delete /etc raises (test coverage обязателен)
    - L2: deny-list freeze; merge logic полностью в `agent/run.py` (caller-side)
    - L3: branch isolation — никогда не сливать в main из этой сессии

- **id:** W5
  **title:** Telegram bot real-mode + e2e smoke test + production launcher docs (FINAL)
  **surface:** backend-python
  **spec_section:** 480-560
  **depends_on:** [W2, W4]
  **destructive_actions:** []
  **checkpoint:** false
  **estimated_retries_allowed:** 3
  **acceptance:**
    - Bot `_handle_text_message` (или эквивалент) emits `USER_CHAT_MESSAGE(corr_id, text)` → awaits `HUMAN_RESPONSE(corr_id)` через `bus.subscribe_one_correlation` (60s timeout)
    - Add `EventLoop.subscribe_one_correlation(corr_id) → asyncio.Future` in `runtime/event_loop.py`
    - `tests/test_w5_e2e_smoke.py` — 1 full e2e (synthetic Odyssey wave: spawn → code-review approve → merge → wave_boundary) + 14 unit tests (correlation, bot flow)
    - `docs/production-launcher.md` — systemd unit example (BMAD_REQUIRE_SANDBOX=1, BMAD_REQUIRE_DB_BRIDGE=1) + pre-deployment checklist + recovery runbook
    - `grep -c "subscribe_one_correlation" src/bmad_orchestrator/runtime/event_loop.py` ≥ 1
    - `grep -c "USER_CHAT_MESSAGE\|asyncio.wait_for" src/bmad_orchestrator/bot/handlers.py` ≥ 1
    - `test -f docs/production-launcher.md` succeeds
    - `pytest tests/ -q` — 728 PASS; ruff/mypy clean
    - E2E smoke test green на 5 повторов (no flakes)
  **safety_gates:**
    - L1: git commit discipline; no force/no-verify
    - L2: deny-list freeze; bot/handlers.py changes limited to text-message dispatcher
    - L3: branch isolation; Final session — manual merge через human review (Auto merge=false)

### Current
(none — next wake promotes W1 from Pending)

### Completed
(none)

## Safety Gates Triggered
(none yet)

## Blockers / Pauses
(none yet)

## Decisions Log

- **date:** 2026-05-16 (bootstrap)
  **session:** bootstrap
  **decision:** Initiative wave_1a_pilot_wiring — 5 sessions W1-W5, surface=backend-python, code-only. Replace `raise NotImplementedError` в `agent/run.py:152` real event loop с DAG → worker spawn → JSONL tail → cost tracking → code-review gate → integration merge. Wire `human_query_subscriber` (line 506) к real Anthropic Messages API через AsyncAnthropic с prompt caching.
  **rationale:** Все ингредиенты production-mode'a структурно готовы после MVP scaffold S1-S8 + 4 раундов security fixes + R5 hotfix (4934b12 on main). 613 tests PASS baseline. Реальный pilot data — единственный source of truth для последующих инициатив (parallelism presets, multi-key failover, cgroup migration); pre-investing engineering effort на cgroup migration без real-world signal = premature optimization.
  **impact:** После W5 merge'a — оркестратор может запустить настоящий Wave 1a pilot на /home/server/odyssey-ux/ (16 эпиков, 133 FR). Next backlog items unblocked: parallelism-presets-menu, multi-key-failover (после pilot data), sandbox-cgroup-migration (переоценка).

- **date:** 2026-05-16 (bootstrap)
  **session:** bootstrap
  **decision:** Sandbox cgroup migration НЕ blocking wave-1a-pilot.
  **rationale:** Round 5 hotfix уже поднял nproc default до 16384 (`runtime/sandbox.py:61`) — типичные хосты (~3000 user procs) покрыты. Per-UID counting остаётся fundamental issue, но без real pilot data нельзя оценить ROI миграции.
  **impact:** Если pilot ловит EAGAIN burst — открыть отдельную follow-up инициативу для cgroup migration. Memo `project_backlog_sandbox_cgroup_migration.md` остаётся в backlog.

## Journal

[2026-05-16 bootstrap] bootstrap: tracker + backup + integration branch созданы, 5 sessions planned, runtime=loop_wrapper, delay=300s, auto_merge=false
