# Initiative Tracker — Orchestrator Agent Security Fixes

## Metadata
- **Spec:** spec/spec_orchestrator_agent_security_fixes.md
- **Parent specs:** spec/spec_orchestrator_agent.md
- **Integration branch:** integration/orchestrator_agent_security_fixes
- **Base branch:** integration/orchestrator_agent (НЕ main — фиксим код S1-S8)
- **Backup branch:** backup/orchestrator_agent_security_fixes-pre-2026-05-16
- **Created:** 2026-05-16
- **Bootstrap completed:** 2026-05-16 manual (pre-created branches, skipping auto-loop-spec bootstrap mode)
- **Scope frozen:** 2026-05-16
- **Runtime:** loop_wrapper
- **Delay seconds:** 600
- **Auto merge:** false

## Scope Freeze

### In scope
- 4 fix-сессии (FS1..FS4) закрывающие P0 blockers B1-B12 + критичные P1 из code-review + security audit `integration/orchestrator_agent`.
- FS1 Secret hygiene (B6 B7 B8 + H5 + M6 + M10 + H13 RU PII gaps)
- FS2 Safety hooks hardening (B2 B3 B4 + C5 substring bypass + M3 + M8 callback whitelist)
- FS3 Budget aggregator + concurrency (B5 + H11 retro overwrite + H15 subprocess timeout + M5 event queue)
- FS4 Real-mode SDK wiring + bot cross-process bridge (B1 + B9 + B10 + B11 + B12)

### Out of scope (explicit — deferred to follow-up initiatives)
- H1 (rmtree TOCTOU), H2 (sync_skill_patches symlinks) — pre-pilot, no real workers
- H9, H14 (PID ownership registry) — single orchestrator per spec
- M1 `--dangerously-skip-permissions` opt-in
- M2 pre-action-snapshot.sh `set -e`
- M4 parse_story_md bounds
- M7 model name validation
- M9 homoglyph normalization в intent-router

### Deferred to follow-up initiative
- Реальный pilot run на Odyssey Wave 1a — после merge ОБЕИХ integration branches на main
- TTS (voice output) — v2

## Sessions

### Pending

- **id:** FS4
  **title:** Real-mode SDK wiring + system_prompt impl + defer_loading + bot cross-process bridge (CHECKPOINT)
  **surface:** backend-python
  **spec_section:** 385-490
  **depends_on:** [FS3]
  **acceptance:**
    - Verify ClaudeAgentOptions API через mcp__context7__query-docs (claude-agent-sdk-python)
    - agent/run.py: build_agent_options под актуальную API; soft-fail на TypeError → RuntimeError
    - run_orchestrator real-mode: либо реальный loop с worker spawn'ами либо raise NotImplementedError loud (НЕ silent no-op)
    - agent/system_prompt.py: _load_project_context (target CLAUDE.md + orchestrator CLAUDE.md + epics.md + sprint-status; cap 25K tokens); _operational_rules (из spec §17+§9+§10); _few_shot_examples (10-15 RU→tool pairs); cache_control ttl=1h явно на каждом блоке
    - Verify defer_loading=True API через context7; либо wire на всех 34 tools либо manual split (5 always-on + 29 search-on-demand)
    - bot/handlers.py: _HUMAN_RESPONSES → per-chat FIFO с corr_id; cap 100 in-flight
    - cross-process bridge через state.db.event_queue (НЕ in-process _BUS); latency <500ms
    - agent/run.py: subscriber на HUMAN_QUERY → intent-router → emit HUMAN_RESPONSE с corr_id
    - tools/spawn.py spawn_worker(real=True): mock-fallback с loud warning + {"mock":true,"fallback_reason":...,"real_requested":true}; caller raise если получил такой payload
    - tests/test_fs4_real_mode_wiring.py 15+ tests PASS
    - All 247 (existing) + ~80 (new across FS1-FS4) pytest PASS
    - ruff + mypy --strict зелёные
    - Re-run code-reviewer + code-auditor → verdict APPROVE / SAFE TO MERGE
  **safety_gates:**
    - L1+L2+L3 full activation — final integration test post-hardening
  **destructive_actions:** []
  **checkpoint:** true
  **estimated_retries_allowed:** 3

### Current

