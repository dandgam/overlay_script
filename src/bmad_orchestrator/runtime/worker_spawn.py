"""Spawn `claude -p /bmad-auto-dev` worker subprocess in a worktree.

Real-mode (binary present):
- `asyncio.create_subprocess_exec("claude", "-p", "/bmad-auto-dev <args>", cwd=worktree, ...)`.
- stdout/stderr streamed → JSONL events file
  `<target>/_bmad-output/runs/<wave>/<story>.events.jsonl`.
- Returns (pid, jsonl_path).

Mock-mode (binary missing OR mock=True):
- Не запускает subprocess. Эмитит синтетические `worker_spawned` + `worker_completed`
  события в JSONL — достаточно чтобы DAG / event_loop / liveness pipeline'ы
  отработали end-to-end в pytest без зависимости от Claude CLI.

Дизайн:
- Defaults: model=`claude-sonnet-4-6` (spec §16.3 dev role).
- stdout-watcher парсит каждую строку:
  - Если это JSON `{event_type: ...}` — append as-is + tag with `worktree`/`ts`.
  - Иначе — wrap как `{event_type: stdout_line, text: <line>}`.
- exit-code → final `worker_completed` event с `status: success|failure`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bmad_orchestrator.agent.memory.levels import MemoryPersistor
from bmad_orchestrator.agent.safety.session_start import (
    _resolve_skill_slug,
    build_session_start_block,
    inject_into_worker_env,
)
from bmad_orchestrator.agent.tools._common import (
    append_jsonl,
    now_iso,
    worker_jsonl_path,
)
from bmad_orchestrator.config import DEFAULT_BUDGET_CAP_USD, DEFAULT_MODEL
from bmad_orchestrator.runtime.embedded_skills import (
    ApplyResult,
    apply_embedded_skills,
)
from bmad_orchestrator.runtime.mcp_readiness import (
    DEFAULT_INTERVAL_MS as MCP_READINESS_DEFAULT_INTERVAL_MS,
)
from bmad_orchestrator.runtime.mcp_readiness import (
    DEFAULT_TIMEOUT_S as MCP_READINESS_DEFAULT_TIMEOUT_S,
)
from bmad_orchestrator.runtime.mcp_readiness import (
    ReadinessResult,
    poll_mcp_ready,
)
from bmad_orchestrator.runtime.sandbox import (
    DEFAULT_CGROUP_LIMITS,
    NetworkPolicy,
    NoSandbox,
    Sandbox,
    detect_sandbox,
)
from bmad_orchestrator.runtime.worker_cancellation import (
    CancellationToken,
    build_worker_id,
    register_worker,
    unregister_worker,
)

log = logging.getLogger(__name__)

# Phase 4 hardening #2 — module-level MemoryPersistor instance shared across
# all spawn calls in the same process (stateless — all state lives on disk).
_MEMORY_PERSISTOR = MemoryPersistor()

CLAUDE_BIN_DEFAULT = "claude"
# Patch Y 2026-05-18: bypass `/bmad-auto-dev` slash-command. LLM workers
# repeatedly halt on perceived layout mismatches even with minimal SKILL.md.
# Replacement prompt is a direct execution order — Claude treats it as a task
# (run this bash, report exit code), not a skill to reason about.
DEFAULT_SKILL_INVOCATION = (
    "Execute exactly this bash command and nothing else: "
    "bash .claude/skills/bmad-auto-dev/scripts/bmad-auto-dev-runner.sh --max 1. "
    "Do not read other files. Do not analyse the project layout — the runner "
    "auto-detects everything. Do not ask me any questions. When the command "
    "exits, report only its exit code and the last 3 lines of stderr."
)

# Patch H (canonical port 2026-05-18): default 30 min, was 24h. Long-running
# legitimate stories should set BMAD_WORKER_TIMEOUT_SEC explicitly; the default
# now matches Odyssey's bmad-auto-dev-runner.sh upstream value (1800s) so
# stuck workers are killed within one orchestrator round instead of stalling
# the wave overnight. Reference: ~/.claude/skills/bmad-auto-dev/scripts/bmad-auto-dev-runner.sh
# lines 91-127 (`# Patch H 2026-...`).
def _worker_timeout_sec() -> int:
    raw = os.environ.get("BMAD_WORKER_TIMEOUT_SEC", "")
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return 1800

# FS1 B8: workers do NOT call LLMs (per spec §16.3 dev role isolation), so the
# only env vars they need are the bare-minimum runtime ones. Everything else —
# *_TOKEN, *_SECRET, *_API_KEY, ANTHROPIC_*, TELEGRAM_*, OPENAI_*, YANDEX_*,
# GOOGLE_*, GH_*, GITHUB_* — gets stripped automatically by the allowlist.
ALLOWED_WORKER_ENV: frozenset[str] = frozenset({
    "PATH", "HOME", "USER", "LANG", "LC_ALL", "TZ", "PWD", "SHELL", "TERM",
    # XDG_RUNTIME_DIR + DBUS_SESSION_BUS_ADDRESS — required so the OUTER
    # ``systemd-run --user --scope`` cgroup wrapper can reach the user systemd
    # manager. Without them systemd-run dies with "Failed to connect to bus:
    # No medium found" and the whole worker spawn fails. The bwrap sandbox
    # strips both for the INNER worker process (``--unsetenv`` in
    # sandbox.py::BwrapSandbox.wrap_command) so the sandboxed ``claude -p``
    # still cannot reach the session bus — isolation is preserved.
    "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
})


def _build_worker_env(extra: dict[str, str] | None) -> dict[str, str]:
    """Build subprocess env from allow-list only. Caller-passed `extra` is trusted
    (intended for ORCHESTRATOR_WORKER_* context vars)."""
    env: dict[str, str] = {
        k: os.environ[k] for k in ALLOWED_WORKER_ENV if k in os.environ
    }
    if extra:
        env.update(extra)
    return env

# Module-level pool — predotvrachaet GC.collect() unblocking subprocess watcher
# tasks before they finalize the JSONL stream. Callers obtain handles back from
# `spawn_worker`; this set just prevents asyncio orphaning.
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


class MCPNotReadyError(RuntimeError):
    """Pre-spawn MCP readiness probe found unauthenticated required tools.

    Initiative pilot_findings_closure S5 (#5 R2). The orchestrator should catch
    this and convert it into a halt-before-spawn for the affected story.
    """

    def __init__(
        self,
        *,
        story_id: str,
        missing: list[str],
        elapsed_ms: int,
        last_error: str | None,
    ) -> None:
        self.story_id = story_id
        self.missing = list(missing)
        self.elapsed_ms = elapsed_ms
        self.last_error = last_error
        joined = ", ".join(self.missing)
        super().__init__(
            f"mcp_not_ready story={story_id} missing=[{joined}] "
            f"elapsed_ms={elapsed_ms} last_error={last_error!r}"
        )


# Initiative pilot_findings_closure S6 (#7 P2) — worktree path of the halt
# marker written by the BMad auto-dev runner. Mirrors Patch BB orphan-story
# pre-flight in spirit: a stale halt-reason from a prior run causes silent
# Stage 0 failures in every spawned worker. The pre-spawn gate refuses to
# spawn until either the file is cleared manually or ``auto_clear_halt=True``
# is passed (CLI ``--resume``).
HALT_REASON_RELPATH = Path("_bmad") / "auto-dev-state" / "halt-reason.txt"


class WorkerHaltPrespawnError(RuntimeError):
    """Pre-spawn gate detected ``halt-reason.txt`` and ``auto_clear_halt`` was False.

    Initiative pilot_findings_closure S6 (#7 P2). Carries the worktree path,
    the story id, and the first line of the halt reason so the orchestrator
    can surface an actionable error to the operator.
    """

    def __init__(
        self,
        *,
        story_id: str,
        worktree: str,
        halt_path: str,
        reason: str,
    ) -> None:
        self.story_id = story_id
        self.worktree = worktree
        self.halt_path = halt_path
        self.reason = reason
        super().__init__(
            f"worker_halt_prespawn story={story_id} halt_path={halt_path} "
            f"reason={reason!r} (pass auto_clear_halt=True / CLI --resume to clear)"
        )


def _read_halt_reason(halt_path: Path) -> str:
    """Read first non-empty line from halt-reason.txt. Tolerant to noise."""
    try:
        raw = halt_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:512]
    return ""


async def _git_porcelain(worktree: Path) -> list[str] | None:
    """Return ``git status --porcelain`` lines for ``worktree``.

    ``None`` when the path is not a git worktree (``git`` exits non-zero) —
    the dirty-worktree gate then treats it as not-applicable (mock workers
    are routinely spawned into plain tmp dirs in tests).
    """
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(worktree), "status", "--porcelain",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return None
    return [ln for ln in stdout.decode(errors="replace").splitlines() if ln.strip()]


def _porcelain_path(line: str) -> str | None:
    """Extract the path from one ``git status --porcelain`` line.

    Porcelain format: 2-char status, a space, then the path (cols 3+). For
    renames/copies the field is ``old -> new`` — we return the *new* path.
    Returns ``None`` for lines too short to carry a path.
    """
    if len(line) < 4:
        return None
    path = line[3:].strip()
    if " -> " in path:
        path = path.split(" -> ", 1)[1].strip()
    # Porcelain may quote paths containing special chars; strip the quotes.
    if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
        path = path[1:-1]
    return path or None


def filter_dirty_outside_claude(porcelain_lines: list[str]) -> list[str]:
    """Drop ``.claude/`` entries from a porcelain listing (NEW-5 recheck, v4 §3).

    Embedded skills are an intentional orchestrator inject (``apply_embedded_skills``
    writes ~73 files into ``<worktree>/.claude/skills/``), not residue from a
    prior aborted run. Treating them as a dirty worktree makes the pre-spawn
    gate either halt the story or ``git clean`` the skills away. Real dirt
    outside ``.claude/`` is still returned so the gate keeps catching it.
    """
    real: list[str] = []
    for line in porcelain_lines:
        path = _porcelain_path(line)
        if path is None:
            continue
        if path == ".claude" or path.startswith(".claude/"):
            continue
        real.append(line)
    return real


async def _clean_dirty_worktree(worktree: Path) -> None:
    """Discard uncommitted residue: ``git reset --hard`` + ``git clean -fd``.

    Destructive — acceptable here ONLY because the caller has gated this to a
    managed ``.worktrees/`` path under orchestrator control (never a user
    repo): it discards just the orchestrator-residue of a prior aborted run
    (NEW-5, spec_pilot_findings_closure_v3 §#5).

    ``.claude/`` is excluded from ``git clean`` (``-e .claude``) so embedded
    skills injected by the orchestrator survive the cleanup (NEW-5 recheck,
    v4 §3).
    """
    for args in (["reset", "--hard"], ["clean", "-fd", "-e", ".claude"]):
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(worktree), *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()


@dataclass(slots=True)
class WorkerHandle:
    worktree: str
    story_id: str
    branch: str
    pid: int
    jsonl_path: Path
    process: asyncio.subprocess.Process | None
    mock: bool
    fallback_reason: str | None = None
    real_requested: bool = False
    sandbox_kind: str = "none"
    base_sha: str | None = None
    isolated_home_path: str | None = None
    cgroup_limits_applied: dict[str, str] | None = None
    # Initiative pilot_findings_closure S4 (#4 R1): per-worker cancellation
    # token registered in :mod:`runtime.worker_cancellation`. The supervisor
    # can flip this token via the ``cancel_worker`` action to kill a stuck
    # worker without waiting for the orchestrator-wide timeout.
    worker_id: str | None = None
    cancellation_token: CancellationToken | None = None


# Initiative #1 Task 1.4 — per-worker HOME snapshot.
#
# Parallel workers (max_parallel > 1) cannot safely share the host's
# ``~/.claude*`` files: the Claude CLI writes session state, lock files,
# and credential rotation to these paths and concurrent writes corrupt the
# JSON / SQLite stores. The fix is to give every worker its own copy:
#
#   /tmp/bmad-worker-<story>-<n>/.claude/
#   /tmp/bmad-worker-<story>-<n>/.claude.json
#   /tmp/bmad-worker-<story>-<n>/.local/share/claude/
#
# We then pass the temp root to ``Sandbox.wrap_command(worker_home_overlay=...)``
# which bwrap-binds the snapshot OVER the host paths inside the namespace,
# so the worker still sees ``$HOME/.claude`` resolve to the right disk
# location — just one that's exclusively its own.
#
# Cleanup is best-effort in ``_wait_and_finalize`` (after exit). We avoid
# atexit because the parent process may outlive many workers and accumulate
# stale dirs otherwise.

# Files to symlink/skip rather than copy (large session state with no
# concurrency-sensitive writes; keeping host originals is faster + safer).
_SYMLINK_CLAUDE_SUBPATHS: frozenset[tuple[str, ...]] = frozenset({
    (".claude", "projects"),
    (".local", "share", "claude"),
})


def _create_isolated_home(
    *, worker_label: str, host_home: Path | None = None
) -> Path:
    """Snapshot ``~/.claude*`` into a private tmpdir for one worker.

    Returns the overlay root (parent dir holding ``.claude/`` etc). The
    overlay is suitable for passing as ``worker_home_overlay`` to
    ``Sandbox.wrap_command``. Caller is responsible for cleanup via
    ``_cleanup_isolated_home``.

    Heuristic copies:
      * ``~/.claude.json``         → file copy (small, contention-prone).
      * ``~/.claude/`` (tree)      → file copy minus ``projects/`` (large
                                     conversation history; not needed by
                                     a fresh worker spawn).
      * ``~/.local/share/claude/`` → directory created, NOT copied
                                     (versions/installer cache; worker
                                     can re-populate).

    Missing source paths are silently skipped — the overlay still works
    because the bwrap binds we add conditionally check for existence.
    """
    home = host_home if host_home is not None else Path(os.path.expanduser("~"))
    overlay = Path(tempfile.mkdtemp(prefix=f"bmad-worker-{worker_label}-"))

    # ~/.claude.json
    src_json = home / ".claude.json"
    if src_json.exists() and src_json.is_file():
        try:
            shutil.copy2(src_json, overlay / ".claude.json")
        except OSError as exc:
            log.warning("isolated_home copy .claude.json failed: %s", exc)

    # ~/.claude/ (tree, minus heavy subdirs)
    src_claude = home / ".claude"
    if src_claude.exists() and src_claude.is_dir():
        dest_claude = overlay / ".claude"
        dest_claude.mkdir(parents=True, exist_ok=True)
        # Walk top-level only; recurse selectively.
        for entry in src_claude.iterdir():
            rel = (".claude", entry.name)
            dest = dest_claude / entry.name
            try:
                if rel in _SYMLINK_CLAUDE_SUBPATHS:
                    # Create empty placeholder dir so claude doesn't EROFS;
                    # don't copy GB of project history.
                    dest.mkdir(exist_ok=True)
                    continue
                if entry.is_dir():
                    shutil.copytree(entry, dest, symlinks=True, dirs_exist_ok=True)
                else:
                    shutil.copy2(entry, dest)
            except OSError as exc:
                log.warning("isolated_home copy %s failed: %s", entry, exc)

    # NB: ``~/.local/share/claude`` is deliberately NOT snapshotted into the
    # overlay — it holds the immutable claude binary install
    # (``versions/<v>/``). The sandbox binds it from the real host (see
    # sandbox.py::host_only_claude_subpaths). An empty overlay copy would
    # shadow the binary → ``bwrap: execvp .../claude: No such file or directory``.
    return overlay


def _cleanup_isolated_home(overlay: Path | None) -> None:
    """Best-effort rm-rf of a worker's HOME overlay. Never raises.

    Safety: only rm-rf paths whose basename starts with the ``bmad-worker-``
    prefix used by :func:`_create_isolated_home`. A bug that passed any
    other path here (project worktree, user $HOME, etc.) must be a no-op so
    we never destroy data that does not belong to this helper.
    """
    if overlay is None:
        return
    if not overlay.name.startswith("bmad-worker-"):
        return
    if not str(overlay).startswith(tempfile.gettempdir()):
        return
    try:
        if overlay.exists():
            shutil.rmtree(overlay, ignore_errors=True)
    except OSError as exc:
        log.warning("isolated_home cleanup failed for %s: %s", overlay, exc)


def cleanup_stale_worker_homes(max_age_seconds: float = 86400.0) -> int:
    """Remove ``/tmp/bmad-worker-*`` snapshots older than ``max_age_seconds``.

    Review finding H-2: ``_create_isolated_home`` copies live OAuth tokens
    (``~/.claude.json``, ``~/.claude/``) into ``/tmp/bmad-worker-*``. A
    SIGKILL/OOM on the parent skips ``_cleanup_isolated_home`` and the
    snapshot lingers on disk with live credentials until reboot. Call this
    at pilot startup so each new run garbage-collects orphans from prior
    crashes that share the current UID.

    Safety:
    * Only paths whose basename starts with ``bmad-worker-`` (the
      ``_create_isolated_home`` prefix) are considered.
    * Only paths inside ``tempfile.gettempdir()`` are touched (so a
      symlinked ``/tmp`` does not redirect deletion elsewhere).
    * Only entries owned by the current UID are removed (so a multi-user
      host cannot have one user clean another user's snapshots).
    * Mtime check uses ``max_age_seconds`` (default 24h) so an in-flight
      sibling pilot whose worker dir mtime hasn't bumped in N hours is
      never touched. The default sits well above
      ``MultiProjectPlan.per_project_timeout_sec`` (4h) so even a
      maximally-long real-mode wave whose worker dir mtime only reflects
      creation time is safe from cleanup by a sibling pilot startup.

    Returns the count of removed entries.
    """
    tmp_root = Path(tempfile.gettempdir())
    if not tmp_root.is_dir():
        return 0
    now = time.time()
    cutoff = now - max_age_seconds
    uid = os.getuid()
    removed = 0
    for entry in tmp_root.glob("bmad-worker-*"):
        if not entry.name.startswith("bmad-worker-"):
            continue
        try:
            stat = entry.lstat()
        except OSError:
            continue
        if stat.st_uid != uid:
            continue
        if stat.st_mtime > cutoff:
            continue
        try:
            if entry.is_symlink() or entry.is_file():
                entry.unlink(missing_ok=True)
            else:
                shutil.rmtree(entry, ignore_errors=True)
            removed += 1
        except OSError as exc:
            log.warning(
                "stale_worker_home_cleanup_failed path=%s error=%s",
                str(entry),
                str(exc),
            )
    if removed:
        log.info(
            "stale_worker_homes_cleaned count=%d cutoff_age_seconds=%d",
            removed,
            max_age_seconds,
        )
    return removed


def _resolve_claude_bin() -> str | None:
    """Return path to claude binary or None if not available."""
    return shutil.which(CLAUDE_BIN_DEFAULT)


def _emit(jsonl_path: Path, event: dict[str, Any]) -> None:
    payload = {"ts": now_iso(), **event}
    append_jsonl(jsonl_path, payload)


def trigger_precompact_dump(
    worktree: str,
    story_id: str,
    retry_count: int = 0,
    last_event_seq: int = 0,
    active_skill: str = "bmad-auto-dev",
    scope_drift_warnings: int = 0,
    worker_started_at: str | None = None,
    jsonl_path: Path | None = None,
) -> Path:
    """Persist worker state to the precompact snapshot before a model swap.

    Phase 4 hardening #2 — called from ``set_model`` (orchestrator tool) or
    any other compact-trigger boundary to ensure mid-story context is not lost.

    Emits a ``WORKER_STATE_PERSISTED`` entry to ``jsonl_path`` (if provided)
    for observability. Returns the path of the written snapshot.

    Args:
        worktree: Absolute path to the worker's worktree directory.
        story_id: The story currently being processed.
        retry_count: Number of review-fix iterations completed so far.
        last_event_seq: The last JSONL event sequence number observed.
        active_skill: The skill currently active in the worker.
        scope_drift_warnings: Count of scope-drift warnings emitted so far.
        worker_started_at: ISO-8601 timestamp when the worker was spawned.
            Used as the freshness marker for ``load_state``.
        jsonl_path: Optional JSONL events file to write the observability event.
            When ``None``, no event is written (silent persist).

    Returns:
        The ``Path`` of the written ``_precompact.json`` snapshot.
    """
    started_at = worker_started_at or now_iso()
    state = {
        "current_story_id": story_id,
        "retry_count": retry_count,
        "last_event_seq": last_event_seq,
        "active_skill": active_skill,
        "scope_drift_warnings": scope_drift_warnings,
        "worker_started_at": started_at,
    }
    snap_path = _MEMORY_PERSISTOR.dump_state(Path(worktree), state)

    if jsonl_path is not None:
        _emit(
            jsonl_path,
            {
                "event_type": "worker_state_persisted",
                "worktree": worktree,
                "story_id": story_id,
                "snapshot_path": str(snap_path),
                "retry_count": retry_count,
                "scope_drift_warnings": scope_drift_warnings,
                "active_skill": active_skill,
            },
        )
        log.debug(
            "worker_state_persisted story=%s snap=%s", story_id, snap_path
        )

    return snap_path


async def _stream_subprocess_stdout(
    stream: asyncio.StreamReader, jsonl_path: Path, worktree: str
) -> None:
    """Pipe stdout lines → JSONL events. Tolerant to non-JSON output."""
    while True:
        raw = await stream.readline()
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            continue
        parsed: dict[str, Any] | None = None
        if line.startswith("{") and line.endswith("}"):
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    parsed = obj
            except json.JSONDecodeError:
                parsed = None
        if parsed is None:
            _emit(jsonl_path, {"event_type": "stdout_line", "worktree": worktree, "text": line})
        else:
            parsed.setdefault("event_type", "claude_event")
            parsed.setdefault("worktree", worktree)
            _emit(jsonl_path, parsed)


def _read_review_iteration(worktree: str, story_id: str) -> int:
    """Read bmad-auto-dev retry count from the worker state file.

    The shell runner persists per-story retry counts in
    ``<worktree>/_bmad/auto-dev-state/current-batch.json`` under
    ``retries[<story_id>]``. ``review_iteration`` equals ``retry_count + 1``:
    the initial Stage 6 review is iteration 1, the first auto-fix re-review is
    iteration 2, and so on. Missing/malformed state → safe default of 1.
    """
    state_path = Path(worktree) / "_bmad" / "auto-dev-state" / "current-batch.json"
    if not state_path.is_file():
        return 1
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 1
    if not isinstance(data, dict):
        return 1
    retries = data.get("retries")
    if not isinstance(retries, dict):
        return 1
    raw = retries.get(story_id, 0)
    try:
        count = int(raw)
    except (TypeError, ValueError):
        return 1
    return max(1, count + 1)


async def _wait_and_finalize(
    process: asyncio.subprocess.Process,
    jsonl_path: Path,
    worktree: str,
    story_id: str,
    *,
    isolated_home_overlay: Path | None = None,
    worker_id: str | None = None,
) -> None:
    """Wait for subprocess exit; append final worker_completed event.

    FS3 H15: hard upper bound on the wait (configurable via BMAD_WORKER_TIMEOUT_SEC,
    default 24h) so a deadlocked worker can't pin the background task forever.
    On timeout: SIGKILL + emit subprocess_timeout audit + emit
    worker_completed with status=failure.

    Initiative #1 Task 1.4: if ``isolated_home_overlay`` was used, rm-rf the
    snapshot tmpdir after process exit (best-effort; never raises).

    P5 Evaluator-Optimizer: ``review_iteration`` is read from the shell
    runner's state file (current-batch.json → retries[story_id] + 1) and
    surfaced into the worker_completed event so the orchestrator's
    ``_gate_iteration_cap`` can trip on runaway review→fix loops.
    """
    timeout = _worker_timeout_sec()
    try:
        rc = await asyncio.wait_for(process.wait(), timeout=timeout)
    except TimeoutError:
        process.kill()
        rc = await process.wait()
        _emit(
            jsonl_path,
            {
                "event_type": "subprocess_timeout",
                "worktree": worktree,
                "story_id": story_id,
                "command": "claude -p (worker)",
                "pid": process.pid,
                "timeout_sec": timeout,
            },
        )
    _emit(
        jsonl_path,
        {
            "event_type": "worker_completed",
            "worktree": worktree,
            "story_id": story_id,
            "exit_code": rc,
            "status": "success" if rc == 0 else "failure",
            "review_iteration": _read_review_iteration(worktree, story_id),
        },
    )
    _cleanup_isolated_home(isolated_home_overlay)
    if worker_id is not None:
        unregister_worker(worker_id)


async def spawn_worker(
    *,
    worktree: str,
    story_id: str,
    branch: str,
    model: str = DEFAULT_MODEL,
    budget_cap_usd: float = DEFAULT_BUDGET_CAP_USD,
    skill_invocation: str = DEFAULT_SKILL_INVOCATION,
    extra_args: list[str] | None = None,
    mock: bool | None = None,
    env: dict[str, str] | None = None,
    use_sandbox: bool = True,
    sandbox_network: NetworkPolicy = "none",
    embedded_skills_root: Path | str | None = None,
    allowed_worktree_root: Path | str | None = None,
    base_sha: str | None = None,
    isolated_home: bool = False,
    cgroup_limits: dict[str, str] | None = None,
    required_mcp_tools: list[str] | None = None,
    mcp_readiness_timeout_s: int = MCP_READINESS_DEFAULT_TIMEOUT_S,
    mcp_readiness_interval_ms: int = MCP_READINESS_DEFAULT_INTERVAL_MS,
    auto_clear_halt: bool = False,
    auto_clean_dirty_worktree: bool = True,
) -> WorkerHandle:
    """Spawn a worker. `mock=None` → auto-detect (mock-mode if claude binary absent).

    Returns immediately; subprocess лог-стрим продолжается в фоне как создаваемые
    asyncio.Tasks. Caller получает PID + JSONL path для observation.

    If ``embedded_skills_root`` is provided (path to orchestrator's ``skills/``
    directory containing ``upstream/`` + optional ``customize/``), the helper
    :func:`apply_embedded_skills` copies all 14 canonical phase 4+5 skills into
    ``<worktree>/.claude/skills/`` before the subprocess launches. The worker
    then resolves skills locally without touching the target project's main
    ``.claude/skills/``. ``allowed_worktree_root`` (required when the previous
    parameter is set) gates the copy to worktrees strictly inside the configured
    root — typically ``<target>/.worktrees``.

    Initiative #1 Task 1.3/1.4 (real-mode only):
    * ``isolated_home=True`` — snapshot the host ``~/.claude*`` files into a
      per-worker tmp dir and bwrap-bind the snapshot into the sandbox. Required
      when running ``max_parallel > 1`` to avoid corrupting the shared Claude
      CLI state. The overlay is cleaned up after the worker exits.
    * ``cgroup_limits`` — per-worker systemd scope (``MemoryMax``, ``CPUQuota``,
      ``TasksMax``…). Pass :data:`runtime.sandbox.DEFAULT_CGROUP_LIMITS` for the
      project default (8 GiB / 200% CPU / 16384 tasks). Ignored in mock mode
      and when ``use_sandbox=False``.
    """
    wt_path = Path(worktree)
    if not wt_path.exists():
        raise FileNotFoundError(f"worktree path missing: {worktree}")

    # Initiative pilot_findings_closure S6 (#7 P2): halt-reason pre-flight.
    # Spawning a worker into a worktree that still has the prior run's
    # halt-reason.txt produces a silent Stage 0 failure (the runner refuses
    # to start). Detect the marker before any heavier work (MCP probe,
    # sandbox setup) so the orchestrator can surface a real error or, with
    # ``auto_clear_halt=True`` (CLI ``--resume``), wipe the marker and
    # proceed exactly as a fresh spawn would.
    halt_path = wt_path / HALT_REASON_RELPATH
    if halt_path.is_file():
        reason = _read_halt_reason(halt_path)
        if auto_clear_halt:
            try:
                halt_path.unlink()
            except OSError as exc:
                log.warning(
                    "halt_reason_clear_failed path=%s error=%s",
                    str(halt_path),
                    str(exc),
                )
            else:
                log.info(
                    "halt_reason_cleared path=%s story_id=%s reason=%r",
                    str(halt_path),
                    story_id,
                    reason,
                )
        else:
            prespawn_jsonl_path = worker_jsonl_path(worktree)
            prespawn_jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            _emit(
                prespawn_jsonl_path,
                {
                    "event_type": "worker_halt_prespawn",
                    "worktree": worktree,
                    "story_id": story_id,
                    "halt_path": str(halt_path),
                    "reason": reason,
                },
            )
            raise WorkerHaltPrespawnError(
                story_id=story_id,
                worktree=worktree,
                halt_path=str(halt_path),
                reason=reason,
            )

    # Initiative pilot_findings_closure v3 (#5 NEW-5): dirty reused worktree
    # gate. A reused worktree carrying uncommitted residue from a prior
    # aborted run makes the runner's Stage 0 halt ("working tree not clean").
    # The ``worktree_dirty_pre_spawn`` warning already fired upstream but took
    # no action — the worker spawned and failed anyway. Resolve it here:
    #   auto_clean_dirty_worktree=True (default) → reset --hard + clean -fd
    #     (destructive, but :func:`_clean_dirty_worktree` is gated to managed
    #      ``.worktrees/`` residue under orchestrator control — never a user
    #      repo);
    #   False (safe mode) → emit WORKER_HALT_PRESPAWN reason=dirty_worktree and
    #     refuse to spawn, so the operator can inspect the residue by hand.
    # NEW-5 recheck (v4 §3): ``.claude/`` entries are filtered out — embedded
    # skills are an intentional inject, not dirty residue. Only real dirt
    # outside ``.claude/`` arms the gate.
    dirty_raw = await _git_porcelain(wt_path)
    dirty = filter_dirty_outside_claude(dirty_raw) if dirty_raw else dirty_raw
    if dirty:
        if auto_clean_dirty_worktree:
            await _clean_dirty_worktree(wt_path)
            log.info(
                "worktree_auto_cleaned story=%s discarded_files=%d",
                story_id,
                len(dirty),
            )
        else:
            dirty_jsonl_path = worker_jsonl_path(worktree)
            dirty_jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            _emit(
                dirty_jsonl_path,
                {
                    "event_type": "worker_halt_prespawn",
                    "worktree": worktree,
                    "story_id": story_id,
                    "halt_path": "",
                    "reason": "dirty_worktree",
                    "dirty_count": len(dirty),
                },
            )
            raise WorkerHaltPrespawnError(
                story_id=story_id,
                worktree=worktree,
                halt_path="",
                reason="dirty_worktree",
            )

    # Initiative pilot_findings_closure S5 (#5 R2): MCP readiness gate.
    # When ``required_mcp_tools`` is non-empty, poll ``claude mcp list --json``
    # for up to ``mcp_readiness_timeout_s`` and refuse to spawn if any tool is
    # not authenticated. Emit MCP_NOT_READY to the worker JSONL audit log so
    # downstream subscribers can correlate the halt to a specific story.
    if required_mcp_tools:
        readiness: ReadinessResult = await poll_mcp_ready(
            required_mcp_tools,
            timeout_s=mcp_readiness_timeout_s,
            interval_ms=mcp_readiness_interval_ms,
        )
        if not readiness.ok:
            mcp_jsonl_path = worker_jsonl_path(worktree)
            mcp_jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            _emit(
                mcp_jsonl_path,
                {
                    "event_type": "mcp_not_ready",
                    "worktree": worktree,
                    "story_id": story_id,
                    "missing": list(readiness.missing),
                    "required": list(required_mcp_tools),
                    "elapsed_ms": readiness.elapsed_ms,
                    "polls": readiness.polls,
                    "last_error": readiness.last_error,
                },
            )
            raise MCPNotReadyError(
                story_id=story_id,
                missing=list(readiness.missing),
                elapsed_ms=readiness.elapsed_ms,
                last_error=readiness.last_error,
            )

    skills_result: ApplyResult | None = None
    if embedded_skills_root is not None:
        if allowed_worktree_root is None:
            raise ValueError(
                "allowed_worktree_root must be provided when embedded_skills_root is set"
            )
        skills_result = apply_embedded_skills(
            worktree=wt_path,
            skills_resolution_root=embedded_skills_root,
            allowed_worktree_root=allowed_worktree_root,
        )

    bin_path = _resolve_claude_bin()
    auto_mock = bin_path is None
    use_mock = auto_mock if mock is None else mock

    # FS4 B12: track caller intent on the handle. `real_requested` is True
    # only when the caller passed ``mock=False`` explicitly. When ``mock=None``
    # the caller has no preference, so the auto-mock path is not a "fallback"
    # from the runtime's perspective — yet ``fallback_reason`` is still set so
    # the @tool wrapper can decide whether to surface a loud-error envelope
    # (it knows it asked for real).
    real_requested = mock is False
    fallback_reason: str | None = None
    if use_mock and auto_mock and mock is not True:
        fallback_reason = "claude_binary_not_found"

    jsonl_path = worker_jsonl_path(worktree)
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    # Patch CC 2026-05-18: a re-used worktree path inherits events.jsonl
    # from any previous pilot. tail_jsonl_events stops at the first
    # ``worker_completed`` line it sees, so a stale terminal event from
    # the prior run would short-circuit the new pilot in <1s with a
    # bogus "silent_failure" verdict (zero new commits since base_sha).
    # Truncate to a fresh slate per spawn — preserves history only when
    # the caller archives the file before re-spawning.
    if jsonl_path.exists():
        jsonl_path.write_text("", encoding="utf-8")

    if skills_result is not None:
        _emit(
            jsonl_path,
            {
                "event_type": "embedded_skills_applied",
                "worktree": worktree,
                "story_id": story_id,
                "skills_applied": skills_result.skills_applied,
                "skills_skipped_disabled": skills_result.skills_skipped_disabled,
                "files_written": skills_result.files_written,
                "overlays_applied": skills_result.overlays_applied,
                "target_root": str(skills_result.target_root) if skills_result.target_root else None,
            },
        )

    if use_mock:
        # Synthetic spawn + completion — useful for pipeline E2E без CLI.
        # Mock-mode never actually invokes a subprocess so sandbox_kind is
        # recorded but no wrap occurs.
        sandbox_kind = "n/a-mock"
        _emit(
            jsonl_path,
            {
                "event_type": "worker_spawned",
                "worktree": worktree,
                "story_id": story_id,
                "branch": branch,
                "model": model,
                "budget_cap_usd": budget_cap_usd,
                "mock": True,
                "fallback_reason": fallback_reason,
                "real_requested": real_requested,
                "sandbox_used": False,
                "sandbox_kind": sandbox_kind,
            },
        )
        _emit(
            jsonl_path,
            {
                "event_type": "worker_completed",
                "worktree": worktree,
                "story_id": story_id,
                "exit_code": 0,
                "status": "success",
                "mock": True,
                "fallback_reason": fallback_reason,
                "review_iteration": _read_review_iteration(worktree, story_id),
            },
        )
        mock_worker_id = build_worker_id(story_id=story_id, branch=branch, pid=0)
        mock_token = register_worker(
            worker_id=mock_worker_id,
            story_id=story_id,
            worktree=worktree,
            branch=branch,
            jsonl_path=jsonl_path,
            process=None,
        )
        # Mock workers exit synchronously above, so deregister immediately —
        # the token stays available on the handle for tests that want to flip
        # it post-hoc, but the registry no longer points to it.
        unregister_worker(mock_worker_id)
        return WorkerHandle(
            worktree=worktree,
            story_id=story_id,
            branch=branch,
            pid=0,
            jsonl_path=jsonl_path,
            process=None,
            mock=True,
            fallback_reason=fallback_reason,
            real_requested=real_requested,
            sandbox_kind=sandbox_kind,
            base_sha=base_sha,
            worker_id=mock_worker_id,
            cancellation_token=mock_token,
        )

    # Real-mode subprocess.
    assert bin_path is not None  # narrowing for mypy
    args: list[str] = [bin_path, "-p", skill_invocation]
    if extra_args:
        args.extend(extra_args)

    merged_env = _build_worker_env(env)
    merged_env.setdefault("ORCHESTRATOR_WORKER_STORY_ID", story_id)
    merged_env.setdefault("ORCHESTRATOR_WORKER_BRANCH", branch)
    merged_env.setdefault("ORCHESTRATOR_WORKER_MODEL", model)
    merged_env.setdefault("ORCHESTRATOR_WORKER_BUDGET_USD", str(budget_cap_usd))

    # Phase 4 hardening #1 — SessionStart hook: inject worker policy + skill
    # snippet + workflow phase marker before subprocess.Popen so the worker
    # session starts with full context regardless of CLAUDE.md drift.
    # Phase 4 hardening #2 integration: also embed precompact state if available
    # (worker_started_at=None means skip stale guard on first launch).
    _spawn_payload = {"skill_slug": None, "skill_invocation": skill_invocation}
    _skill_slug = _resolve_skill_slug(_spawn_payload)
    _resumed_state = _MEMORY_PERSISTOR.load_state(wt_path, worker_started_at=None)
    _bootstrap_block = build_session_start_block(
        _skill_slug, story_id, resumed_state=_resumed_state
    )
    merged_env = inject_into_worker_env(merged_env, _bootstrap_block)

    # FS7 — wrap the worker command in an OS-level sandbox (default: bwrap)
    # so the inner ``claude -p`` cannot reach prod files no matter what bash
    # tricks it attempts. ``_scan_bash`` remains as defence-in-depth.
    sandbox: Sandbox = detect_sandbox() if use_sandbox else NoSandbox()

    # Initiative #1 Task 1.4 — per-worker HOME snapshot for parallel safety.
    # Only meaningful with a real sandbox (NoSandbox ignores the overlay arg,
    # and skipping the snapshot when no isolation exists avoids surprising
    # the operator with tmp churn under degraded mode).
    overlay_path: Path | None = None
    if isolated_home and isinstance(sandbox, NoSandbox) is False:
        worker_label = f"{story_id.replace('/', '_')[:48]}-{os.getpid()}"
        try:
            overlay_path = _create_isolated_home(worker_label=worker_label)
        except OSError as exc:
            log.warning(
                "isolated_home snapshot failed for %s (%s); proceeding "
                "with shared host HOME — concurrent workers may race",
                story_id, exc,
            )
            overlay_path = None

    wrapped_args = sandbox.wrap_command(
        args,
        worktree=Path(worktree),
        network=sandbox_network,
        env=merged_env,
        worker_home_overlay=overlay_path,
        cgroup_limits=cgroup_limits,
    )

    process = await asyncio.create_subprocess_exec(
        *wrapped_args,
        cwd=worktree,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=merged_env,
    )

    pid = process.pid
    worker_id = build_worker_id(story_id=story_id, branch=branch, pid=pid)
    cancellation_token = register_worker(
        worker_id=worker_id,
        story_id=story_id,
        worktree=worktree,
        branch=branch,
        jsonl_path=jsonl_path,
        process=process,
    )

    _emit(
        jsonl_path,
        {
            "event_type": "worker_spawned",
            "worktree": worktree,
            "story_id": story_id,
            "branch": branch,
            "model": model,
            "budget_cap_usd": budget_cap_usd,
            "pid": pid,
            "mock": False,
            "sandbox_used": sandbox.kind != "none",
            "sandbox_kind": sandbox.kind,
            "isolated_home": overlay_path is not None,
            "isolated_home_path": str(overlay_path) if overlay_path else None,
            "cgroup_limits": cgroup_limits if cgroup_limits else None,
        },
    )

    background_tasks: set[asyncio.Task[None]] = _BACKGROUND_TASKS
    if process.stdout is not None:
        stdout_task = asyncio.create_task(
            _stream_subprocess_stdout(process.stdout, jsonl_path, worktree),
            name=f"worker_stdout_{pid}",
        )
        background_tasks.add(stdout_task)
        stdout_task.add_done_callback(background_tasks.discard)
    wait_task = asyncio.create_task(
        _wait_and_finalize(
            process, jsonl_path, worktree, story_id,
            isolated_home_overlay=overlay_path,
            worker_id=worker_id,
        ),
        name=f"worker_wait_{pid}",
    )
    background_tasks.add(wait_task)
    wait_task.add_done_callback(background_tasks.discard)

    return WorkerHandle(
        worktree=worktree,
        story_id=story_id,
        branch=branch,
        pid=pid,
        jsonl_path=jsonl_path,
        process=process,
        mock=False,
        sandbox_kind=sandbox.kind,
        base_sha=base_sha,
        isolated_home_path=str(overlay_path) if overlay_path else None,
        cgroup_limits_applied=dict(cgroup_limits) if cgroup_limits else None,
        worker_id=worker_id,
        cancellation_token=cancellation_token,
    )


async def tail_jsonl_events(
    jsonl_path: Path,
    poll_interval: float = 0.25,
    start_seen: int = 0,
) -> AsyncIterator[dict[str, Any]]:
    """Async generator yielding new events as they appear (file tail-follow).

    Останавливается когда последний event имеет `event_type == worker_completed`
    или `event_type == worker_halt_file`. Полезно для real-time observation в
    тестах + watchdog'е.

    Patch CC 2026-05-18: ``start_seen`` skips that many existing lines before
    tailing — set by callers that share an events.jsonl with prior pilots on
    the same worktree path. Default ``0`` preserves test contract. The fresh-
    start logic lives in :func:`spawn_worker`, which now truncates a reused
    events.jsonl so the tailer never observes stale ``worker_completed`` from
    a prior run (that 0-second silent-failure trap was discovered 2026-05-18).
    """
    seen = start_seen
    while True:
        if not jsonl_path.exists():
            await asyncio.sleep(poll_interval)
            continue
        lines = jsonl_path.read_text(encoding="utf-8").splitlines()
        new = lines[seen:]
        for raw in new:
            raw = raw.strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError:
                continue
            yield ev
            seen += 1
            if ev.get("event_type") in {"worker_completed", "worker_halt_file"}:
                return
        # If we caught up but no terminal event yet — wait.
        await asyncio.sleep(poll_interval)


__all__ = [
    "ALLOWED_WORKER_ENV",
    "CLAUDE_BIN_DEFAULT",
    "DEFAULT_BUDGET_CAP_USD",
    "DEFAULT_CGROUP_LIMITS",
    "DEFAULT_MODEL",
    "DEFAULT_SKILL_INVOCATION",
    "HALT_REASON_RELPATH",
    "MCPNotReadyError",
    "WorkerHaltPrespawnError",
    "WorkerHandle",
    "filter_dirty_outside_claude",
    "spawn_worker",
    "tail_jsonl_events",
    "trigger_precompact_dump",
]
