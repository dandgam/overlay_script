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

- **id:** FS2
  **title:** Safety hooks hardening (shlex parse) + merge gate wiring + main-merge signed token + callback whitelist (CHECKPOINT)
  **surface:** backend-python
  **spec_section:** 185-280
  **depends_on:** [FS1]
  **acceptance:**
    - _scan_filesystem_write: relative paths resolve против cwd/worktree; wire validate_worker_write_path
    - _scan_bash: shlex.split + canonical flag parser + split составных команд (;/&&/||/|)
    - Deny matrix: rm -r+-f (вкл. долгие флаги), git push -f/+refspec, git commit --no-verify (вкл. -n combined + env GIT_COMMIT_NO_VERIFY=*), git reset --hard на protected, $(...)/backticks/bash -c/eval/exec/source, git merge → main/master
    - git_merge tool: вызвать validate_merge_target; refuse target=main без signed token
    - BMAD_ALLOW_MAIN_MERGE → one-shot signed token .claude/main-merge-token.json (TTL 300s + used flag)
    - bot callback whitelist {stop:, proposal:, merge:, confirm:, cancel:}; unknown → ignored
    - tests/test_fs2_safety_hardening.py 30+ PoC bypass tests PASS (all → deny)
  **safety_gates:**
    - L1 hardened PreToolUse (token-based, not substring) — full activation
    - L3 branch isolation wired into git_merge tool
  **destructive_actions:** []
  **checkpoint:** true
  **estimated_retries_allowed:** 3

- **id:** FS3
  **title:** Budget aggregator + atomic concurrency + retro overwrite protection + subprocess timeouts
  **surface:** backend-python
  **spec_section:** 285-380
  **depends_on:** [FS2]
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

- **id:** FS1
  **title:** Secret hygiene — audit redaction, worker env allowlist, PII gaps
  **surface:** backend-python
  **spec_section:** 56-75
  **depends_on:** []
  **acceptance:**
    - audit.events.jsonl: secrets scrubbed (sk-ant-*, ghp_*, AKIA*, Bearer, URL creds), file 0600
    - telegram.jsonl: убрать original=raw field (или opt-in env + HMAC + 0600)
    - worker subprocess env: allowlist {PATH, HOME, USER, LANG, LC_ALL, TZ, PWD, SHELL, TERM}; strip *_TOKEN, *_SECRET, *_API_KEY, ANTHROPIC_*, TELEGRAM_*, OPENAI_*, YANDEX_*, GOOGLE_*, GH_*, GITHUB_*
    - gh_client.py: token через urllib headers (не argv-видимый curl)
    - os.umask(0o077) в cli/main.py + bot/main.py entrypoints
    - PII detector: PHONE_RU `:`-prefix + 8-prefix; PATH_LIKE negative lookahead email
    - tests/test_fs1_secret_hygiene.py 15+ tests PASS
  **safety_gates:**
    - L1 secret pattern filter — wired into all audit writes
  **destructive_actions:** []
  **checkpoint:** false
  **estimated_retries_allowed:** 3
  **started:** 2026-05-16 16:30 UTC
  **workflow:** .claude/skills/auto-loop-spec/workflows/backend-python.md
  **retry_count:** 0
  **worker_branches:** []

### Completed
(empty — initiative not yet started)

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

## Journal

[2026-05-16 04:30 UTC] bootstrap: manual создание tracker + integration/orchestrator_agent_security_fixes FROM integration/orchestrator_agent + backup/orchestrator_agent_security_fixes-pre-2026-05-16. 4 fix-сессии запланировано (все surface=backend-python). Runtime=loop_wrapper, Delay=600s, Auto merge=false. Spec: spec/spec_orchestrator_agent_security_fixes.md v0.1.

[2026-05-16 16:30 UTC] FS1 promoted to Current — workflow=backend-python (adapted: stdlib edits, no new gateway). Targets: agent/safety/audit.py, bot/audit.py, runtime/worker_spawn.py, imports/from_bad/gh_client.py, cli/main.py, bot/main.py, bot/pii_detector.py + tests/test_fs1_secret_hygiene.py.

## Final Report
(empty — last session not yet completed)