- **id:** FS3
  **title:** Budget aggregator + atomic concurrency + retro overwrite protection + subprocess timeouts
  **surface:** backend-python
  **spec_section:** 285-380
  **depends_on:** [FS2]
  **started:** 2026-05-16 17:55 UTC
  **workflow:** workflows/backend-python.md (adapted — stdlib edits, no new gateway)
  **acceptance:**
    - runtime/budget.py: TokenUsage dataclass + MODEL_PRICING_USD_PER_MTOK (Opus 4.7, Sonnet 4.6, Haiku 4.5) + usd_cost(Decimal) + NaN/inf/negative guard
    - state/db.py::upsert_budget: additive (SET spent_usd = budget_tracker.spent_usd + excluded.spent_usd); BEGIN IMMEDIATE transaction
    - BudgetGuard.enforce_*: transactional read+update в одной BEGIN IMMEDIATE
    - BudgetGuard.enforce_day(spent_today_usd, daily_limit_usd) — новый метод, wired в orchestrator main loop
    - NaN/inf/negative spent_usd → halt + audit critical
    - memory/levels.py record_*_lesson: exclusive create (open("x")); refuse overwrite; append_retro_artifact для legit append с timestamp suffix
    - memory/gates.py can_promote_wave: content schema check (frontmatter required, min 200 chars)
    - все await proc.communicate() в tools/merge.py, state.py, retro.py, runtime/worker_spawn.py через asyncio.wait_for(timeout=300); proc.kill() + audit on timeout
    - state/db.py claim_next_event: atomic UPDATE ... RETURNING (или BEGIN IMMEDIATE + SELECT + UPDATE)
    - tests/test_fs3_budget_concurrency.py 20+ tests: concurrent gather → exactly one passes; additive upsert; inf → halt; retro overwrite → FileExistsError; subprocess timeout → fire 300s
  **safety_gates:**
    - L2 budget hard-cap (race-free)
    - L2 retro hard gate (content-validated, not size-based)
  **destructive_actions:** []
  **checkpoint:** false
  **estimated_retries_allowed:** 3
  **retry_count:** 0
  **worker_branches:** []

### Completed

- **id:** FS2
  **completed:** 2026-05-16 17:55 UTC
  **commit:** 4df0e96
  **files_changed:** 8 (+1201 -81)
  **tests:** 91 new (test_fs2_safety_hardening.py) + updates to test_s2_tools.py & test_s4_safety.py — 366 total PASS, 0 fail
  **closed_blockers:** B2, B3, B4, C5, M3, M8
  **summary:** Safety hooks hardening. _scan_bash перепиcан с shlex.shlex(punctuation_chars=";&|") + canonical flag parser (combined shorts -rfu → -r -f -u, long=value normalize). Sub-command split на ;/&&/|||| + strip env-prefix + strip sudo wrapper + skip git global flags (-c/-C/--git-dir=/--namespace=/--exec-path=/--config-env=). Deny matrix: rm -rf (вкл. --recursive=true), git push -f/+refspec/HEAD:main, git commit --no-verify (вкл. -nm + env GIT_*_NO_VERIFY=*), git reset --hard, git clean -f, subshell vectors ($(...), backticks, ${...}, <(...), bash -c, eval, exec, source). _scan_filesystem_write: removed relative-path early-return; resolves против tool_input.cwd → BMAD_WORKER_WORKTREE env; missing → deny cwd_unknown; wires validate_worker_write_path. BMAD_ALLOW_MAIN_MERGE env заменён на one-shot signed token .claude/main-merge-token.json (secrets.token_urlsafe(32) + compare_digest + TTL 300s + used flag + 0o600). Hook делает read-only has_active_token, tool burns token через consume_token. git_merge tool refuses target=main без signed_token; non-main targets через validate_merge_target. Bot callback whitelist {stop:, proposal:, merge:, confirm:, cancel:}; unknown prefix → log + audit drop.

