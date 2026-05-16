# Spec — Orchestrator Agent Security Fixes (Round 4 — Sandbox Config Hardening)

**Дата:** 2026-05-16
**Версия:** 0.1
**Базовая ветка:** `integration/orchestrator_agent_security_fixes_3` (cumulative)
**Backup branch:** `backup/orchestrator_agent_security_fixes_4-pre-2026-05-16`
**Integration branch:** `integration/orchestrator_agent_security_fixes_4`
**Auto merge:** false

---

## 1. Контекст

После round 3 (FS7 bwrap + FS8 NH1/NH2) код-аудитор round 4 нашёл 4 must-fix HIGH в **конфигурации** sandbox (архитектура корректна и proven via live PoC, проблемы в default values + missing guards):

- **H2** — `sandbox_network="github_only"` default в `worker_spawn.py` silently ≡ `"full"` (нет `--unshare-net` пока нет nftables). Spec §7.1 говорит default `"none"` — расхождение.
- **H3** — Нет rlimits → worker может fork-bomb / OOM host / fill 24G tmpfs.
- **H4** — `--tmpfs /tmp` без size cap (default 50% RAM).
- **H5** — `NoSandbox` silent fallback если bwrap missing → workers без isolation в production.
- **H6** — `BMAD_SANDBOX=none` env disable без confirmation (любой process может выключить).

Плюс 2 info-disclosure из audit:
- **H1** — `/sys` mounted read-visible через `--ro-bind / /` → kernel info leak (LSMs, dmi, network).
- **H8** — stale `BMAD_ORCHESTRATOR_SESSION_ID` env var не очищается при cross-DB fail.
- **H9** — `bot/main.py::_attach_bridge` swallow'ит DB errors → silent stub mode → cross-process bridge silently broken.

Все ~30 LOC core + ~15 LOC tests. Single session FS9.

---

## 2. Принципы round 4

- **Конфигурация = code-as-policy**: default values должны быть safe-by-default, override должен требовать explicit confirmation
- **Fail-loud, не fail-silent**: missing sandbox / DB bridge должны блокировать прод-запуск с явным error message, не молча fallback
- **Resource limits как defence-in-depth для DoS**: bwrap не имеет native rlimit flag — используем `prlimit(1)` wrapper

---

## 3. Stack / Constraints

- `prlimit(1)` (system package `util-linux`, обычно установлен — verify в FS9)
- Без новых Python deps
- Сохранить все 584 PASS из round 3 + добавить ~15-20 тестов на новые guards
- ruff + mypy --strict зелёные

---

## 4. Session Plan

1 сессия (FS9), surface=`backend-python`, code-only.

### FS9 — Sandbox config hardening (CHECKPOINT)

