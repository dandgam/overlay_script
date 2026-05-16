# Initiative Tracker — Orchestrator Agent Security Fixes Round 2

## Metadata
- **Spec:** spec/spec_orchestrator_agent_security_fixes_2.md
- **Parent specs:** spec/spec_orchestrator_agent_security_fixes.md, spec/spec_orchestrator_agent.md
- **Integration branch:** integration/orchestrator_agent_security_fixes_2
- **Base branch:** integration/orchestrator_agent_security_fixes (cumulative — содержит S1-S8 + FS1-FS4)
- **Backup branch:** backup/orchestrator_agent_security_fixes_2-pre-2026-05-16
- **Created:** 2026-05-16
- **Bootstrap completed:** 2026-05-16 manual (pre-created branches, skipping auto-loop-spec bootstrap mode)
- **Scope frozen:** 2026-05-16
- **Runtime:** loop_wrapper
- **Delay seconds:** 600
- **Auto merge:** false

## Scope Freeze

### In scope
- 2 сессии (FS5, FS6) закрывающие 6 P0 (C1-C6) + 7 HIGH/MED (N1-N7) обнаруженные independent code-auditor'ом после round 1.
- FS5 — P0 closures (C1-C6) с adversarial test corpus (60 DENY + 20 ALLOW + ~30 для C5/C6) [CHECKPOINT]
- FS6 — Cleanup N1-N7 (circular import, enforce_day wiring, mock default, detect_wave_boundary consistency, gh_or_curl SSRF, bot attach_state_db, retro spawn env) [CHECKPOINT]

### Anti-paper-close принципы
- Adversarial-first: для каждого C1-C6 ПЕРВЫМ пишется PoC test (должен fail на текущем коде), потом фикс
- PoC прогон в shell (subprocess.run shell=True с минимальным env) демонстрирует что bash без нашего scanner выполнил бы команду
- Docstring promises = code requirements (если docstring говорит про метод, метод должен существовать)
- Grep validation в каждом acceptance: явные `grep ... | wc -l` cmd чтобы regression была видна сразу

### Out of scope (deferred)
- H1, H2, H9, H14 (per spec round 1 deferred list)
- M1, M2, M4, M7, M9 (per round 1 deferred)
- N8 bot orphan re-enqueue DoS — требует cross-process coordination, defer до production observed traffic
- Реальный pilot run на Wave 1a — отдельная инициатива

## Sessions

### Pending
(empty)

### Current
(empty — initiative complete, awaiting manual merge)

### Completed

- **id:** FS5
  **title:** P0 closures (C1 pipe-to-shell, C2 bash -ic, C3 newline, C4 git -c core.hooksPath, C5 atomic budget REAL impl, C6 telegram secret scrub) + adversarial test corpus (CHECKPOINT)
  **commit:** 69d6c3c
  **completed:** 2026-05-16 13:15 UTC
  **acceptance_met:** all (524 tests pass, ruff + mypy --strict clean on touched files)
  **notes:** C1-C4 hooks.py — _split_subcommands returns (tokens, has_piped_stdin), canonicalize_flags before -c, newline normalisation, _git_subcommand extracts -c K=V + _check_git_config_overrides. C5 state/db.py — BudgetEnforceResult + enforce_and_reserve(BEGIN IMMEDIATE). BudgetGuard.enforce_and_reserve_story/batch/day delegate (unbound mode = synthetic). _run_mock_pilot reserves before spawn. C6 secret_patterns.py — SECRET_PATTERNS tuple (sk-ant, ghp_, github_pat_, AKIA, Telegram tokens, Bearer, URL creds). bot/pii_detector.scrub_output runs scrub_secrets FIRST. bot/handlers._send_safe defence-in-depth. audit._SCRUB reuses generic. Adversarial corpus 90 tests + C5/C6 PoC 11 tests = 101 new tests, anti-paper-close (pattern_id assertion, no "unknown").

- **id:** FS6
  **title:** Cleanup N1-N7 (circular import, enforce_day wiring, mock default, retro consistency, gh_or_curl SSRF, bot attach_state_db, retro env allowlist) (CHECKPOINT)
  **commit:** 7d140ac
  **completed:** 2026-05-16 20:10 UTC
  **acceptance_met:** all (541 tests pass — 524 prior + 17 new FS6 N-tests; ruff + mypy --strict clean on touched files; grep TODO/FIXME audit only in imports/from_bad/ + v1 follow-up markers)
  **notes:** N1 — DEFAULT_MODEL/DEFAULT_BUDGET_CAP_USD перенесены в config.py + lazy import в spawn.py (Option A + B, разрывает worker_spawn → agent.tools → spawn → worker_spawn цикл). N2 — _run_mock_pilot читает BMAD_DAILY_LIMIT_USD env, локальный daily_spent_usd трекер, enforce_day(projected_daily) перед spawn; halt path эмитит BUDGET_THRESHOLD_HIT(scope=day). N3 — run_orchestrator(mock=True) + CLI --mock/--real default mock. N4 — detect_wave_boundary использует is_retro_done() (content-schema), seed-stub 100-char retro корректно даёт retro_done=False. N5 — gh_or_curl SSRF guard: ALLOWED_GH_HOSTS frozenset (5 hosts), _assert_url_safe() raises ValueError("ssrf_blocked") для file://, http://, localhost, evil.com ДО построения headers. N6 — bot/main._attach_bridge() инициализирует StateDB+session+attach_state_db ДО run_bot; degrade to stub на любой DB error. N7 — spawn_retro_worktree(real=True) использует env=_build_worker_env(...), secrets не наследуются.

