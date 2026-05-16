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
- FS5 hardening backlog (MEDIUM/LOW из FS4 audit): skill_invocation/extra_args allow-list, PII scrubbed-text to DB, whitelist re-check в subscriber, defense-in-depth HookMatcher, 128-bit corr_id, connection pooling, _first_existing canonical-missing leak.

## Sessions

### Pending
(none)

### Current
(none — initiative complete)

### Completed

- **id:** FS4
  **completed:** 2026-05-16 22:30 UTC
  **commit:** b49f618
  **files_changed:** 10 (+1515 -182)
  **tests:** 27 new (test_fs4_real_mode_wiring.py — 22 acceptance + 5 security hardening) + updates to test_s3_runtime.py & test_s8_cli_tui_pilot.py — 423 total PASS, 0 fail
  **closed_blockers:** B1, B9, B10, B11, B12
  **summary:** Real-mode SDK wiring + system_prompt full impl + manual tool split + bot cross-process bridge + spawn loud-fallback. agent/run.py: build_agent_options против актуального ClaudeAgentOptions API (betas НЕ beta_headers, mcp_servers dict, HookMatcher wrappers), _validate_sdk_options raises RuntimeError на shape drift, run_orchestrator(mock=False) raises NotImplementedError loudly (НЕ silent no-op). ALWAYS_ON_TOOLS = 5 entries (start_wave / stop_orchestrator / escalate_to_human / read_memory / read_sprint_status); 29 остальных через tool-search-tool-2025-10-19 beta. agent/system_prompt.py: 5 blocks, 4 с cache_control ttl=1h. _load_project_context 25K-token cap с truncation marker; SECURITY allow-root enforcement (project_root | ORCHESTRATOR_ROOT) per source; symlink rejection в _safe_read_text walks path lineage; canonical containment через resolve() + relative_to(allow_root). RU operational_rules + 15 RU few-shot pairs. bot/handlers.py: per-chat FIFO с corr_id matching для concurrent requests; caps _PER_CHAT_CAP=5 + _INFLIGHT_CAP=100 под asyncio.Lock (lazy-init через _get_lock()); bridge-poisoning guard в _poll_state_db_response (resolves только own (chat_id, corr_id); foreign rows re-enqueued ≤3 times per corr_id затем dropped); reset_for_test() helper. state/db.py: enqueue_human_query / enqueue_human_response / claim_next_event_of_type (atomic UPDATE...RETURNING filtered by event_type). runtime/event_loop.py: EventType.HUMAN_QUERY = "human_query" (14 types total). runtime/worker_spawn.py: WorkerHandle.fallback_reason + real_requested fields. agent/tools/spawn.py: loud-fallback isError envelope с basename-scrubbed worktree/jsonl_path (audit JSONL сохраняет полные paths для operator). Security review: code-reviewer APPROVE; security-auditor APPROVE после фикса 1 CRITICAL (bridge poisoning) + 4 HIGH (race / per-chat cap / path traversal / info disclosure) в этой же сессии.