- **surface:** backend-python
- **spec_section:** lines 55-200
- **depends_on:** []
- **destructive_actions:** []
- **checkpoint:** true
- **acceptance:**

  **9.1 H2 — flip sandbox_network default** (`runtime/worker_spawn.py:186`)
  - Изменить `sandbox_network: NetworkPolicy = "github_only"` → `"none"`
  - Спека §22.7 явно: workers получают `--unshare-net` по default. Если pilot run нужен git clone — caller передаёт `sandbox_network="full"` explicitly с warning log.
  - **Test:** unit test что `spawn_worker(mock=True)` без явного `sandbox_network` → wrapped command содержит `--unshare-net`.
  - **Grep validation:** `grep "sandbox_network: NetworkPolicy = \"none\"" src/bmad_orchestrator/runtime/worker_spawn.py` ≥ 1.

  **9.2 H3 + H4 — rlimits через prlimit** (`runtime/sandbox.py::BwrapSandbox.wrap_command`)
  - Verify `prlimit` доступен через `shutil.which("prlimit")` в `BwrapSandbox.__init__` (raise если missing — system package требуется).
  - В `wrap_command` prepend:
    ```python
    rlimit_wrapper = [
        self.prlimit_path,
        f"--nproc={MAX_NPROC}",          # default 512 (fork-bomb cap)
        f"--as={MAX_AS_BYTES}",          # default 8 * 1024**3 (8GB virtual memory)
        f"--fsize={MAX_FSIZE_BYTES}",    # default 10 * 1024**3 (10GB file size)
        f"--nofile={MAX_NOFILE}",        # default 4096 (fd limit)
        "--",
    ]
    return rlimit_wrapper + bwrap_args
    ```
  - Constants в `runtime/sandbox.py`:
    ```python
    MAX_NPROC = 512
    MAX_AS_BYTES = 8 * 1024**3
    MAX_FSIZE_BYTES = 10 * 1024**3
    MAX_NOFILE = 4096
    ```
  - Override через env vars `BMAD_SANDBOX_MAX_NPROC`, `BMAD_SANDBOX_MAX_AS_BYTES`, etc.
  - **PoC test (subprocess.run real bwrap):**
    - Worker внутри запускает `bash -c ":(){ :|:& };:"` (fork-bomb) → процесс получает EAGAIN после 512 forks (не сваливает host)
    - Worker запускает `dd if=/dev/zero of=/tmp/big bs=1M count=20000` → fail в районе 10GB (fsize cap)
    - Worker запускает Python `bytes(20 * 1024**3 * b"a")` → MemoryError (AS cap)
  - **Tmpfs size cap для `/tmp`:** bwrap не имеет native flag. Workaround — `--bind-try /var/tmp/bmad-tmp-<uuid> /tmp` с pre-created sized backing dir. Но требует cleanup. **Defer на v2**, документировать в spec §22.7 что tmpfs cap полагается на host RAM + fsize rlimit.

  **9.3 H5 — BMAD_REQUIRE_SANDBOX hard-fail** (`runtime/sandbox.py::detect_sandbox`)
  - В `detect_sandbox()`:
    ```python
    require = os.environ.get("BMAD_REQUIRE_SANDBOX", "").lower() in ("1", "true", "yes")
    if require and not isinstance(result, BwrapSandbox):
        raise RuntimeError(
            "BMAD_REQUIRE_SANDBOX=1 set but bwrap unavailable; refusing to spawn. "
            "Install bwrap (apt install bubblewrap) or unset BMAD_REQUIRE_SANDBOX."
        )
    ```
  - Документировать в spec §22.7: production deployments должны устанавливать `BMAD_REQUIRE_SANDBOX=1` в systemd unit / launcher script.
  - **Test:** monkeypatch `shutil.which("bwrap")` → None, env `BMAD_REQUIRE_SANDBOX=1` → `detect_sandbox()` raises RuntimeError с конкретным message.

  **9.4 H6 — BMAD_SANDBOX=none confirmation** (`runtime/sandbox.py::detect_sandbox`)
  - Перед возвратом NoSandbox при env override:
    ```python
    if override == "none":
        confirmation = os.environ.get("BMAD_SANDBOX_DISABLE_CONFIRMED", "")
        if confirmation != "yes-i-accept-risk":
            raise RuntimeError(
                "BMAD_SANDBOX=none requires BMAD_SANDBOX_DISABLE_CONFIRMED=yes-i-accept-risk. "
                "Disabling sandbox in production is dangerous."
            )
    ```
  - **Test:** env `BMAD_SANDBOX=none` без confirmation → RuntimeError; с confirmation → NoSandbox + warning log.

  **9.5 H1 — `/sys` tmpfs hide** (`runtime/sandbox.py::BwrapSandbox._build_args`)
  - Добавить `"--tmpfs", "/sys"` после `"--tmpfs", "/tmp"`
  - **Test:** spawn внутри sandbox `ls /sys` → empty (tmpfs); `cat /sys/kernel/security/lsm` → No such file.

  **9.6 H8 — stale session env cleanup** (`agent/run.py::_resolve_session`)
  - В exception path (когда `resolve_or_create_session` raises):
    ```python
    except Exception as exc:
        os.environ.pop("BMAD_ORCHESTRATOR_SESSION_ID", None)
        log.warning("session_resolve_failed_stale_env_cleared", error=str(exc))
        return None, None
    ```
  - Также при stale env (sess_id не существует в DB после `_session_exists` check): `os.environ.pop(SESSION_ENV_VAR, None)` перед re-resolve.
  - **Test:** stale env `BMAD_ORCHESTRATOR_SESSION_ID="nonexistent-id"` + DB unavailable → env var удалён после `_resolve_session()`.

  **9.7 H9 — BMAD_REQUIRE_DB_BRIDGE для bot** (`bot/main.py::_attach_bridge`)
  - В exception path:
    ```python
    except Exception as exc:
        require = os.environ.get("BMAD_REQUIRE_DB_BRIDGE", "").lower() in ("1", "true", "yes")
        if require:
            raise RuntimeError(
                f"BMAD_REQUIRE_DB_BRIDGE=1 but bridge failed: {exc}. Refusing to start bot."
            ) from exc
        log.warning("db_bridge_unavailable_stub_mode", error=str(exc))
    ```
  - **Test:** monkeypatch StateDB.create_or_open → raises; env `BMAD_REQUIRE_DB_BRIDGE=1` → `_attach_bridge` raises RuntimeError.

  **9.8 Spec §22.7 update** (`spec/spec_orchestrator_agent.md`)
  - Добавить subsection «Sandbox configuration defaults»:
    - `sandbox_network` default `"none"` (workers без network)
    - `BMAD_REQUIRE_SANDBOX=1` обязателен для prod
    - `BMAD_SANDBOX=none` требует `BMAD_SANDBOX_DISABLE_CONFIRMED=yes-i-accept-risk`
    - rlimits: nproc=512, AS=8GB, fsize=10GB, nofile=4096 (overridable через env)
    - `/sys` tmpfs (kernel info hidden)
    - Pre-pilot launcher (systemd unit) должен set оба `BMAD_REQUIRE_SANDBOX=1` + `BMAD_REQUIRE_DB_BRIDGE=1`
  - Explicit note: «sandbox = WRITE isolation, не read isolation. Host FS visible read-only через `--ro-bind / /`. Strip host of mode-0644 secrets before pilot.»

  **9.9 Tests** (`tests/test_fs9_sandbox_config_hardening.py`)
  - ~15-20 tests:
    - H2: default `--unshare-net`
    - H3+H4: fork-bomb capped, OOM capped, fsize capped via real subprocess
    - H5: missing bwrap + REQUIRE_SANDBOX → RuntimeError
    - H6: BMAD_SANDBOX=none без confirmation → RuntimeError; с confirmation → NoSandbox
    - H1: `/sys` empty in sandbox
    - H8: stale env var cleared
    - H9: bot REQUIRE_DB_BRIDGE → RuntimeError on DB fail
  - Все 584 + ~18 = ~602 PASS
  - ruff + mypy --strict зелёные

  **9.10 Final verification**
  - `pytest -x` PASS (~602)
  - `ruff check src/ tests/` clean
  - `mypy --strict src/bmad_orchestrator/runtime/sandbox.py src/bmad_orchestrator/runtime/worker_spawn.py src/bmad_orchestrator/agent/run.py src/bmad_orchestrator/bot/main.py` clean
  - Real-bwrap PoC (запустить вручную в FS9 verification): worker fork-bomb → capped; worker /etc write → EPERM; worker /sys list → empty
  - Spec §22.7 updated

