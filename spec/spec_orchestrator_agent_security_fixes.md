# Spec — Orchestrator Agent Security Fixes

**Дата:** 2026-05-16
**Версия:** 0.1
**Цель:** закрыть P0 blockers + критичные P1 issues из code-review + security audit `integration/orchestrator_agent` (verdict: REJECT — FIX BLOCKERS FIRST) перед merge на main.
**Базовая ветка:** `integration/orchestrator_agent` (НЕ main — нужен код с бугами чтобы фиксить).
**Backup branch:** `backup/orchestrator_agent_security_fixes-pre-2026-05-16` (snapshot текущей integration перед фиксами).
**Integration branch:** `integration/orchestrator_agent_security_fixes`.
**Auto merge:** false (manual merge ОБЕИХ веток на main после повторного review pass).

---

## 1. Контекст

После S1..S8 MVP scaffold (commit `aef0501` на `integration/orchestrator_agent`) запущено code-review (code-reviewer agent → APPROVE WITH CHANGES, 10 blocking) + security audit (code-auditor agent → REJECT, 8 P0 + 15 P1 + 10 P2). Сводный verdict: **REJECT — FIX BLOCKERS FIRST**.

Эта спека покрывает **B1-B12 P0 blockers + критичные P1**. Деление на 4 fix-сессии — по тематической связности и инвалидируемой поверхности.

### Деферрено явно (НЕ в scope этой инициативы)
- H1 (rmtree TOCTOU), H2 (sync_skill_patches symlinks) — pre-pilot нет реальных workers
- H9, H14 (PID ownership registry) — single orchestrator process по spec'е
- H13 — частично (RU phone gaps включены в FS1)
- M1 `--dangerously-skip-permissions` в launcher.sh — opt-in env var в follow-up
- M2 pre-action-snapshot.sh `set -e` — follow-up
- M4 parse_story_md bounds — defer
- M6 cli daemon env scrub — частично через FS1
- M7 model name validation — defer
- M9 homoglyph normalization в intent-router — defer

---

## 2. Stack / Constraints

- Python 3.11+, ruff + mypy --strict, pytest
- Без новых deps (используем `shlex`, `fcntl`, `secrets`, `os.umask` из stdlib + уже установленный `aiosqlite`, `pydantic`, `presidio-analyzer`)
- Все фиксы должны сохранять текущие 247 pytest PASS + добавить new tests на каждый closed blocker
- Никаких backwards-compat shims — это greenfield

---

## 3. Цель completion (определение «готово»)

После всех 4 fix-сессий:
- 247 + новых tests PASS
- ruff + mypy --strict зелёные
- Все B1-B12 закрыты, доказано через tests (PoC bypass попытки → reject)
- Повторный code-reviewer + code-auditor → verdict «APPROVE» (без BLOCKING / без CRITICAL P0)
- Manual merge integration/orchestrator_agent + integration/orchestrator_agent_security_fixes на main одной операцией

---

## 4. Session Plan

4 сессии, все surface=`backend-python`, code-only (`destructive_actions: []`).