## Safety Gates Triggered
(none)

## Blockers / Pauses

- **[2026-05-16 20:10 UTC] manual_merge_pending** — initiative complete on integration/orchestrator_agent_security_fixes_2. User must merge manually:
  ```
  git checkout main && git merge --no-ff integration/orchestrator_agent_security_fixes_2 -m "merge orchestrator_agent_security_fixes_2 FS5..FS6"
  ```
  **resolution:** PENDING (user action — wrapper exits, initiative complete)

## Decisions Log

- **date:** 2026-05-16 19:00 UTC
  **session:** bootstrap
  **decision:** Round 2 fix-инициатива из-за paper-close pattern в round 1 (code-auditor PoC bypass нашёл 6 P0 которых не было видно через unit tests).
  **rationale:** Round 1 тесты тестировали реализацию, не adversarial bypass. Round 2 ОБЯЗАН включать adversarial-first paradigm.
  **impact:** Базовая ветка integration/orchestrator_agent_security_fixes (cumulative). После завершения — manual merge только последней integration ветки на main (она содержит всё).

- **date:** 2026-05-16 13:15 UTC
  **session:** FS5
  **decision:** C5 test использует halt=$53 (не $50 как в spec PoC) — устраняет boundary ambiguity. $48+$5=$53 fits ровно один раз, $53+$5=$58>$53 — 9 lose. Spirit (atomic serialisation under cap) сохранён.
  **rationale:** spec PoC math неоднозначна при $48+$5=$53 vs halt=$50 (по строгому `>` никто не должен пройти; по `≤` все пройдут). halt=$53 даёт unambiguous test contract.
  **impact:** Test pattern для будущих atomic-reserve assertions — выбирать cap так чтобы `current + reserve == cap` для первого winner.

- **date:** 2026-05-16 20:10 UTC
  **session:** FS6
  **decision:** N1 потребовал двойной фикс — Option A (перенести константы в config.py) + Option B (lazy import spawn_worker в spawn.py). Только Option A был недостаточен: spawn.py имел top-level `from runtime.worker_spawn import spawn_worker as runtime_spawn_worker`, который триггерил тот же import-cycle (worker_spawn → agent.tools._common → agent.tools.__init__ → agent.tools.spawn → worker_spawn).
  **rationale:** Spec §5.6.1 предлагал каждый из двух вариантов как самостоятельный, но в реальности circular path шёл не через константы, а через сам `spawn_worker` callable. Lazy import — единственный способ полностью разорвать цикл.
  **impact:** Любой будущий tool, который вызывает runtime.worker_spawn, должен использовать lazy import (inside function body), не top-level.

- **date:** 2026-05-16 20:10 UTC
  **session:** FS6
  **decision:** N2 daily cap track через локальный `daily_spent_usd` accumulator в _run_mock_pilot (не через state.db budget_tracker). Unbound BudgetGuard (без state.db binding) не может атомарно аккумулировать; mock pilot в CI как раз unbound.
  **rationale:** spec §5.6.2 описывал enforce_day через atomic state.db rows. Но реальный mock-pilot контекст — unbound BudgetGuard, поэтому enforce_day(projected, today) принимает явный projected total. Production wire-up получит state.db.budget_tracker аккумулятор отдельно.
  **impact:** Halt path эмитит BUDGET_THRESHOLD_HIT(scope=day, level=halt, projected, halt_threshold, day) — observable через event bus. Без state.db этот path остаётся testable.

## Journal

[2026-05-16 19:00 UTC] bootstrap: manual tracker + integration/orchestrator_agent_security_fixes_2 FROM integration/orchestrator_agent_security_fixes (cumulative base). 2 сессии запланировано. Runtime=loop_wrapper, Delay=600s, Auto merge=false. Anti-paper-close principles enforced в spec (adversarial-first, grep validation, docstring=code requirement). Spec: spec/spec_orchestrator_agent_security_fixes_2.md v0.1.

[2026-05-16 12:27 UTC] FS5 promoted to Current — backend-python workflow. Scope: C1-C6 P0 closures + adversarial bash corpus. Auto merge=false, Runtime=loop_wrapper.

