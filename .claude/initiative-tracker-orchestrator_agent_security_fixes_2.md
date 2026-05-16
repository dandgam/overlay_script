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
(empty — FS6 promoted)

### Current

- **id:** FS6
  **title:** Cleanup N1-N7 (circular import, enforce_day wiring, mock default, retro consistency, gh_or_curl SSRF, bot attach_state_db, retro env allowlist) (CHECKPOINT)
  **surface:** backend-python
  **spec_section:** 185-260
  **depends_on:** [FS5]
  **acceptance:**
    - N1: переместить DEFAULT_BUDGET_CAP_USD + DEFAULT_MODEL в bmad_orchestrator/config.py (или lazy import в spawn.py); python -c "import bmad_orchestrator.runtime.worker_spawn" exits 0
    - N2: enforce_day wired в _run_mock_pilot; BMAD_DAILY_LIMIT_USD env var (default $500); test mock pilot с daily_limit=$10 после 2-го spawn → halt
    - N3: mock=True default в run_orchestrator + cli; --real опционально (или auto-detect ANTHROPIC_API_KEY + shutil.which); --real без wiring → NotImplementedError честно
    - N4: agent/tools/retro.py::detect_wave_boundary использует is_retro_done (не stat().st_size > 0); test: retro файл 100-char seed → complete=False
    - N5: gh_or_curl SSRF guard — ALLOWED_GH_HOSTS frozenset; file://, localhost, evil.com → ValueError("ssrf_blocked"); api.github.com → OK
    - N6: bot/main.py::main() attach_state_db(db, session_id) после build_application; fallback на stub если StateDB недоступна
    - N7: retro.py spawn_retro_worktree(create_subprocess_exec) с env=_build_worker_env(...); test: ANTHROPIC_API_KEY=test_leak → subprocess.env не содержит
    - Все ~500 + ~30 N tests PASS
    - ruff + mypy --strict зелёные
    - grep -rn "TODO|FIXME" src/bmad_orchestrator/ — все в imports/from_bad/ или явно "v1 follow-up"
  **safety_gates:**
    - L1+L2+L3 full activation после adversarial hardening + cleanup
  **destructive_actions:** []
  **checkpoint:** true
  **estimated_retries_allowed:** 3
  **started:** 2026-05-16 13:15 UTC
  **workflow:** .claude/skills/auto-loop-spec/workflows/backend-python.md
  **retry_count:** 0
  **worker_branches:** []

### Completed

- **id:** FS5
  **title:** P0 closures (C1 pipe-to-shell, C2 bash -ic, C3 newline, C4 git -c core.hooksPath, C5 atomic budget REAL impl, C6 telegram secret scrub) + adversarial test corpus (CHECKPOINT)
  **commit:** 69d6c3c
  **completed:** 2026-05-16 13:15 UTC
  **acceptance_met:** all (524 tests pass, ruff + mypy --strict clean on touched files)
  **notes:** C1-C4 hooks.py — _split_subcommands returns (tokens, has_piped_stdin), canonicalize_flags before -c, newline normalisation, _git_subcommand extracts -c K=V + _check_git_config_overrides. C5 state/db.py — BudgetEnforceResult + enforce_and_reserve(BEGIN IMMEDIATE). BudgetGuard.enforce_and_reserve_story/batch/day delegate (unbound mode = synthetic). _run_mock_pilot reserves before spawn. C6 secret_patterns.py — SECRET_PATTERNS tuple (sk-ant, ghp_, github_pat_, AKIA, Telegram tokens, Bearer, URL creds). bot/pii_detector.scrub_output runs scrub_secrets FIRST. bot/handlers._send_safe defence-in-depth. audit._SCRUB reuses generic. Adversarial corpus 90 tests + C5/C6 PoC 11 tests = 101 new tests, anti-paper-close (pattern_id assertion, no "unknown").

## Safety Gates Triggered
(none)

## Blockers / Pauses
(none)

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

## Journal

[2026-05-16 19:00 UTC] bootstrap: manual tracker + integration/orchestrator_agent_security_fixes_2 FROM integration/orchestrator_agent_security_fixes (cumulative base). 2 сессии запланировано. Runtime=loop_wrapper, Delay=600s, Auto merge=false. Anti-paper-close principles enforced в spec (adversarial-first, grep validation, docstring=code requirement). Spec: spec/spec_orchestrator_agent_security_fixes_2.md v0.1.

[2026-05-16 12:27 UTC] FS5 promoted to Current — backend-python workflow. Scope: C1-C6 P0 closures + adversarial bash corpus. Auto merge=false, Runtime=loop_wrapper.

[2026-05-16 13:15 UTC] FS5 completed (commit 69d6c3c). 524 tests pass (423 prior + 101 new FS5: 90 adversarial bash + 11 C5/C6 PoC). Все 6 P0 закрыты с adversarial regression tests (pattern_id assertion, никогда "unknown"). C5 атомарный BEGIN IMMEDIATE serialisation подтверждён под 10 concurrent gather workers. C6 secret scrub защищает Telegram outbound от sk-ant/ghp_/AKIA/Telegram-token/URL-creds. FS6 promoted to Current.

## Final Report
(empty — last session not yet completed)