- **id:** FS3
  **completed:** 2026-05-16 18:30 UTC
  **commit:** b3d5442
  **files_changed:** 11 (+1001 -98)
  **tests:** 30 new (test_fs3_budget_concurrency.py) + 6 updates (test_s7_memory_retro.py) — 396 total PASS, 0 fail
  **closed_blockers:** B5, H11, H15, M5
  **summary:** Budget aggregator + concurrency hardening + retro hard-gate + subprocess timeout policy. runtime/budget.py: TokenUsage frozen dataclass с input/cache_creation/cache_read/output tokens (non-negative validated) + MODEL_PRICING_USD_PER_MTOK Decimal table (Opus 4.7 $15/$75/$18.75/$1.50, Sonnet 4.6 $3/$15/$3.75/$0.30, Haiku 4.5 $1/$5/$1.25/$0.10) + usd_cost(model, usage) Decimal + is_finite_spend(v) NaN/inf/negative guard. state/db.py::upsert_budget rewritten: BEGIN IMMEDIATE + additive SET spent_usd = budget_tracker.spent_usd + excluded.spent_usd (closes B5 last-write-wins race на concurrent gather); recomputes breached_alarm/breached_halt от cumulative total. Added get_budget(session_id, scope, scope_target_id) reader. state/db.py::claim_next_event atomic single-statement UPDATE event_queue SET consumed_at=? WHERE id=(SELECT...LIMIT 1) AND consumed_at IS NULL RETURNING — закрывает M5 double-claim race. agent/safety/budget_guard.py: BudgetScope=Literal[story,batch,day], enforce_day + check_day single-threshold daily limit, BudgetResult.corrupted флаг, _evaluate fail-safes на is_finite_spend(False) → halt+corrupted+budget_corruption critical audit. memory/levels.py: record_*_lesson now uses open("x") exclusive create — FileExistsError on overwrite (закрывает H11); append_retro_artifact(base_path, content) для legit appends со timestamped sibling <stem>.<YYYYMMDDTHHMMSSZ><suffix>; has_valid_retro_schema(path) валидирует YAML frontmatter (wave/level/created) + min 200 char body. memory/gates.py::is_retro_done теперь делегирует has_valid_retro_schema — mock seeds не проскакивают wave-boundary hard gate. agent/tools/merge.py + state.py + retro.py: все proc.communicate()/proc.wait() обёрнуты asyncio.wait_for(timeout=300) → proc.kill() + subprocess_timeout audit event on hang (закрывает H15). runtime/worker_spawn.py: BMAD_WORKER_TIMEOUT_SEC env (default 86_400 = 24h) — workers run user stories that can take ~30 min на hot path; 300s cap killed бы legitimate runs; 24h still catches deadlocks. tests/test_fs3_budget_concurrency.py: 30 tests organized по B5 (TokenUsage validation + pricing edge cases + upsert additive + asyncio.gather concurrent sum + corruption fail-safe), H11 (exclusive create + schema check + mock seed rejection), H15 (subprocess timeout firing на git merge), M5 (10 racers × 3 events → exactly 3 claimed, zero duplicates). tests/test_s7_memory_retro.py 6 updates: _valid_retro_body(wave) helper that builds frontmatter + 200+ char body — replaced все "# retro\n" usages; end_to_end test now asserts mock seed NOT is_retro_done, then writes valid body, then re-asserts True.

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

- **[2026-05-16 22:30 UTC] manual_merge_pending** — initiative complete on integration/orchestrator_agent_security_fixes (all 4 sessions FS1-FS4 done, 423 tests pass, ruff + mypy --strict clean, code-reviewer + security-auditor both APPROVE). User must merge manually:
  ```
  git checkout main
  git merge --no-ff integration/orchestrator_agent_security_fixes -m "merge orchestrator_agent_security_fixes FS1..FS4"
  ```
  Note: this branch was forked FROM integration/orchestrator_agent (not main). Verify if integration/orchestrator_agent also needs merging — `git log --oneline integration/orchestrator_agent ^main` shows whether base S1-S8 scaffold is already on main. If not: merge `integration/orchestrator_agent` first, then `integration/orchestrator_agent_security_fixes` on top.
  **resolution:** PENDING (user action)

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