- **id:** FS1
  **completed:** 2026-05-16 17:05 UTC
  **commit:** 5d2bef9
  **files_changed:** 9 (+554 -51)
  **tests:** 28 new (test_fs1_secret_hygiene.py) — 275 total PASS, 0 fail
  **closed_blockers:** B6, B7, B8, H5, M10, H13
  **summary:** Secret hygiene baseline: agent.safety.audit scrubs sk-ant-/telegram-token/ghp_/AKIA/Bearer/URL-creds before write + 0o600. bot.audit drops `original` by default; opt-in HMAC-SHA256 forensics log via BMAD_AUDIT_KEEP_ORIGINAL=1+BMAD_AUDIT_HMAC_KEY. runtime.worker_spawn ALLOWED_WORKER_ENV strips API_KEY/TOKEN/SECRET/AWS_*/etc. gh_client uses urllib (token in headers, not curl argv). bot/main.py + cli/main.py call os.umask(0o077). pii_detector PHONE_RU lookbehind extended (`:;,/`) + 8\d{10} format; PATH_LIKE start-of-pattern lookahead (?!\S*@) so `/var/lib/foo@host.com` doesn't suppress EMAIL detection.

## Safety Gates Triggered
(none)

## Blockers / Pauses
(none)

## Decisions Log

- **date:** 2026-05-16 04:30 UTC
  **session:** bootstrap
  **decision:** Создавать integration FROM integration/orchestrator_agent (НЕ от main); manual bootstrap (skip auto-loop-spec bootstrap mode) чтобы пере-использовать существующий код.
  **rationale:** Нам нужен код S1-S8 чтобы фиксить P0 blockers. Auto-loop-spec bootstrap создаёт integration от main, что бы removed S1-S8 целиком.
  **impact:** После завершения security_fixes — manual merge ОБЕИХ integration branches на main (сначала integration/orchestrator_agent, затем integration/orchestrator_agent_security_fixes; ИЛИ только security_fixes если он включает scaffold base).

- **date:** 2026-05-16 17:00 UTC
  **session:** FS1
  **decision:** Lazy import `from bmad_orchestrator.agent.tools._common import runs_dir` внутри `audit_log_path()` и `telegram_audit_path()`.
  **rationale:** Pre-existing circular dep (`agent.safety.audit ↔ agent.tools._common`) blocked test collection. Discovered when first attempting to run FS1 tests; verified on parent branch — same failure (S4 commit fcc3dd3 introduced the cycle but tests were never actually executed).
  **impact:** Cycle broken; 247 prior tests + 28 new FS1 tests collect & pass. No behavioral change.

- **date:** 2026-05-16 17:00 UTC
  **session:** FS1
  **decision:** PATH_LIKE использует start-of-pattern lookahead `(?!\S*@)` вместо post-match `(?![^/]*@)` из спеки.
  **rationale:** Spec wording позволял regex backtrack: ` /var/lib/foo@host.com` → backtracks to ` /var/lib`, post-match position has `/` next, `[^/]*` matches empty, `@` ≠ `/` → lookahead succeeds → PATH_LIKE matches → EMAIL detection inside the path span gets suppressed by safelist mask. Start-of-pattern `(?!\S*@)` rejects the entire token whenever `@` exists later in the same non-whitespace sequence.
  **impact:** EMAIL detection no longer suppressed by email-bearing path tokens. Pure paths (`/var/lib/foo`) still match.

- **date:** 2026-05-16 17:55 UTC
  **session:** FS2
  **decision:** Заменить `BMAD_ALLOW_MAIN_MERGE` env-флаг на one-shot signed token в файле `.claude/main-merge-token.json` (TTL 300s + used flag + 0o600).
  **rationale:** Env vars наследуются дочерними процессами, persist через session, видны через `/proc/PID/environ`. Signed token: (1) exclusive create + chmod 0o600 — viewable только owner; (2) TTL 300s — auto-expire даже если оператор забыл revoke; (3) used=true flag — single-use semantics, никакой replay даже внутри TTL; (4) secrets.compare_digest — constant-time compare закрывает timing leaks; (5) revoke_token() удаляет файл атомарно. Совместимо с `Auto merge: false` (operator явно генерит token перед manual merge).
  **impact:** Любой merge в main теперь требует генерации валидного токена перед operation. Hook делает read-only `has_active_token()` проверку (чтобы allow всю последовательность `git checkout main && git merge --no-ff ...`); сам `git_merge` tool сжигает токен через `consume_token()` на финальном merge step. Token-path можно переопределить через `BMAD_MAIN_MERGE_TOKEN_PATH` env (тесты).

