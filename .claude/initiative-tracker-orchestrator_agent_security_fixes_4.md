# Initiative Tracker — Orchestrator Agent Security Fixes Round 4 (Sandbox Config Hardening)

## Metadata
- **Spec:** spec/spec_orchestrator_agent_security_fixes_4.md
- **Parent specs:** security_fixes_3.md, security_fixes_2.md, security_fixes.md, spec_orchestrator_agent.md
- **Integration branch:** integration/orchestrator_agent_security_fixes_4
- **Base branch:** integration/orchestrator_agent_security_fixes_3 (cumulative — S1-S8 + FS1-FS8)
- **Backup branch:** backup/orchestrator_agent_security_fixes_4-pre-2026-05-16
- **Created:** 2026-05-16
- **Bootstrap completed:** 2026-05-16 manual
- **Scope frozen:** 2026-05-16
- **Runtime:** loop_wrapper
- **Delay seconds:** 600
- **Auto merge:** false

## Scope Freeze

### In scope
- 1 сессия FS9: sandbox config hardening — закрытие H2/H3/H4/H5/H6/H1/H8/H9 из round 4 audit
- H2 flip sandbox_network default "github_only" → "none"
- H3 + H4 rlimits через prlimit (nproc=512, AS=8GB, fsize=10GB, nofile=4096)
- H5 BMAD_REQUIRE_SANDBOX=1 env hard-fail для production
- H6 BMAD_SANDBOX=none требует BMAD_SANDBOX_DISABLE_CONFIRMED=yes-i-accept-risk
- H1 /sys tmpfs hide (kernel info leak protection)
- H8 stale BMAD_ORCHESTRATOR_SESSION_ID cleanup при cross-DB fail
- H9 BMAD_REQUIRE_DB_BRIDGE=1 для bot (no silent stub mode)
- Spec §22.7 sandbox config defaults documentation
- ~18 новых tests
- prlimit verified installed

### Out of scope (deferred)
- Network whitelist через nftables для github_only — defer на wave-1a-pilot-wiring
- Tmpfs /tmp size cap через --bind-try — defer (fsize rlimit достаточно)
- firejail/nsjail fallback — defer
- W1 host FS read-leak via --ro-bind / / — architectural design, документируется в §22.7 как known
- Все H1-H14 предыдущих rounds, M-items — deferred per spec

## Sessions

### Pending

- **id:** FS9
  **title:** Sandbox config hardening — H2 network default + H3/H4 rlimits + H5/H6 fail-safe overrides + H1 /sys hide + H8/H9 stale-state cleanup (CHECKPOINT)
  **surface:** backend-python
  **spec_section:** 55-200
  **depends_on:** []
  **acceptance:**
    - H2: runtime/worker_spawn.py:186 `sandbox_network: NetworkPolicy = "none"` (было "github_only")
    - H3+H4: runtime/sandbox.py BwrapSandbox.wrap_command prepend prlimit --nproc=512 --as=8GB --fsize=10GB --nofile=4096 --; constants overridable через env BMAD_SANDBOX_MAX_*; prlimit verified в __init__
    - H5: detect_sandbox() raises RuntimeError если BMAD_REQUIRE_SANDBOX=1 + bwrap unavailable
    - H6: detect_sandbox() raises RuntimeError при BMAD_SANDBOX=none без BMAD_SANDBOX_DISABLE_CONFIRMED=yes-i-accept-risk
    - H1: BwrapSandbox._build_args добавить --tmpfs /sys
    - H8: agent/run.py::_resolve_session exception path → os.environ.pop(SESSION_ENV_VAR)
    - H9: bot/main.py::_attach_bridge exception path → raise RuntimeError если BMAD_REQUIRE_DB_BRIDGE=1
    - Spec §22.7 «Sandbox configuration defaults» subsection с описанием всех env vars + production launcher recommendations
    - PoC tests (real subprocess.run bwrap): fork-bomb capped, OOM capped, fsize capped, /sys empty, /etc write EPERM
    - tests/test_fs9_sandbox_config_hardening.py ~18 tests
    - Все 584 + ~18 = ~602 PASS
    - ruff + mypy --strict зелёные
  **safety_gates:**
    - L4 sandbox full config hardening (default safe, override 2-key confirmation)
    - L5 (NEW) resource limits через prlimit (DoS защита)
  **destructive_actions:** []
  **checkpoint:** true
  **estimated_retries_allowed:** 3

### Current
(none — next wake promotes FS9)

### Completed
(empty)

## Safety Gates Triggered
(none)

## Blockers / Pauses
(none)

## Decisions Log

- **date:** 2026-05-16 23:50 UTC
  **session:** bootstrap
  **decision:** Round 4 single-session fix-cycle для closing 4 must-fix HIGH из code-auditor round 4 + 3 bonus HIGH (H1/H8/H9).
  **rationale:** Code-auditor round 4 признал sandbox архитектуру правильной (live PoC verification), но нашёл config gaps (default network, no rlimits, silent fallbacks). Все фиксы ~30 LOC core + ~18 tests, тривиальные.
  **impact:** L5 (NEW) resource limits layer added. Production launcher должен set BMAD_REQUIRE_SANDBOX=1 + BMAD_REQUIRE_DB_BRIDGE=1. Round 5 ревью pass должен быть APPROVE если эти фиксы реально landed.

## Journal

[2026-05-16 23:50 UTC] bootstrap: manual tracker + integration/orchestrator_agent_security_fixes_4 FROM integration/orchestrator_agent_security_fixes_3 (cumulative). Spec: spec/spec_orchestrator_agent_security_fixes_4.md v0.1. Single session FS9. Anti-config-gap approach: defaults safe, overrides 2-key confirmation, fail-loud на missing deps.

## Final Report
(empty)