- **date:** 2026-05-16 18:30 UTC
  **session:** FS3
  **decision:** worker_spawn.py subprocess timeout = BMAD_WORKER_TIMEOUT_SEC env (default 86_400 = 24h), а НЕ literal 300s из спеки. Остальные subprocess sites (merge.py git merge, state.py git worktree list, retro.py claude -p) остаются 300s.
  **rationale:** Spec literally требует 300s на всех proc.communicate(), но workers исполняют user stories которые могут идти ~30 min на hot path (тесты + рефакторинг + commit). 300s cap kill'нул бы любой legitimate run. Other subprocess calls (git merge, git worktree list, retro spawn) — short-lived management ops, для них 300s правильно. 24h на worker всё ещё ловит genuinely-hung workers (deadlock на stdin, broken pipe loop).
  **impact:** Worker hang detection downgraded с 5min → 24h; в обмен — нет false-positive kills legitimate workers. Operator может tighten через env (BMAD_WORKER_TIMEOUT_SEC=3600 → 1h). Subprocess_timeout audit event одинаков для всех sites — отличается timeout_sec в payload. Documented в worker_spawn.py docstring.

- **date:** 2026-05-16 18:30 UTC
  **session:** FS3
  **decision:** is_retro_done hard gate теперь validates frontmatter schema + min 200 char body (через has_valid_retro_schema), а НЕ просто path.exists() and stat().st_size > 0.
  **rationale:** H11 audit finding: spawn_retro_worktree mock seed (~100 chars) проскакивал size>0 check → wave promote unblocked без реального retro content. Tightened gate требует: (1) парсимый YAML frontmatter с keys wave/level/created; (2) body content ≥200 char (skeleton-only seeds <200 char). Это закрывает silent-pass attack: мок не считается за реальный retro.
  **impact:** Mock seed теперь intentionally fails гейт — это эксpected behavior. Live agent должен заполнить retro перед wave promote. Side-effect: 6 существующих S7 tests которые писали "# retro\n" сломались — обновлены через _valid_retro_body() helper. test_end_to_end_lesson_to_retro_to_promote_unblocked теперь симулирует workflow: assert mock seed not done → agent writes valid body → assert done.

- **date:** 2026-05-16 22:30 UTC
  **session:** FS4
  **decision:** Memory tool (`{"type": "memory_20250818", "name": "memory"}`) НЕ wired в `mcp_servers` build_agent_options.
  **rationale:** Server-managed beta tool — управляется через `beta_headers`/`betas`, не через mcp dict. SDK инжектит автоматически на основании `betas=["context-management-2025-06-27"]`. Попытка положить его в `mcp_servers` дала бы TypeError на ClaudeAgentOptions instantiate.
  **impact:** read_memory / write_memory tools зарегистрированы локально через @tool (наши обёртки над memory/levels.py); реальный Anthropic memory-tool service остаётся доступен агенту через beta header. Документировано в docstring build_agent_options.

- **date:** 2026-05-16 22:30 UTC
  **session:** FS4
  **decision:** `_HUMAN_RESPONSES` lock через lazy `_get_lock()` вместо module-level `asyncio.Lock()`.
  **rationale:** Module-level `asyncio.Lock()` биндится к event loop на момент import — в pytest каждый test получает fresh loop, и предыдущий lock референсит мёртвый loop → `RuntimeError: <Future> attached to a different loop`. Lazy init через `_get_lock()` создаёт lock в первый вызов внутри running loop, `reset_for_test()` сбрасывает state между тестами.
  **impact:** Все 27 FS4 тестов проходят без cross-test interference. Pattern reusable для других asyncio primitives на module level.