- **date:** 2026-05-16 17:55 UTC
  **session:** FS2
  **decision:** Subshell vectors (`$(...)`, backticks, `${...}`, `<(...)`) проверяются substring-scan'ом по raw command string ДО shlex tokenize.
  **rationale:** shlex с posix=True снимает quotes — `echo "$(rm -rf /)"` после tokenize становится `echo $(rm -rf /)`. Но bash расширяет command substitution внутри double quotes — поэтому даже quoted `$(...)` опасен. Substring-scan по сырой строке ловит ВСЕ варианты включая literal-string кейсы где quoting "защитил" бы expansion. False positives возможны (echo с literal `$(` в single quotes), но conservative deny приемлемее для PreToolUse.
  **impact:** Любая команда содержащая `$(`, ```, `${`, `<(` сразу denied с pattern_id `subshell_*`, regardless of quoting context. Известный side-effect: blocks legitimate `printf '$(date)'` — но такие cases должны идти через ad-hoc operator override (generate-token-style mechanism в будущем).

## Journal

[2026-05-16 04:30 UTC] bootstrap: manual создание tracker + integration/orchestrator_agent_security_fixes FROM integration/orchestrator_agent + backup/orchestrator_agent_security_fixes-pre-2026-05-16. 4 fix-сессии запланировано (все surface=backend-python). Runtime=loop_wrapper, Delay=600s, Auto merge=false. Spec: spec/spec_orchestrator_agent_security_fixes.md v0.1.

[2026-05-16 16:30 UTC] FS1 promoted to Current — workflow=backend-python (adapted: stdlib edits, no new gateway). Targets: agent/safety/audit.py, bot/audit.py, runtime/worker_spawn.py, imports/from_bad/gh_client.py, cli/main.py, bot/main.py, bot/pii_detector.py + tests/test_fs1_secret_hygiene.py.

[2026-05-16 17:05 UTC] FS1 completed (commit 5d2bef9): B6/B7/B8/H5/M10/H13 closed. 28 new tests + 247 prior → 275 PASS. Ruff clean. 9 files changed (+554 -51). Side-effect: fixed pre-existing circular import (agent.safety.audit ↔ agent.tools._common) via lazy import — added to Decisions Log. Auto merge=false → no main merge. Runtime=loop_wrapper → exit cleanly, wrapper handles next iteration.

[2026-05-16 17:05 UTC] FS2 promoted to Current — workflow=backend-python. Targets: agent/safety/hooks.py (shlex/token parse), agent/tools/merge.py + branch_validator.py (signed-token main-merge gate), bot/handlers.py (callback whitelist) + tests/test_fs2_safety_hardening.py 30+ PoC bypass tests.

[2026-05-16 17:55 UTC] FS2 completed (commit 4df0e96): B2/B3/B4/C5/M3/M8 closed. 91 new tests + 275 prior → 366 PASS, 0 fail. Ruff clean. 8 files changed (+1201 -81). Highlights: shlex.shlex(punctuation_chars=";&|") tokenize закрывает substring-bypass family (echo a; rm -rf /); canonical flag parser ловит rm --recursive=true / -fR / -rfu; git -c key=val push --force теперь parse'ит global flags; BMAD_ALLOW_MAIN_MERGE env заменён signed-token файлом с TTL+used+0o600; B2 относительные пути ресolved через cwd → worktree env, missing → deny cwd_unknown; M8 callback whitelist {stop:,proposal:,merge:,confirm:,cancel:} + audit drop. Auto merge=false → no main merge. Runtime=loop_wrapper → exit cleanly.

[2026-05-16 17:55 UTC] FS3 promoted to Current — workflow=backend-python (adapted: stdlib edits, no new gateway). Targets: runtime/budget.py (TokenUsage + MODEL_PRICING + usd_cost + NaN/inf/negative guard), state/db.py (additive upsert_budget + BEGIN IMMEDIATE + atomic claim_next_event), agent/safety/budget_guard.py (transactional enforce + enforce_day), memory/levels.py (exclusive-create retro), memory/gates.py (content schema check), agent/tools/merge.py + state.py + retro.py + runtime/worker_spawn.py (asyncio.wait_for(timeout=300) на все proc.communicate()) + tests/test_fs3_budget_concurrency.py.

## Final Report
(empty — last session not yet completed)
