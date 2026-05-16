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

- **id:** FS7
  **title:** Worker subprocess sandbox via bwrap + demote _scan_bash до defence-in-depth (CHECKPOINT)
  **surface:** backend-python
  **spec_section:** 60-180
  **depends_on:** []
  **acceptance:**
    - runtime/sandbox.py — Sandbox Protocol + BwrapSandbox + NoSandbox + detect_sandbox() factory
    - BwrapSandbox: --ro-bind / /, --bind worktree worktree, --proc, --dev, --tmpfs /tmp, --unshare-pid/uts/ipc/net (default), --die-with-parent, --new-session, --setenv allowlist
    - Wire в runtime/worker_spawn.py spawn_worker (use_sandbox=True по default)
    - Wire в agent/tools/retro.py spawn_retro_worktree
    - Audit event: sandbox_used + sandbox_kind
    - agent/safety/hooks.py: docstring обновить «defence-in-depth, не primary»; severity scanner deny = info если sandbox активен
    - Spec §22.7 Sandbox layer documentation
    - tests/test_fs7_sandbox.py ~25 tests: real bwrap PoC restrictions (worker не пишет в /etc, не читает /home/server/crm/.env, нет network); abstraction unit tests; bypass attempts блокируются на FS уровне
    - Все 541 + ~25 tests PASS
    - ruff + mypy --strict зелёные
  **safety_gates:**
    - L4 (NEW): OS-level sandbox primary
    - L1: scanner defence-in-depth
  **destructive_actions:** []
  **checkpoint:** true
  **estimated_retries_allowed:** 3

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
    - Все 541 + 25 FS7 + ~10 FS8 = ~575 tests PASS
    - ruff + mypy --strict зелёные
    - grep TODO|FIXME только в imports/from_bad/ или v1 follow-up
  **safety_gates:**
    - L2 atomic budget guard wired в pilot loop (final)
  **destructive_actions:** []
  **checkpoint:** true
  **estimated_retries_allowed:** 3

### Current
(none — next wake promotes FS7)

### Completed
(empty)

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

## Journal

[2026-05-16 20:30 UTC] bootstrap: manual tracker + integration/orchestrator_agent_security_fixes_3 FROM integration/orchestrator_agent_security_fixes_2 (cumulative base). 2 сессии (FS7 bwrap sandbox, FS8 wiring). Spec: spec/spec_orchestrator_agent_security_fixes_3.md v0.1. Anti-pattern-recursion approach: OS-level isolation > pattern matching.

## Final Report
(empty)