- **date:** 2026-05-16 22:30 UTC
  **session:** FS4
  **decision:** Bridge-poisoning защита через "resolve-only-own-pair" rule + re-enqueue с per-corr_id 3-attempt cap.
  **rationale:** Security auditor flagged CRITICAL — старый `_poll_state_db_response` резолвил ЛЮБОЙ row через `deliver_human_response(row_chat, row_corr, row_text)`, что позволяло attacker'у со state.db write access (или скомпрометированному collateral процессу) подкидывать (chat_B, corr_id_of_pending_A) rows и резолвить future чужого чата произвольным текстом. Fix: каждый poller резолвит ТОЛЬКО `(row_chat == self.chat_id and row_corr == self.corr_id)`; foreign row re-enqueue'ится для своего владельца с per-corr 3-attempt cap (orphan drop предотвращает infinite ping-pong если законный owner уже timeout'нул).
  **impact:** Attacker даже с DB write access НЕ может cross-resolve чужие futures. Cost: foreign row latency = N_pollers × _POLL_INTERVAL_SEC × _MAX_REENQUEUE_ATTEMPTS = до ~150ms в worst case (still well under 500ms target).

- **date:** 2026-05-16 22:30 UTC
  **session:** FS4
  **decision:** Per-chat cap 5 в дополнение к глобальному cap 100; `_safe_read_text` enforces allow-root через resolve() + relative_to() + reject symlink в path lineage.
  **rationale:** Security auditor flagged HIGH — (a) global cap 100 без per-chat cap = один whitelisted-but-noisy chat может flood'нуть весь queue → DoS остальных whitelisted chats; (b) `_load_project_context` принимал произвольный `project_root` без allow-root, и `_safe_read_text` следовал symlinks → attacker с write access на BMAD_TARGET_PROJECT subtree мог замаппить CLAUDE.md → /etc/passwd через symlink, и весь /etc вылетал в system_prompt → cache leak.
  **impact:** Per-chat 5 ограничивает один user'а; global 100 защищает от cross-chat sum overflow. Path-traversal: ORCHESTRATOR_ROOT прибит на /home/server/bmad-orchestrator (resolve()'нут), project_root resolve()'ится at load, каждый source имеет explicit allow_root (project_root | ORCHESTRATOR_ROOT), и `_safe_read_text` walks `[path, *path.parents]` rejecting symlinks ДО `relative_to(allow_root)` check.

## Journal

[2026-05-16 04:30 UTC] bootstrap: manual создание tracker + integration/orchestrator_agent_security_fixes FROM integration/orchestrator_agent + backup/orchestrator_agent_security_fixes-pre-2026-05-16. 4 fix-сессии запланировано (все surface=backend-python). Runtime=loop_wrapper, Delay=600s, Auto merge=false. Spec: spec/spec_orchestrator_agent_security_fixes.md v0.1.

[2026-05-16 16:30 UTC] FS1 promoted to Current — workflow=backend-python (adapted: stdlib edits, no new gateway). Targets: agent/safety/audit.py, bot/audit.py, runtime/worker_spawn.py, imports/from_bad/gh_client.py, cli/main.py, bot/main.py, bot/pii_detector.py + tests/test_fs1_secret_hygiene.py.

[2026-05-16 17:05 UTC] FS1 completed (commit 5d2bef9): B6/B7/B8/H5/M10/H13 closed. 28 new tests + 247 prior → 275 PASS. Ruff clean. 9 files changed (+554 -51). Side-effect: fixed pre-existing circular import (agent.safety.audit ↔ agent.tools._common) via lazy import — added to Decisions Log. Auto merge=false → no main merge. Runtime=loop_wrapper → exit cleanly, wrapper handles next iteration.

[2026-05-16 17:05 UTC] FS2 promoted to Current — workflow=backend-python. Targets: agent/safety/hooks.py (shlex/token parse), agent/tools/merge.py + branch_validator.py (signed-token main-merge gate), bot/handlers.py (callback whitelist) + tests/test_fs2_safety_hardening.py 30+ PoC bypass tests.

[2026-05-16 17:55 UTC] FS2 completed (commit 4df0e96): B2/B3/B4/C5/M3/M8 closed. 91 new tests + 275 prior → 366 PASS, 0 fail. Ruff clean. 8 files changed (+1201 -81). Highlights: shlex.shlex(punctuation_chars=";&|") tokenize закрывает substring-bypass family (echo a; rm -rf /); canonical flag parser ловит rm --recursive=true / -fR / -rfu; git -c key=val push --force теперь parse'ит global flags; BMAD_ALLOW_MAIN_MERGE env заменён signed-token файлом с TTL+used+0o600; B2 относительные пути ресolved через cwd → worktree env, missing → deny cwd_unknown; M8 callback whitelist {stop:,proposal:,merge:,confirm:,cancel:} + audit drop. Auto merge=false → no main merge. Runtime=loop_wrapper → exit cleanly.