[2026-05-16 13:15 UTC] FS5 completed (commit 69d6c3c). 524 tests pass (423 prior + 101 new FS5: 90 adversarial bash + 11 C5/C6 PoC). Все 6 P0 закрыты с adversarial regression tests (pattern_id assertion, никогда "unknown"). C5 атомарный BEGIN IMMEDIATE serialisation подтверждён под 10 concurrent gather workers. C6 secret scrub защищает Telegram outbound от sk-ant/ghp_/AKIA/Telegram-token/URL-creds. FS6 promoted to Current.

[2026-05-16 20:10 UTC] FS6 completed (commit 7d140ac). 541 tests pass (524 prior + 17 new FS6 N-tests). N1-N7 закрыты: N1 dual-fix (config constants + lazy import) разорвал circular import; N2 daily cap эмитит halt event; N3 mock default = True; N4 content-schema retro check; N5 SSRF guard на 5 GH hosts; N6 bot state-db bridge; N7 retro env allowlist. ruff + mypy --strict clean, TODO/FIXME audit clean. Runtime=loop_wrapper → wrapper exits после Final Report. Auto merge=false → manual main merge required.

[2026-05-16 20:10 UTC] initiative complete — all 6 P0 (round 2 C1-C6) + 7 HIGH/MED (N1-N7) closed. Manual merge pending on main: `git checkout main && git merge --no-ff integration/orchestrator_agent_security_fixes_2`. После merge — закрыть backup branch lifecycle, обновить spec §22 / CLAUDE.md status в отдельном commit на main.

## Final Report

**Initiative:** Orchestrator Agent Security Fixes Round 2
**Status:** ✅ Complete (awaiting manual merge to main)

**Sessions:** 2/2 completed
- FS5 (commit `69d6c3c`) — P0 closures C1-C6 + adversarial corpus (101 new tests)
- FS6 (commit `7d140ac`) — HIGH/MED cleanup N1-N7 (17 new tests)

**Integration branch:** `integration/orchestrator_agent_security_fixes_2`
**Backup branch:** `backup/orchestrator_agent_security_fixes_2-pre-2026-05-16`

**Test coverage:**
- Total: 541 PASSED (was 423 pre-round-2, +118 new tests in round 2)
- New tests by session: FS5=101 (90 adversarial bash + 11 C5/C6 PoC), FS6=17
- Quality gates: ruff clean, mypy --strict clean on all touched modules
- TODO/FIXME audit: only `imports/from_bad/` (external code) + `v1 follow-up` markers

**Diff stats (FS5 + FS6 combined):**
```
git diff --stat integration/orchestrator_agent_security_fixes..integration/orchestrator_agent_security_fixes_2
```
(см. два commit'a 69d6c3c, 7d140ac)

**P0 closures (round 2 C1-C6):**
- C1 pipe-to-shell — hooks._split_subcommands returns (tokens, has_piped_stdin)
- C2 bash -ic — canonicalize_flags before -c rule
- C3 newline injection — newline normalisation pre-tokenisation
- C4 git -c core.hooksPath bypass — _git_subcommand extracts -c K=V, _check_git_config_overrides
- C5 budget race — atomic BEGIN IMMEDIATE serialisation в state/db.py::enforce_and_reserve
- C6 telegram secret leak — SECRET_PATTERNS scrubbed BEFORE PII redaction, defence-in-depth в bot/handlers._send_safe

**HIGH/MED closures (N1-N7):**
- N1 circular import (worker_spawn ↔ agent.tools.spawn) — constants→config, lazy import
- N2 enforce_day wiring — BMAD_DAILY_LIMIT_USD env, halt event observable
- N3 mock=True default — production code never silently raises NotImplementedError
- N4 detect_wave_boundary content-schema — is_retro_done() replaces stat().st_size > 0
- N5 gh_or_curl SSRF — ALLOWED_GH_HOSTS frozenset (5 canonical hosts), _assert_url_safe()
- N6 bot StateDB bridge — _attach_bridge() в bot/main.py, fallback to stub mode on DB error
- N7 retro subprocess env allowlist — secrets не наследуются в `claude -p /bmad-retrospective`

**Out of scope (deferred to future initiatives):**
- H1, H2, H9, H14 (per spec round 1 deferred)
- M1, M2, M4, M7, M9 (per round 1 deferred)
- N8 bot orphan re-enqueue DoS (cross-process coordination)
- Real pilot run на Odyssey Wave 1a (отдельная инициатива)

**Manual merge command:**
```bash
git checkout main && \
  git merge --no-ff integration/orchestrator_agent_security_fixes_2 \
    -m "merge orchestrator_agent_security_fixes_2 FS5..FS6"
```

**Post-merge follow-ups (separate commit on main):**
- Обновить `spec/spec_orchestrator_agent_security_fixes_2.md` §22 (sign-off)
- Обновить `CLAUDE.md` status section (Phase 4 round 2 complete)
- backlog: parallelism presets menu (см. memory: project_backlog_parallelism_presets)
