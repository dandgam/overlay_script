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
- Multi-process orchestrator scenarios

## Sessions

### Pending
(empty)

### Current

- **id:** FS8
  **title:** NH1 shared session model + NH2 StateDB binding в _run_mock_pilot (CHECKPOINT)
  **surface:** backend-python
  **spec_section:** 185-260
  **depends_on:** [FS7]
  **acceptance:**
    - StateDB.resolve_or_create_session(target_project, wave) — BEGIN IMMEDIATE SELECT-or-INSERT; returns session_id
    - agent/run.py: resolution priority — env BMAD_ORCHESTRATOR_SESSION_ID > resolve_or_create_session > new
    - Export env для child processes
    - bot/main.py::_attach_bridge: same priority order
    - Test: 2 subprocess.Popen (orchestrator + bot) → same session_id; bot insert human_query → orchestrator claim'ит; round-trip <500ms
    - _run_mock_pilot до enforce_and_reserve_*: budget.attach_state_db(state_db, session_id)
    - Regression test: после run_orchestrator(mock=True) → budget.state_db is not None AND budget.session_id is not None
    - tests/conftest.py: sandbox_available fixture + tmp_state_db_session fixture
    - Все 541 + 32 FS7 + ~10 FS8 = ~583 tests PASS
    - ruff + mypy --strict зелёные
    - grep TODO|FIXME только в imports/from_bad/ или v1 follow-up
  **safety_gates:**
    - L2 atomic budget guard wired в pilot loop (final)
  **destructive_actions:** []
  **checkpoint:** true
  **estimated_retries_allowed:** 3
  **started:** 2026-05-16 22:30 UTC
  **workflow:** workflows/backend-python.md (adapted for bmad-orchestrator project)
  **retry_count:** 0
  **worker_branches:** []

### Completed

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
(none)

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

## Journal

[2026-05-16 20:30 UTC] bootstrap: manual tracker + integration/orchestrator_agent_security_fixes_3 FROM integration/orchestrator_agent_security_fixes_2 (cumulative base). 2 сессии (FS7 bwrap sandbox, FS8 wiring). Spec: spec/spec_orchestrator_agent_security_fixes_3.md v0.1. Anti-pattern-recursion approach: OS-level isolation > pattern matching.
[2026-05-16 21:10 UTC] FS7 promoted to Current. bwrap 0.9.0 verified; unprivileged_userns_clone=1; kernel 6.17. Starting implementation: runtime/sandbox.py + wiring + tests + docs.
[2026-05-16 22:30 UTC] FS7 completed (commit 3053031). 7 files: runtime/sandbox.py (NEW, ~245 lines), runtime/worker_spawn.py (sandbox wire + WorkerHandle.sandbox_kind), agent/tools/retro.py (sandbox wire), agent/safety/hooks.py (defence-in-depth docstring + info severity when sandbox active), tests/test_fs7_sandbox.py (NEW, 32 tests incl. real-bwrap PoC), spec/§22.7 (full Sandbox layer doc), CLAUDE.md (boundary #5). Tests: 573 PASS in 9.43s. Ruff + mypy --strict зелёные на FS7 modules. Decisions: --clearenv обязателен; Sandbox.kind через @property; pre-existing main_merge_token mypy deferred. FS8 promoted to Current. Runtime=loop_wrapper → no ScheduleWakeup. Auto merge=false → no main merge; ждём FS8.

## Final Report
(empty)