### FS1 — Secret hygiene (B6, B7, B8 + H5 + M6 + M10 + H13 RU PII)
- **surface:** backend-python
- **spec_section:** lines 95-180
- **depends_on:** []
- **destructive_actions:** []
- **checkpoint:** false
- **acceptance:**
  - **B6** — `agent/safety/audit.py` (record_audit): scrub `tool_input` через `scrub_output` + secret-pattern filter (regex: `sk-ant-[A-Za-z0-9_-]{40,}`, `\d{8,10}:[A-Za-z0-9_-]{35}` (Telegram), `ghp_[A-Za-z0-9]{36}` (GitHub PAT), `AKIA[0-9A-Z]{16}` (AWS), `Bearer\s+[A-Za-z0-9_.-]{20,}` (generic), `https?://[^:]+:[^@]+@` (URL creds)) перед write. File create через `os.open(..., O_CREAT | O_WRONLY | O_APPEND, 0o600)`. Hooks вызывают этот же sanitizer.
  - **B7** — `bot/audit.py`: убрать поле `original=raw` из всех записей (оставить `redacted` + `pii_categories` для forensics). Если spec требует original для дебага — opt-in через env `BMAD_AUDIT_KEEP_ORIGINAL=1` + write в отдельный файл `telegram-original.jsonl` с permissions 0600 + HMAC over redacted+original.
  - **B8** — `runtime/worker_spawn.py`: построить `merged_env` через allow-list (НЕ `dict(os.environ)`):
    ```python
    ALLOWED_WORKER_ENV = {"PATH", "HOME", "USER", "LANG", "LC_ALL", "TZ", "PWD", "SHELL", "TERM"}
    merged_env = {k: os.environ[k] for k in ALLOWED_WORKER_ENV if k in os.environ}
    # Anthropic API key — только если worker должен звать LLM (S2 design — НЕ должен)
    # Все *_TOKEN, *_SECRET, *_API_KEY, *_KEY, ANTHROPIC_*, TELEGRAM_*, OPENAI_*, YANDEX_*, GOOGLE_*, GH_*, GITHUB_* — STRIPPED
    ```
  - **H5** — `imports/from_bad/gh_client.py`: заменить `curl -H "Authorization: Bearer {token}"` на `urllib.request.Request(headers={"Authorization": f"Bearer {token}"})` (stdlib, не argv-видимый). Либо если оставлять curl — через `--config -` stdin.
  - **M10** — `os.umask(0o077)` в `cli/main.py` entrypoint + `bot/main.py` entrypoint
  - **H13** — `bot/pii_detector.py`: PHONE_RU lookbehind расширить до `(?:(?<=\s)|(?<=^)|(?<=[(\[:;,/]))` + alternative `8\d{10}`. PATH_LIKE — negative lookahead `(?![^/]*@)` чтобы не глотать email-like spans.
  - **Tests:** `tests/test_fs1_secret_hygiene.py` — 15+ tests: каждый secret-pattern не утекает в audit; worker subprocess env не содержит `*_API_KEY`/`*_TOKEN`/`*_SECRET`; telegram audit без `original`; PHONE_RU с `:`-prefix и 8-prefix ловится; email внутри path mask'ится.

