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

- **id:** FS5
  **title:** P0 closures (C1 pipe-to-shell, C2 bash -ic, C3 newline, C4 git -c core.hooksPath, C5 atomic budget REAL impl, C6 telegram secret scrub) + adversarial test corpus (CHECKPOINT)
  **surface:** backend-python
  **spec_section:** 75-180
  **depends_on:** []
  **acceptance:**
    - C1: _scan_sub_command принимает has_piped_stdin; shell+pipe → deny; ALLOW: cat|grep, git log|head, ls|sort
    - C2: shells (bash/sh/ksh/zsh/dash) canonicalize_flags перед "-c" check; bash -ic / -lic / -ic / sh -ic → deny
    - C3: command.replace("\n", ";") перед tokenize; echo a\nrm -rf /tmp/x → deny
    - C4: _git_subcommand возвращает configured globals; core.hooksPath / hooks.pre-commit override → deny git_no_verify_via_config
    - C5: state/db.py::enforce_and_reserve (BEGIN IMMEDIATE + atomic check-and-reserve); BudgetGuard.enforce_and_reserve_story/batch/day делегирует; _run_mock_pilot использует через replace старого pattern; PoC 10 concurrent gather workers $5 each при cap=$50 spent=$48 → ровно 1 allowed, 9 halt_breached, final $53
    - C6: agent/safety/secret_patterns.py (новый модуль), scrub_secrets() применяется в bot/pii_detector.py::scrub_output И в bot/handlers.py::_send_safe; PoC: sk-ant-*, Telegram token, ghp_*, URL creds, AKIA — все redacted в outbound
    - tests/test_fs5_adversarial_bash_corpus.py: 60+ DENY parametrize cases, 20+ ALLOW. Каждый DENY — assert decision="deny" AND reason contains pattern_id (не "unknown")
    - Все ~500 tests PASS (423 + ~80 new)
    - ruff + mypy --strict зелёные
  **safety_gates:**
    - L1 full hardening (token-based, adversarial-tested)
    - L2 atomic budget REAL impl
    - L1 Telegram outbound secret scrub
  **destructive_actions:** []
  **checkpoint:** true
  **estimated_retries_allowed:** 3

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

### Current
(none — next wake promotes FS5 from Pending)

### Completed
(empty — initiative not yet started)

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

## Journal

[2026-05-16 19:00 UTC] bootstrap: manual tracker + integration/orchestrator_agent_security_fixes_2 FROM integration/orchestrator_agent_security_fixes (cumulative base). 2 сессии запланировано. Runtime=loop_wrapper, Delay=600s, Auto merge=false. Anti-paper-close principles enforced в spec (adversarial-first, grep validation, docstring=code requirement). Spec: spec/spec_orchestrator_agent_security_fixes_2.md v0.1.

## Final Report
(empty — last session not yet completed)