- **safety_gates:**
  - L4 sandbox (full config hardening — default safe, override requires confirmation)
  - L5 (NEW) resource limits (prlimit-based, DoS защита)
- **destructive_actions:** []
- **checkpoint:** true
- **estimated_retries_allowed:** 3

---

## 5. Out of scope (deferred)

- Network whitelist через nftables (для `github_only` mode) — defer на wave-1a-pilot-wiring
- Tmpfs size cap для `/tmp` через `--bind-try` backing dir — defer (fsize rlimit достаточно для MVP)
- `firejail` / `nsjail` fallback (spec упоминал, impl только bwrap → NoSandbox) — defer
- Все H1-H14, M-items из round 1/2 — остаются deferred
- W1 (host FS read-leak через `--ro-bind / /`) — это **architectural design** (workers нужны readable codebases), документируется в spec §22.7 как known
- Multi-process orchestrator — single-process per spec

---

## 6. Post-completion

1. Final commit + Final Report
2. Round 5 independent code-reviewer + code-auditor (если оба APPROVE → merge)
3. Manual merge:
   ```bash
   git checkout main
   git merge --no-ff integration/orchestrator_agent_security_fixes_4 \
     -m "merge orchestrator_agent MVP + 4 rounds security fixes"
   ```

---

**End of spec v0.1**
