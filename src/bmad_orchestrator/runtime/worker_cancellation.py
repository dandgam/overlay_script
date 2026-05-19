"""Per-worker cancellation tokens (Initiative pilot_findings_closure S4 / #4 R1).

Pattern inspired by leaked claude-code ``tools/AgentTool/runAgent.ts:520`` (the
idea — a token bound to each spawned worker that the coordinator can flip
without waiting for SIGTERM + the orchestrator-wide ``BMAD_WORKER_TIMEOUT_SEC``
ceiling, default 1800s).

Layout:

* :class:`CancellationToken` — ``asyncio.Event`` wrapper that also remembers
  *why* it was cancelled and *who* did it. ``set`` is idempotent.
* :class:`WorkerRegistration` — registry record (token + worktree + story +
  the live :class:`asyncio.subprocess.Process`).
* Module-level :data:`_REGISTRY` keyed by ``worker_id`` (string, derived from
  ``story_id + branch + pid``).
* :func:`register_worker`, :func:`unregister_worker`, :func:`get_token`,
  :func:`get_token_for_story`, :func:`active_worker_ids` — public surface used
  by ``spawn_worker`` and supervisor actions.
* :func:`cancel_worker` — flips the token, SIGTERMs the process, falls back
  to SIGKILL after ``grace_seconds``, and emits a ``WORKER_CANCELLED`` JSONL
  audit record.

Design notes:

* The registry is process-local. Workers are spawned via
  ``asyncio.create_subprocess_exec`` inside the orchestrator process, so a
  shared dict is enough — no cross-process IPC required.
* Mock workers (no real subprocess) still register a token so tests can assert
  cancellation semantics end-to-end.
* ``cancel_worker`` is async because SIGTERM → wait → SIGKILL needs to
  cooperate with the asyncio event loop. It returns ``True`` if the token was
  newly cancelled, ``False`` if the worker was unknown or already cancelled
  (idempotent).
"""

from __future__ import annotations

import asyncio
import logging
import signal
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bmad_orchestrator.agent.tools._common import append_jsonl, now_iso

log = logging.getLogger(__name__)

# Time to wait between SIGTERM and SIGKILL when the worker doesn't exit.
DEFAULT_GRACE_SECONDS: float = 2.0


@dataclass(slots=True)
class CancellationToken:
    """One-shot cancellation signal bound to a single worker.

    ``set`` is idempotent — repeated calls keep the original ``reason`` and
    ``cancelled_by`` so audit logs reflect the first trigger.
    """

    worker_id: str
    _event: asyncio.Event = field(default_factory=asyncio.Event)
    reason: str | None = None
    cancelled_by: str | None = None

    @property
    def is_set(self) -> bool:
        return self._event.is_set()

    def set(self, *, reason: str, cancelled_by: str) -> bool:
        """Flip the token. Returns True if newly set, False if already set."""
        if self._event.is_set():
            return False
        self.reason = reason
        self.cancelled_by = cancelled_by
        self._event.set()
        return True

    async def wait(self) -> None:
        await self._event.wait()


@dataclass(slots=True)
class WorkerRegistration:
    """One entry in :data:`_REGISTRY`."""

    worker_id: str
    story_id: str
    worktree: str
    branch: str
    token: CancellationToken
    process: asyncio.subprocess.Process | None
    jsonl_path: Path


_REGISTRY: dict[str, WorkerRegistration] = {}


def build_worker_id(*, story_id: str, branch: str, pid: int) -> str:
    """Stable per-process worker ID. PID disambiguates retries with same story."""
    safe_story = story_id.replace("/", "_")
    safe_branch = branch.replace("/", "_")
    return f"{safe_story}::{safe_branch}::{pid}"


def register_worker(
    *,
    worker_id: str,
    story_id: str,
    worktree: str,
    branch: str,
    jsonl_path: Path,
    process: asyncio.subprocess.Process | None,
) -> CancellationToken:
    """Create a token and register the worker. Returns the token."""
    token = CancellationToken(worker_id=worker_id)
    _REGISTRY[worker_id] = WorkerRegistration(
        worker_id=worker_id,
        story_id=story_id,
        worktree=worktree,
        branch=branch,
        token=token,
        process=process,
        jsonl_path=jsonl_path,
    )
    return token


def unregister_worker(worker_id: str) -> None:
    """Remove the worker from the registry. No-op if absent."""
    _REGISTRY.pop(worker_id, None)


def get_token(worker_id: str) -> CancellationToken | None:
    reg = _REGISTRY.get(worker_id)
    return reg.token if reg is not None else None


def get_token_for_story(story_id: str) -> CancellationToken | None:
    """First registered token whose story_id matches. Useful when callers know
    the story but not the PID-suffixed worker_id."""
    for reg in _REGISTRY.values():
        if reg.story_id == story_id:
            return reg.token
    return None


def active_worker_ids() -> list[str]:
    return list(_REGISTRY.keys())


def _emit_cancelled_event(reg: WorkerRegistration, token: CancellationToken) -> None:
    payload: dict[str, Any] = {
        "ts": now_iso(),
        "event_type": "worker_cancelled",
        "worker_id": reg.worker_id,
        "story_id": reg.story_id,
        "worktree": reg.worktree,
        "branch": reg.branch,
        "reason": token.reason,
        "cancelled_by": token.cancelled_by,
    }
    try:
        append_jsonl(reg.jsonl_path, payload)
    except OSError as exc:
        log.warning(
            "worker_cancelled_emit_failed worker_id=%s error=%s",
            reg.worker_id, exc,
        )


async def cancel_worker(
    worker_id: str,
    *,
    reason: str,
    cancelled_by: str,
    grace_seconds: float = DEFAULT_GRACE_SECONDS,
) -> bool:
    """Flip the worker's token, SIGTERM then SIGKILL its process.

    Idempotent: a second call on a token that's already set returns ``False``
    without re-killing.

    ``cancelled_by`` should be one of ``supervisor`` / ``user`` / ``timeout``
    so downstream observers can attribute the action.

    Returns ``True`` if the token was newly cancelled, ``False`` if the worker
    was unknown or already cancelled.
    """
    reg = _REGISTRY.get(worker_id)
    if reg is None:
        log.debug("cancel_worker_unknown worker_id=%s", worker_id)
        return False

    newly_set = reg.token.set(reason=reason, cancelled_by=cancelled_by)
    if not newly_set:
        log.debug("cancel_worker_idempotent worker_id=%s", worker_id)
        return False

    _emit_cancelled_event(reg, reg.token)

    process = reg.process
    if process is None or process.returncode is not None:
        return True

    try:
        process.send_signal(signal.SIGTERM)
    except (ProcessLookupError, OSError) as exc:
        log.debug("cancel_worker_sigterm_skip worker_id=%s error=%s", worker_id, exc)
        return True

    try:
        await asyncio.wait_for(process.wait(), timeout=grace_seconds)
        return True
    except TimeoutError:
        pass

    try:
        process.kill()
    except (ProcessLookupError, OSError) as exc:
        log.debug("cancel_worker_sigkill_skip worker_id=%s error=%s", worker_id, exc)
    return True


__all__ = [
    "DEFAULT_GRACE_SECONDS",
    "CancellationToken",
    "WorkerRegistration",
    "active_worker_ids",
    "build_worker_id",
    "cancel_worker",
    "get_token",
    "get_token_for_story",
    "register_worker",
    "unregister_worker",
]
