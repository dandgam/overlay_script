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
import os
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bmad_orchestrator.agent.tools._common import (
    append_jsonl,
    now_iso,
    worker_jsonl_path,
)

CLAUDE_BIN_DEFAULT = "claude"
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_BUDGET_CAP_USD = 30.0
DEFAULT_SKILL_INVOCATION = "/bmad-auto-dev"

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
    process: asyncio.subprocess.Process, jsonl_path: Path, worktree: str, story_id: str
) -> None:
    """Wait for subprocess exit; append final worker_completed event."""
    rc = await process.wait()
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
) -> WorkerHandle:
    """Spawn a worker. `mock=None` → auto-detect (mock-mode if claude binary absent).

    Returns immediately; subprocess лог-стрим продолжается в фоне как создаваемые
    asyncio.Tasks. Caller получает PID + JSONL path для observation.
    """
    wt_path = Path(worktree)
    if not wt_path.exists():
        raise FileNotFoundError(f"worktree path missing: {worktree}")

    bin_path = _resolve_claude_bin()
    auto_mock = bin_path is None
    use_mock = auto_mock if mock is None else mock

    jsonl_path = worker_jsonl_path(worktree)
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)

    if use_mock:
        # Synthetic spawn + completion — useful for pipeline E2E без CLI.
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

    process = await asyncio.create_subprocess_exec(
        *args,
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
        _wait_and_finalize(process, jsonl_path, worktree, story_id),
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
    "DEFAULT_MODEL",
    "DEFAULT_SKILL_INVOCATION",
    "WorkerHandle",
    "spawn_worker",
    "tail_jsonl_events",
]