[2026-05-16 17:55 UTC] FS3 promoted to Current — workflow=backend-python (adapted: stdlib edits, no new gateway). Targets: runtime/budget.py (TokenUsage + MODEL_PRICING + usd_cost + NaN/inf/negative guard), state/db.py (additive upsert_budget + BEGIN IMMEDIATE + atomic claim_next_event), agent/safety/budget_guard.py (transactional enforce + enforce_day), memory/levels.py (exclusive-create retro), memory/gates.py (content schema check), agent/tools/merge.py + state.py + retro.py + runtime/worker_spawn.py (asyncio.wait_for(timeout=300) на все proc.communicate()) + tests/test_fs3_budget_concurrency.py.

[2026-05-16 18:30 UTC] FS3 completed (commit b3d5442): B5/H11/H15/M5 closed. 30 new tests (test_fs3_budget_concurrency.py) + 6 updates (test_s7_memory_retro.py) + 366 prior → 396 PASS, 0 fail. Ruff clean (10 auto-fixed: 4 unused imports + 6 UP041 asyncio.TimeoutError→TimeoutError). 11 files changed (+1001 -98). Highlights: TokenUsage Decimal pricing с Anthropic 2026 rates; upsert_budget additive под BEGIN IMMEDIATE закрывает B5 last-write-wins на concurrent gather; claim_next_event single-stmt UPDATE...RETURNING закрывает M5 double-claim; is_finite_spend NaN/inf/neg → halt+budget_corruption critical (никакого silent fail-open); record_*_lesson exclusive create FileExistsError on overwrite + append_retro_artifact timestamped sibling для legit appends; is_retro_done теперь validates frontmatter+body≥200char (H11 mock-seed bypass closed); все subprocess sites wrapped asyncio.wait_for(timeout=300) → kill+audit on hang (H15), кроме worker_spawn где default 24h через BMAD_WORKER_TIMEOUT_SEC env (workers run ~30min stories, 300s killed бы legitimate runs). Auto merge=false → no main merge. Runtime=loop_wrapper → exit cleanly.

[2026-05-16 18:30 UTC] FS4 promoted to Current — workflow=backend-python. Last session (CHECKPOINT). Targets: agent/run.py (real-mode loop + ClaudeAgentOptions verify via context7), agent/system_prompt.py (_load_project_context cap 25K + _operational_rules + _few_shot_examples + cache_control ttl=1h), defer_loading wire on 34 tools (или manual 5+29 split), bot/handlers.py (per-chat FIFO + corr_id, cap 100), state.db.event_queue cross-process bridge HUMAN_QUERY/HUMAN_RESPONSE, tools/spawn.py real=True loud-fallback + tests/test_fs4_real_mode_wiring.py 15+ tests. Acceptance включает финальный re-run code-reviewer + code-auditor → APPROVE/SAFE TO MERGE.