### FS2 — Safety hooks hardening (B2, B3, B4 + C5 substring bypass + M3 + M8 callback)
- **surface:** backend-python
- **spec_section:** lines 185-280
- **depends_on:** [FS1]
- **destructive_actions:** []
- **checkpoint:** true
- **acceptance:**
  - **B2** — `agent/safety/hooks.py::_scan_filesystem_write`: убрать early-return на `if not candidate.is_absolute()`. Resolve relative paths против `tool_input.get("cwd")` (если есть) или worker's `worktree` (передаваемый через hook context). Если cwd не известен — отказ с `code="cwd_unknown"`.
  - **B2** — `_scan_filesystem_write` дополнительно вызывает `validate_worker_write_path(resolved, worktree_root)` (already exists in `branch_isolation.py`, нужно wire'нуть).
  - **B3 + C5** — `agent/safety/hooks.py::_scan_bash`: полностью переписать:
    1. `shlex.split(command, posix=True)` → tokens
    2. Split составной command по `;`, `&&`, `||`, `|` → list of sub-commands
    3. На каждом sub-command: strip leading env-prefixes (tokens `K=V`), strip leading absolute paths
    4. Program = first non-env token, basename
    5. Если program in {`rm`, `git`, `curl`, `wget`, `dd`, `mkfs`, `chmod`, `chown`}: canonicalize flags (split combined shorts: `-rfu` → `["-r","-f","-u"]`; normalize long↔short: `--recursive` ≡ `-r`, `--force` ≡ `-f`)
    6. Deny matrix:
       - `rm` AND (`-r`/`-R`/`--recursive`) AND (`-f`/`--force`) AND NOT (`-i`/`--interactive`) → reason `rm_recursive_force`
       - `git push` AND (`-f`/`--force`/`--force-with-lease` OR any positional starts with `+`) → reason `git_push_force`
       - `git commit` AND (`--no-verify`/`-n` in flags after `commit`) → reason `git_no_verify`
       - `git commit` AND env contains `GIT_COMMIT_NO_VERIFY=1`-like (any `GIT_*_NO_VERIFY` set) → same reason
       - `git reset` AND (`--hard`) AND positional includes `main`/`master`/`origin/main`/`origin/master`/`HEAD~*` → reason `git_reset_hard_protected`
       - any sub-command contains `$(...)` literal substring OR backticks OR `bash -c`/`sh -c`/`eval`/`exec`/`source` → reason `subshell_unsafe`
       - `git merge` AND target in {`main`,`master`} → reason `git_merge_into_protected_branch` (см. B4)
  - **B4** — `agent/tools/merge.py::git_merge`: на top вызвать `validate_merge_target(target_branch, has_human_approval=...)`. Refuse на `(False, reason)`. Если `target ∈ {"main", "master"}` — refuse независимо от approval unless explicit signed token (см. M3).
  - **M3** — `BMAD_ALLOW_MAIN_MERGE` env var заменить на one-shot signed token: файл `.claude/main-merge-token.json` `{token: secrets.token_urlsafe(32), expires_at: <unix_ts+300>, used: false}`. После одного use → `used=true`, refuse повтор. Если файл отсутствует / просрочен / used → refuse merge на main.
  - **M8** — `bot/handlers.py::handle_callback`: whitelist allowed callback_data prefixes (`{"stop:", "proposal:", "merge:", "confirm:", "cancel:"}`). Unknown prefix → log warning, ignore (НЕ forward to agent).
  - **Tests:** `tests/test_fs2_safety_hardening.py` — 30+ tests PoC bypass: `rm  -rf` (2 spaces), `rm --recursive --force`, `rm -fR`, `rm -Rf`, `git push -fu`, `git push origin +main`, `git push origin HEAD:main`, `git commit -nm "msg"`, `env GIT_COMMIT_NO_VERIFY=1 git commit`, `echo a; rm  -r --force /tmp/x`, `foo $(rm -rf x)`, `bash -c "rm -rf /"`, `git -c protocol.version=2 push --force`. Все → deny. Relative path `../../../etc/passwd` через worker cwd → deny. `git_merge target=main` без signed token → deny; с token → allow + token used. Callback prefix `evil:` → ignored.

### FS3 — Budget aggregator + concurrency hardening (B5 + H11 + H15 + M5)
- **surface:** backend-python
- **spec_section:** lines 285-380
- **depends_on:** [FS2]
- **destructive_actions:** []
- **checkpoint:** false
- **acceptance:**
  - **B5** — `runtime/budget.py`: создать (сейчас пуст). Public API:
    ```python
    @dataclass(frozen=True)
    class TokenUsage:
        input_tokens: int
        cache_creation_input_tokens: int   # 1h cache writes
        cache_read_input_tokens: int       # cache hits
        output_tokens: int

    MODEL_PRICING_USD_PER_MTOK: dict[str, dict[str, float]] = {
        # claude-opus-4-7: input $15, output $75, cache write 1h $18.75, cache read $1.50 (per Anthropic 2026 pricing)
        # claude-sonnet-4-6: input $3, output $15, cache write 1h $3.75, cache read $0.30
        # claude-haiku-4-5: input $1, output $5, cache write 1h $1.25, cache read $0.10
        ...
    }

    def usd_cost(model: str, usage: TokenUsage) -> Decimal: ...
        # Decimal не float — финансовые расчёты
        # NaN/inf/negative → ValueError
    ```
  - **B5** — `state/db.py::upsert_budget`: заменить `SET spent_usd = excluded.spent_usd` на `SET spent_usd = budget_tracker.spent_usd + excluded.spent_usd` (additive). Same для `spent_tokens`. Транзакция оборачивается `BEGIN IMMEDIATE` для exclusive lock.
  - **B5** — `agent/safety/budget_guard.py::enforce_*`: проверка через transactional read через `BEGIN IMMEDIATE` + UPDATE одной транзакцией. Race-free check-and-reserve.
  - **B5** — daily_limit_usd enforce: новый `BudgetGuard.enforce_day(spent_today_usd, daily_limit_usd)`. Wired в caller'е (orchestrator main loop).
  - **B5** — NaN/inf/negative guards: `if not math.isfinite(spent_usd) or spent_usd < 0: treat as halt + audit critical`
  - **H11** — `agent/memory/levels.py::record_tactical_lesson` / `record_strategic_lesson`: заменить `out.write_text(...)` на `out.open("x")` (exclusive create) — refuse overwrite. Если файл существует — append-only через новую функцию `append_retro_artifact` с timestamp suffix. Content schema check (frontmatter `wave:`, `date:`, `lessons:` fields required, min 200 chars body) перед declare "done".
  - **H11** — `agent/memory/gates.py::can_promote_wave`: добавить content schema check (не только `stat().st_size > 0`).
  - **H15** — все `await proc.communicate()` в `agent/tools/merge.py`, `state.py`, `retro.py`, `runtime/worker_spawn.py` обернуть в `asyncio.wait_for(..., timeout=300)`. На timeout: `proc.kill()` + emit `subprocess_timeout` audit event + return error.
  - **M5** — `state/db.py::claim_next_event`: переписать через `UPDATE event_queue SET consumed_at = ? WHERE id = (SELECT MIN(id) FROM event_queue WHERE consumed_at IS NULL) AND consumed_at IS NULL RETURNING *` (atomic в SQLite 3.35+). Если нет RETURNING — `BEGIN IMMEDIATE` + SELECT + UPDATE in single transaction.
  - **Tests:** `tests/test_fs3_budget_concurrency.py` — 20+ tests: 10 concurrent `asyncio.gather` worker spawn attempts при `spent=$45, halt=$50` → точно один проходит. `upsert_budget` дважды по $25 → итог $50, не $25. `BudgetGuard.enforce_*` с `float("inf")` → halt + audit. Retro overwrite attempt (вторая запись в тот же файл) → `FileExistsError`. Retro с пустым content / без frontmatter → `can_promote_wave = False`. Subprocess hang sim → timeout fire в 300s.

### FS4 — Real-mode SDK wiring + bot cross-process bridge (B1 + B9 + B10 + B11 + B12) (CHECKPOINT)
- **surface:** backend-python
- **spec_section:** lines 385-490
- **depends_on:** [FS3]
- **destructive_actions:** []
- **checkpoint:** true
- **acceptance:**
  - **B1** — verify реальная сигнатура `ClaudeAgentOptions` через `mcp__context7__query-docs` (library: `claude-agent-sdk-python`). Переписать `agent/run.py::build_agent_options` под актуальную API. Удалить `_attempt_sdk_run` soft-fail на `TypeError` (превращать в `RuntimeError` чтобы видеть реальную проблему).
  - **B1** — `agent/run.py::run_orchestrator`: real-mode (`mock=False`) должен либо реально работать (полный event loop с dispatch worker spawn'ов), либо `raise NotImplementedError("real mode requires Wave 1a pilot wiring; use --mock for now")` с loud `log.error`. НЕ silent no-op.
  - **B10** — `agent/system_prompt.py`:
    - `_load_project_context(target_project_path)` → читает `<target>/CLAUDE.md`, `<orchestrator>/CLAUDE.md`, `<target>/_bmad-output/planning-artifacts/epics.md`, `<target>/_bmad-output/implementation-artifacts/sprint-status.yaml`. Concat с разделителями. Размер cap 25K токенов (truncate с warning если больше).
    - `_operational_rules()` → загружает actual rules из spec §17 disambiguation rules + section §9 safety + section §10 IN-scope. Cached через `@lru_cache(maxsize=1)`.
    - `_few_shot_examples()` → 10-15 пар «русский свободный текст → tool call» из spec §17.5. Cached.
    - Все 3 имплементировать, удалить TODO-стубы. Cache_control `{"type":"ephemeral","ttl":"1h"}` на каждом большом блоке (project_context, operational_rules, few_shot).
  - **B11** — `agent/tools/__init__.py` + per-tool decorators: verify через context7 что `claude-agent-sdk` поддерживает `defer_loading=True` на `@tool` decorator. Если поддерживает — добавить на все 34 tools. Если НЕ поддерживает — открыть GitHub issue + в spec §22 deferred items зафиксировать «awaiting SDK API», а в коде сделать manual split: top 5 always-on tools (`start_wave`, `get_status`, `stop_orchestrator`, `escalate_to_human`, `read_memory`) + 29 search-on-demand через Tool Search Tool beta.
  - **B9** — `bot/handlers.py`:
    - `_HUMAN_RESPONSES: dict[int, list[tuple[str, asyncio.Future[str]]]]` (per-chat FIFO с correlation_id)
    - `forward_to_agent` создаёт `corr_id = secrets.token_hex(8)`, appends `(corr_id, future)` в list для chat_id
    - `deliver_human_response(chat_id, corr_id, text)` находит future по corr_id, удаляет из list, set_result
    - Cap на 100 in-flight requests суммарно — выше = reject с `"queue_full"`
    - `_HUMAN_RESPONSES` глобал → переписать как singleton class с `attach_event_loop` / `bus` connection
  - **B9 cross-process** — заменить in-process `_BUS` на cross-process bridge через `state.db.event_queue` (таблица уже есть). `bot.handlers.forward_to_agent` insert'ит event row → orchestrator poll'ит `claim_next_event` → emit `HUMAN_RESPONSE` обратно в queue → bot subscriber poll'ит. Latency target: <500ms.
  - **B9** — wire `agent/run.py::run_orchestrator` subscriber на `EventType.HUMAN_QUERY` → dispatch через intent-router → emit `HUMAN_RESPONSE` обратно с corr_id.
  - **B12** — `agent/tools/spawn.py::spawn_worker(real=True)`: если `claude` бинарь не найден / fallback в mock — `log.warning("real_mode_fallback_to_mock", reason=...)` + return payload `{"mock": True, "fallback_reason": "claude_binary_not_found", "real_requested": True}`. Caller (run.py) должен fail-loud если получил такой payload (`if handle.mock and handle.real_requested: raise RuntimeError(...)`).
  - **Tests:** `tests/test_fs4_real_mode_wiring.py` — 15+ tests: `build_agent_options` возвращает schema совместимый с актуальной `ClaudeAgentOptions` (тест строит реальный объект); `system_prompt` без TODO-стубов (assert no "TODO" substring); concurrent `forward_to_agent` calls — 5 messages, 5 ответов с правильным mapping по corr_id; queue_full reject after 100; bot→DB→orchestrator→DB→bot round-trip через mock DB <500ms; `spawn_worker(real=True)` без claude binary → raise + warning log.
  - **Final check:** все 247 (existing) + ~80 (new across FS1-FS4) pytest PASS. ruff + mypy --strict зелёные. Re-run code-reviewer + code-auditor agents → verdict APPROVE / SAFE TO MERGE.

---

## 5. Post-completion (после FS4)

1. Final commit с FINAL REPORT в tracker
2. PushNotification: «security_fixes complete. Ready for human re-review + merge BOTH integration branches на main»
3. Manual merge sequence (operator):
   ```bash
   git checkout main
   git merge --no-ff integration/orchestrator_agent -m "merge orchestrator_agent S1..S8 (MVP scaffold)"
   git merge --no-ff integration/orchestrator_agent_security_fixes -m "merge security fixes FS1..FS4"
   # ИЛИ одной операцией если security_fixes уже включает orchestrator_agent base:
   git merge --no-ff integration/orchestrator_agent_security_fixes -m "merge MVP scaffold + security fixes"
   ```
4. Cleanup: keep backup branches для отката, delete integration branches после успешного merge.

---

**End of spec v0.1**
