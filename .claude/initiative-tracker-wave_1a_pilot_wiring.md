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

- **id:** W2
  **title:** Intent-router LLM dispatch — wire human_query_subscriber to Anthropic Messages API (CHECKPOINT)
  **surface:** backend-python
  **spec_section:** 200-280
  **depends_on:** [W1]
  **destructive_actions:** []
  **checkpoint:** true
  **estimated_retries_allowed:** 3
  **started:** 2026-05-16 17:07 UTC
  **workflow:** workflows/backend-python.md
  **retry_count:** 0
  **worker_branches:** []
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

### Completed

- **id:** W1
  **title:** Real-mode event loop core — replace NotImplementedError + CLI flags + sandbox guard (CHECKPOINT)
  **surface:** backend-python
  **spec_section:** 100-180
  **started:** 2026-05-16 23:30 UTC
  **finished:** 2026-05-16 17:07 UTC (next-day continuation after compaction)
  **commit_hash:** 13737de
  **commit_message:** feat(sdk): W1 — real-mode event loop core + CLI caps + sandbox guard
  **files_changed:**
    - src/bmad_orchestrator/agent/run.py
    - src/bmad_orchestrator/cli/main.py
    - tests/test_fs4_real_mode_wiring.py
    - tests/test_w1_real_pilot.py (new)
  **diff_stats:** 4 files changed, 1005 insertions(+), 30 deletions(-)
  **tests_passed:** 640 (613 baseline + 27 new W1 tests)
  **retry_count:** 0
  **outcome:** SUCCESS
  **dod_evidence:**
    - `_run_real_pilot` implemented in `agent/run.py:519` with DAG loop, spawn_worker, tail_jsonl, bridge worker_completed → bus events
    - `raise NotImplementedError` removed from `run_orchestrator` — replaced with dispatch to `_run_real_pilot`
    - CLI flags `--max-stories 50` (default) and `--max-spend-usd 50.0` (default) added to `cli/main.py::run` and propagated to daemon/watch/direct paths
    - Production guard `detect_sandbox()` at `_run_real_pilot` entry → RuntimeError when `BMAD_REQUIRE_SANDBOX=1` + NoSandbox
    - tests/test_w1_real_pilot.py — 27 acceptance tests (CLI flags, grep, signature, sandbox guard pass/fail, fake-spawn end-to-end, WAVE_BOUNDARY_REACHED, start_backstop called, max_stories cap 0/1/2, max_spend_usd cap halt + reason=max_spend_usd_cap, mock regression, daemon args propagation)
    - tests/test_fs4_real_mode_wiring.py — old `test_run_orchestrator_real_mode_raises_not_implemented` renamed and rewritten to assert dispatch to `_run_real_pilot` with caps forwarded
    - grep validation: `raise NotImplementedError` = 0; `async def _run_real_pilot` = 1; `max_stories` in cli/main.py = 4; `max_spend_usd` in cli/main.py = 4
    - pytest: 640 passed in ~10s; ruff clean; mypy pre-existing main_merge_token.py:40 error confirmed unrelated to W1 via git stash baseline check

### Notable findings during W1 (carry into W2+)

- **EventLoop backstop task cancellation bug** discovered during W1 test work: `EventLoop.start_backstop_task` runner wraps `await self._stopped.wait()` in `with suppress(asyncio.CancelledError)` inside a `while not self._stopped.is_set()` loop. Pytest-asyncio teardown cancels never escape the suppress, so the task spins forever after fixture teardown and hangs the next test in the session. W1 tests work around it by calling `await bus.stop()` in the `_drain` helper and after every direct `_run_real_pilot(...)` invocation. **Out of W1 scope to fix** — but: W2-W4 tests must use the same drain/stop discipline OR W3 (event_loop adjacent surface) should consider tightening the runner so cancel can propagate when `_stopped` is unset. Document this in a follow-up if W3 doesn't address it.

## Safety Gates Triggered
(none — W1 was code-only, no destructive actions, no deny-list hits)

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

- **date:** 2026-05-16 (W1)
  **session:** W1
  **decision:** `_run_real_pilot` дизайн — bus-emit-only (no direct subscriber wiring в этой сессии). Worker spawn использует `runtime_spawn_worker` через `from ..runtime.worker_spawn import spawn_worker as runtime_spawn_worker` import; JSONL bridged через `_tail_and_emit_completion(bus, handle, story_id)`. Каскад W2 (intent-router subscriber) и W4 (code_review_subscriber + merge_to_integration_subscriber) подключаются позже через bus.on() — `_run_real_pilot` остаётся «producer» only.
  **rationale:** Изолирует W1 от W2/W4 контракта и упрощает testability — fake spawn в tests inject'ит JSONL события напрямую в bus.queue без реального subprocess.
  **impact:** W2/W4 могут подключаться к WORKER_COMPLETED без модификации `_run_real_pilot`. Cost-tracker (W3) понадобится поправить `_tail_and_emit_completion` чтобы parse usage blocks — это ожидаемое касание в W3 scope, не deviation.

## Journal

[2026-05-16 bootstrap] bootstrap: tracker + backup + integration branch созданы, 5 sessions planned, runtime=loop_wrapper, delay=300s, auto_merge=false
[2026-05-16 23:30 UTC] W1 start: promoted to Current; surface=backend-python; workflow=workflows/backend-python.md; baseline 613 PASS confirmed
[2026-05-16 17:07 UTC] W1 done, runtime=loop_wrapper — wrapper handles next iteration. Commit 13737de; 640 PASS (613 baseline + 27 new); ruff clean; mypy pre-existing only. W2 promoted to Current.