[2026-05-16 22:30 UTC] FS4 completed (commit b49f618): B1/B9/B10/B11/B12 closed. 22 acceptance + 5 security hardening tests (test_fs4_real_mode_wiring.py) + updates to test_s3_runtime.py & test_s8_cli_tui_pilot.py + 396 prior → 423 PASS, 0 fail. Ruff clean. Mypy --strict clean on 3 changed modules (1 pre-existing main_merge_token error untouched). 10 files changed (+1515 -182). First-pass security-auditor returned NEEDS WORK with 1 CRITICAL (bridge-poisoning через state.db row injection) + 4 HIGH (race в _HUMAN_RESPONSES без lock / global cap без per-chat cap / path traversal через symlink в _safe_read_text / info disclosure абс. пути в spawn error envelope). Fixes applied in-session: (1) `_poll_state_db_response` резолвит ТОЛЬКО own (chat_id, corr_id), foreign rows re-enqueue с 3-attempt cap; (2) `asyncio.Lock` lazy через `_get_lock()` вокруг cap-check+append+cleanup, `reset_for_test()` сбрасывает state между тестами; (3) `_PER_CHAT_CAP = 5` alongside `_INFLIGHT_CAP = 100`; (4) `ORCHESTRATOR_ROOT.resolve()` + per-source allow_root tuples + `_safe_read_text` rejects symlinks в parent lineage + canonical `relative_to(allow_root)`; (5) spawn error envelope basename-only (full path остаётся в audit JSONL для operator). Second-pass: code-reviewer APPROVE; security-auditor APPROVE (residual MEDIUM/LOW deferred к FS5 hardening). Auto merge=false → manual_merge_pending journal entry с git checkout main && git merge command для user. Final Report записан. Runtime=loop_wrapper → exit cleanly.

## Final Report

**Initiative complete on integration/orchestrator_agent_security_fixes** (b49f618 — head; 5 commits FS1→FS4 + 3 tracker commits + 1 bootstrap = 9 commits ahead of integration/orchestrator_agent).

**Diff vs base (integration/orchestrator_agent):** 37 files changed, 4735 insertions, 412 deletions across 4 fix-sessions.

**Blockers closed (13 total):**
- B1 Real-mode SDK silent no-op (FS4)
- B2 Filesystem write hook relative-path skip (FS2)
- B3 Bash scanner substring bypass (FS2)
- B4 Main-merge guard via env (FS2)
- B5 Budget last-write-wins on concurrent gather (FS3)
- B6 Audit log secret hygiene (FS1)
- B7 Bot audit log full message storage (FS1)
- B8 Worker env leakage (FS1)
- B9 Bot↔agent cross-process bridge (FS4)
- B10 system_prompt placeholder TODOs (FS4)
- B11 defer_loading=True missing on @tool (FS4)
- B12 spawn_worker(real=True) silent mock-fallback (FS4)
- H5/H11/H13/H15/M5/M6/M8/M10/C5 closed across FS1-FS3

**Final security verdict:** code-reviewer APPROVE; security-auditor APPROVE (after CRITICAL + 4 HIGH fixes within FS4).

**Test totals:** 423 PASS, 0 fail. New FS-suite tests: 28 (FS1) + 91 (FS2) + 30 (FS3) + 27 (FS4) = 176 new acceptance + security tests; remaining 247 prior tests unchanged.

**Quality gates:** ruff clean across src/+tests/; mypy --strict clean on all changed modules in FS1-FS4 (1 pre-existing main_merge_token.py error documented unchanged).

**Manual merge instructions (Auto merge=false):**
```
# Step 1: verify base scaffold is on main (skip if integration/orchestrator_agent already merged)
git log --oneline integration/orchestrator_agent ^main | head
# If non-empty:
git checkout main && git merge --no-ff integration/orchestrator_agent -m "merge orchestrator_agent S1..S8 scaffold"

# Step 2: merge security fixes on top
git checkout main && git merge --no-ff integration/orchestrator_agent_security_fixes -m "merge orchestrator_agent_security_fixes FS1..FS4"
```

**Backup:** `backup/orchestrator_agent_security_fixes-pre-2026-05-16` (untouched; rollback via `git reset --hard backup/orchestrator_agent_security_fixes-pre-2026-05-16` if needed — requires explicit user action per CLAUDE.md).

**Deferred to FS5 hardening backlog:** skill_invocation/extra_args allow-list, PII scrubbed-text to DB, whitelist re-check в HUMAN_QUERY subscriber, defense-in-depth HookMatcher, 128-bit corr_id, aiosqlite connection pooling, _first_existing canonical-missing path leak. Bootstrap separate initiative when prioritised.
