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
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
from bmad_orchestrator.runtime.sandbox import (
    DEFAULT_CGROUP_LIMITS,
    NetworkPolicy,
    NoSandbox,
    Sandbox,
    detect_sandbox,
)

log = logging.getLogger(__name__)

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

    # ~/.local/share/claude/ — create empty so bwrap bind has a target;
    # claude will re-populate version state on first run.
    dest_share = overlay / ".local" / "share" / "claude"
    dest_share.mkdir(parents=True, exist_ok=True)

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


def _resolve_claude_bin() -> str | None:
    """Return path to claude binary or None if not available."""
    return shutil.which(CLAUDE_BIN_DEFAULT)


def _emit(jsonl_path: Path, event: dict[str, Any]) -> None:
    payload = {"ts": now_iso(), **event}
    append_jsonl(jsonl_path, payload)


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


async def _wait_and_finalize(
    process: asyncio.subprocess.Process,
    jsonl_path: Path,
    worktree: str,
    story_id: str,
    *,
    isolated_home_overlay: Path | None = None,
) -> None:
    """Wait for subprocess exit; append final worker_completed event.

    FS3 H15: hard upper bound on the wait (configurable via BMAD_WORKER_TIMEOUT_SEC,
    default 24h) so a deadlocked worker can't pin the background task forever.
    On timeout: SIGKILL + emit subprocess_timeout audit + emit
    worker_completed with status=failure.

    Initiative #1 Task 1.4: if ``isolated_home_overlay`` was used, rm-rf the
    snapshot tmpdir after process exit (best-effort; never raises).
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
        },
    )
    _cleanup_isolated_home(isolated_home_overlay)


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
            },
        )
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
    )


async def tail_jsonl_events(
    jsonl_path: Path, poll_interval: float = 0.25
) -> AsyncIterator[dict[str, Any]]:
    """Async generator yielding new events as they appear (file tail-follow).

    Останавливается когда последний event имеет `event_type == worker_completed`
    или `event_type == worker_halt_file`. Полезно для real-time observation в
    тестах + watchdog'е.
    """
    seen = 0
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
    "WorkerHandle",
    "spawn_worker",
    "tail_jsonl_events",
]
