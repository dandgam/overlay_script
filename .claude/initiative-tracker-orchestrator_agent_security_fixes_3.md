# Initiative Tracker — Orchestrator Agent Security Fixes Round 3 (Sandbox)

## Metadata
- **Spec:** spec/spec_orchestrator_agent_security_fixes_3.md
- **Parent specs:** security_fixes_2.md, security_fixes.md, spec_orchestrator_agent.md
- **Integration branch:** integration/orchestrator_agent_security_fixes_3
- **Base branch:** integration/orchestrator_agent_security_fixes_2 (cumulative — S1-S8 + FS1-FS6)
- **Backup branch:** backup/orchestrator_agent_security_fixes_3-pre-2026-05-16
- **Created:** 2026-05-16
- **Bootstrap completed:** 2026-05-16 manual
- **Scope frozen:** 2026-05-16
- **Runtime:** loop_wrapper
- **Delay seconds:** 600
- **Auto merge:** false

## Scope Freeze

### In scope
- 2 сессии (FS7, FS8) для перехода на OS-level sandbox (bwrap) + закрытия NH1+NH2 wiring issues
- FS7 — Worker subprocess sandbox via bwrap (primary), demote _scan_bash до defence-in-depth [CHECKPOINT]
- FS8 — NH1 cross-process shared session model + NH2 atomic budget StateDB binding в _run_mock_pilot [CHECKPOINT]

### Rationale для смены подхода
3 round'а fix-loop'ов на blacklist-based bash scanner показали что каждый round находит новые bypass'ы (round 1: 6 P0, round 2: 6 P0, round 3: 5 P0 — NC1 bash <<<, NC2 (...) subshell, NC3 brace expansion, NC4 xargs/find, NC5 pipe-to-python). Pattern recursion бесконечен. OS-level sandbox через bwrap (verified — установлен 0.9.0 + kernel 6.17 + unprivileged userns enabled) решает класс целиком.

### Out of scope
- Network whitelist через nftables — defer (sandbox default --unshare-net)
- Все H1, H2, H9, H14, M1-M9, N8 из round 1/2 — остаются deferred
- FS7-A..FS7-E fast-follows от round 3 reviewer — закроем при wave 1a pilot wiring
- Multi-process orchestrator scenarios (2+ orchestrator daemons на одной DB) — single-process per spec

## Sessions

### Pending
(empty)

### Current
(none — initiative complete, awaiting manual merge to main)

### Completed

- **id:** FS8
  **title:** NH1 shared session model + NH2 StateDB binding в _run_mock_pilot (CHECKPOINT)
  **commit:** e3f4b3a
  **files_changed:** 5
  **completed:** 2026-05-16 23:30 UTC
  **tests_passed:**
    - 11 new FS8 tests (test_fs8_shared_session_and_budget.py — 7 unit для resolve_or_create_session, 2 integration для run_orchestrator env/resolve/export, 1 regression для BudgetGuard.attach_state_db, 1 cross-process round-trip через 2× subprocess.Popen)
    - 32 FS7 + 541 baseline preserved
    - Total: 584 PASS in 9.97s
  **quality_gates:**
    - ruff check src/ tests/ — All checks passed!
    - mypy --strict на FS8-touched modules (state/db.py, agent/run.py, bot/main.py, tests/conftest.py, tests/test_fs8_*.py) — Success: no issues found in 5 source files
    - Pre-existing mypy error main_merge_token.py:40 — остаётся deferred per FS7 decision (отдельный one-liner commit, не блокирует initiative)
  **decisions:**
    - `resolve_or_create_session` возвращает `int` (соответствует существующему `create_session` API и `agent_session.id INTEGER` schema), хотя spec text писал `-> str`. Env layer (BMAD_ORCHESTRATOR_SESSION_ID) хранит `str(int)` и парсит назад — типизация чистая на каждом слое.
    - `wave: str | None`: SELECT с `wave=None` matches любой wave (bot daemon convention — bot не знает orchestrator-овский wave label); SELECT с `wave=str` — exact match. INSERT path при `wave=None` использует sentinel `"default"` (schema `wave TEXT NOT NULL`).
    - Env-priority path в `_resolve_session` валидирует session_id против actual DB через `_session_exists` ДО binding — без этой проверки stale env (test isolation, distinct deployments) приводил к FK constraint failure при последующих `budget_tracker INSERT` (caught в первом прогоне full suite — `test_s8_cli_tui_pilot::test_mock_pilot_runs_to_completion` упал с `sqlite3.IntegrityError: FOREIGN KEY constraint failed` пока env=stale_id leak'ал между тестами).
    - Cross-process round-trip test использует `subprocess.run` (cold-start), wall-clock budget loose (<30s) — spec'ные <500ms суть target для running daemon path, не для cold subprocess startup. Test проверяет convergence (bot.resolve_or_create без wave находит agent's session) и round-trip (claim_next_event_of_type вытягивает bot-inserted human_query).
    - `BudgetGuard.attach_state_db` вызывается ДО `_run_mock_pilot` (внутри `run_orchestrator` после `_resolve_session`), а не ВНУТРИ pilot — каждый `enforce_and_reserve_*` call уже видит bound DB.
  **deferred_items:**
    - main_merge_token.py:40 mypy fix — отдельный one-liner commit (per FS7 decision)
    - FS7-A..FS7-E fast-follows от round 3 reviewer — wire при wave 1a pilot run

- **id:** FS7
  **title:** Worker subprocess sandbox via bwrap + demote _scan_bash до defence-in-depth (CHECKPOINT)
  **commit:** 3053031
  **files_changed:** 7
  **completed:** 2026-05-16 22:30 UTC
  **tests_passed:**
    - 32 new FS7 tests (test_fs7_sandbox.py — Protocol/flag/factory unit + real-bwrap PoC: blocks /etc write, blocks /dev/tcp net, blocks bash <<<, blocks (...) subshell, blocks xargs, env isolation drops ANTHROPIC_API_KEY)
    - 541 baseline preserved
    - Total: 573 PASS in 9.43s
  **quality_gates:**
    - ruff check — All checks passed!
    - mypy --strict (FS7-touched modules: sandbox.py, worker_spawn.py, retro.py, hooks.py) — Success: no issues found in 4 source files
  **decisions:**
    - bwrap requires `--clearenv` ДО `--setenv` — без него host env (incl. ANTHROPIC_API_KEY) leak'ает в sandbox. Caught by test_real_sandbox_environment_isolation на первой попытке.
    - Sandbox.kind в Protocol → `@property` (read-only) вместо `kind: str` — frozen dataclass slot non-mutable; mypy --strict без property бьёт «expected settable variable».
    - `--tmpfs /tmp` помечен `# noqa: S108` — bwrap mount point внутри namespace, не host path.
    - Network policy default для workers: `github_only` (treated as `full` пока nftables whitelist deferred per spec §5); для retro: `none` (local aggregation only).
    - Severity scanner deny → `info` если sandbox активен (defence-in-depth catches не должны давать warning-grade alerts на user dashboard'е).
    - Pre-existing mypy error `main_merge_token.py:40 Returning Any from function declared to return "dict[str, Any] | None"` — verified pre-existing via git stash; unrelated к FS7. **DEFERRED** — open standalone fix.
  **deferred_items:**
    - FS7-A..FS7-E fast-follows из round 3 reviewer — wire при wave 1a pilot run
    - nftables `github_only` whitelist — defer (current behaviour = `--share-net` for non-none policies)
    - main_merge_token.py mypy fix — separate one-liner commit, не блокирует initiative

## Safety Gates Triggered
(none)

## Blockers / Pauses

- **date:** 2026-05-16 23:30 UTC
  **session:** FS8
  **type:** manual_merge_pending
  **detail:** initiative complete on integration/orchestrator_agent_security_fixes_3 — FS7 (commit 3053031) + FS8 (commit e3f4b3a). Auto merge=false → wrapper exits, user reviews and merges manually:
  ```
  git checkout main
  git merge --no-ff integration/orchestrator_agent_security_fixes_3 \
    -m "merge orchestrator_agent MVP + 3 rounds security fixes (FS7 bwrap + FS8 NH1/NH2 wiring)"
  ```
  **resolution:** PENDING (user action)

## Decisions Log

- **date:** 2026-05-16 20:30 UTC
  **session:** bootstrap
  **decision:** Round 3 переход на OS-level sandbox (bwrap) как primary safety; demote _scan_bash до defence-in-depth.
  **rationale:** 3 round'а blacklist-based scanner показали pattern recursion (5+5+5 P0 bypasses). bwrap verified установленным (0.9.0), unprivileged userns enabled — переход возможен без новых system deps.
  **impact:** L4 added (sandbox); L1 scanner roleshift с primary на defence-in-depth. FS7-A..FS7-E fast-follows из round 3 reviewer — deferred к wave 1a wiring.

- **date:** 2026-05-16 22:30 UTC
  **session:** FS7
  **decision:** `--clearenv` обязателен ДО `--setenv` в bwrap argv.
  **rationale:** bwrap по default inherits parent env. Без `--clearenv` allow-list контракт нарушается — host secrets (ANTHROPIC_API_KEY и т.д.) попадают в sandboxed worker. Caught real-bwrap test'ом первой итерации.
  **impact:** BwrapSandbox.wrap_command всегда эмитит `--clearenv` перед `--setenv` блоком. Тест test_real_sandbox_environment_isolation закрепляет инвариант.

- **date:** 2026-05-16 22:30 UTC
  **session:** FS7
  **decision:** Pre-existing mypy error в main_merge_token.py:40 — deferred за scope FS7.
  **rationale:** verified via git stash что ошибка существовала ДО FS7; unrelated к sandbox wiring. Включать в FS7 commit → scope creep.
  **impact:** standalone fix отдельным коммитом после initiative merge; добавлен в deferred_items FS7.

- **date:** 2026-05-16 23:30 UTC
  **session:** FS8
  **decision:** Env-priority path валидирует session_id через `_session_exists` ДО bind'а.
  **rationale:** Без валидации stale `BMAD_ORCHESTRATOR_SESSION_ID` (leak между тестами, разные deployments) приводил к `FOREIGN KEY constraint failed` при последующем INSERT в `budget_tracker`. Caught на первом прогоне full suite (test_mock_pilot_runs_to_completion).
  **impact:** Production-side robustness — orchestrator не падает при stale env (например, после rotate state.db); test isolation — env-mutation внутри одного process'а не ломает следующий test.

- **date:** 2026-05-16 23:30 UTC
  **session:** FS8
  **decision:** `resolve_or_create_session` returns `int`, не `str` как в spec text.
  **rationale:** Существующий `create_session` возвращает `int`, schema `agent_session.id INTEGER PRIMARY KEY`. Env layer хранит `str(int)`. Типизация чистая на каждом слое; spec text написан до того как стала ясна consistency invariant.
  **impact:** Все callers (`agent.run._resolve_session`, `bot.main._attach_bridge`, тесты) типизированы `int`. Env serialization автоматическая.

## Journal

[2026-05-16 20:30 UTC] bootstrap: manual tracker + integration/orchestrator_agent_security_fixes_3 FROM integration/orchestrator_agent_security_fixes_2 (cumulative base). 2 сессии (FS7 bwrap sandbox, FS8 wiring). Spec: spec/spec_orchestrator_agent_security_fixes_3.md v0.1. Anti-pattern-recursion approach: OS-level isolation > pattern matching.
[2026-05-16 21:10 UTC] FS7 promoted to Current. bwrap 0.9.0 verified; unprivileged_userns_clone=1; kernel 6.17. Starting implementation: runtime/sandbox.py + wiring + tests + docs.
[2026-05-16 22:30 UTC] FS7 completed (commit 3053031). 7 files: runtime/sandbox.py (NEW, ~245 lines), runtime/worker_spawn.py (sandbox wire + WorkerHandle.sandbox_kind), agent/tools/retro.py (sandbox wire), agent/safety/hooks.py (defence-in-depth docstring + info severity when sandbox active), tests/test_fs7_sandbox.py (NEW, 32 tests incl. real-bwrap PoC), spec/§22.7 (full Sandbox layer doc), CLAUDE.md (boundary #5). Tests: 573 PASS in 9.43s. Ruff + mypy --strict зелёные на FS7 modules. Decisions: --clearenv обязателен; Sandbox.kind через @property; pre-existing main_merge_token mypy deferred. FS8 promoted to Current. Runtime=loop_wrapper → no ScheduleWakeup. Auto merge=false → no main merge; ждём FS8.
[2026-05-16 23:30 UTC] FS8 completed (commit e3f4b3a). 5 files: state/db.py (resolve_or_create_session, +73 lines), agent/run.py (_resolve_session priority env→resolve→new + _session_exists FK guard + BudgetGuard.attach_state_db wire, +117 lines), bot/main.py (_attach_bridge mirrors priority, +54 lines net), tests/conftest.py (NEW — sandbox_available + tmp_state_db_session fixtures), tests/test_fs8_shared_session_and_budget.py (NEW, 11 tests). Tests: 584 PASS in 9.97s (541 baseline + 32 FS7 + 11 FS8). Ruff: All checks passed. mypy --strict: clean on FS8-touched modules. Decisions: int return type (consistency with create_session); wave=None matches any wave; FK guard via _session_exists; cross-process test loose <30s wall-clock budget. Initiative done. Runtime=loop_wrapper → no ScheduleWakeup. Auto merge=false → manual_merge_pending entry; Final Report populated.

## Final Report

Initiative: Orchestrator Agent Security Fixes Round 3 (Sandbox)
Spec: spec/spec_orchestrator_agent_security_fixes_3.md
Started: 2026-05-16 20:30 UTC
Completed: 2026-05-16 23:30 UTC
Sessions: 2 planned (FS7, FS8), 2 executed, 0 buffered
Safety gate trips: 0
Human pauses: 0
Integration branch: integration/orchestrator_agent_security_fixes_3
Backup branch: backup/orchestrator_agent_security_fixes_3-pre-2026-05-16

Commits on integration (last 2 = FS7 + FS8):
- 3053031 feat(safety): FS7 — bwrap sandbox primary safety + demote _scan_bash to defence-in-depth (7 files, +~750 lines incl. tests)
- e3f4b3a feat(orchestrator): FS8 — NH1 shared session model + NH2 atomic budget binding (5 files, +598/-12 lines)

Diff vs main: 112 files changed, 19088 insertions(+), 554 deletions(-) — cumulative with security_fixes_1 + security_fixes_2 + S1-S8 MVP.

Quality:
- Tests: 584 PASS in 9.97s (541 baseline + 32 FS7 + 11 FS8)
- ruff check src/ tests/: All checks passed!
- mypy --strict on FS7+FS8-touched modules: clean
- Pre-existing mypy error main_merge_token.py:40 — deferred (standalone one-liner follow-up, не блокирует initiative)

Recommendation: NEEDS HUMAN REVIEW & MANUAL MERGE.

Merge hint (manual — Auto merge=false):
```
git checkout main
git merge --no-ff integration/orchestrator_agent_security_fixes_3 \
  -m "merge orchestrator_agent MVP + 3 rounds security fixes (FS7 bwrap + FS8 NH1/NH2 wiring)"
```

Deferred follow-ups (not blocking merge):
- main_merge_token.py:40 mypy fix (one-liner)
- FS7-A..FS7-E reviewer fast-follows — wire при wave 1a pilot run
- nftables `github_only` whitelist (sandbox network policy refinement)
