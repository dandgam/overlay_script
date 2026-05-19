"""MCP server readiness polling — spec_pilot_findings_closure §1 #5 (R2).

Before a worker is spawned the orchestrator may require that one or more MCP
tools are not just *connected* to the Claude CLI but actually
*authenticated* (e.g. OAuth flow finished, token still valid). A worker that
spawns against a half-authenticated MCP wastes ~5 minutes and several dollars
before hitting a ``tool not found`` deep inside Stage 4.

The helper here polls ``claude mcp list --json`` at ``interval_ms`` cadence
(default 500ms) for up to ``timeout_s`` (default 30s) and returns whether
every entry in ``required_tools`` is reported as ``authenticated: true``.

The runtime guarantees:

* Pure stdlib + asyncio — no extra deps. Subprocess call is cheap (~50ms)
  so a 30s window comfortably fits 60 polls at 500ms each.
* No partial-credit: a tool that is ``connected: true`` but not
  ``authenticated: true`` counts as missing — we only spawn against a fully
  ready MCP.
* Tolerant to malformed JSON / missing fields: any parsing error treats the
  output as «no tools ready», so missing-binary or upgrade-in-progress cases
  fall through to a timeout rather than crashing the caller.
* Opt-in by default: ``Settings.required_mcp_tools`` is empty unless
  explicitly populated, so existing callers (real-mode pilots that don't
  rely on MCP) are unaffected.

Used by :func:`runtime.worker_spawn.spawn_worker` (pre-Popen check) and by
``runtime/worker_cancellation`` consumers that want to surface
``MCP_NOT_READY`` to the orchestrator event bus.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

MCP_LIST_BIN_DEFAULT = "claude"
"""CLI binary used to query MCP state. Override by passing ``cli_argv`` to
:func:`poll_mcp_ready` (e.g. for tests that mock the subprocess)."""

DEFAULT_TIMEOUT_S = 30
DEFAULT_INTERVAL_MS = 500


@dataclass(slots=True)
class ReadinessResult:
    """Outcome of one :func:`poll_mcp_ready` invocation.

    Attributes:
        ok: True iff every entry in ``required_tools`` is authenticated.
        missing: Tools (by name) that were not authenticated at the moment
            the poll loop exited. Empty when ``ok`` is True.
        elapsed_ms: Wall-clock time spent polling, milliseconds. Capped by
            the caller's ``timeout_s`` (within one poll-interval slack).
        polls: Number of ``claude mcp list`` invocations executed. Useful
            for tests asserting backoff behaviour.
        last_error: Last subprocess / JSON parse error message, if any. ``None``
            when the final poll succeeded or no error was ever observed.
    """

    ok: bool
    missing: list[str] = field(default_factory=list)
    elapsed_ms: int = 0
    polls: int = 0
    last_error: str | None = None


def _resolve_cli() -> list[str] | None:
    """Return the argv prefix for ``claude mcp list --json`` or ``None``."""
    bin_path = shutil.which(MCP_LIST_BIN_DEFAULT)
    if bin_path is None:
        return None
    return [bin_path, "mcp", "list", "--json"]


def parse_mcp_list_output(stdout: str) -> dict[str, bool]:
    """Parse ``claude mcp list --json`` stdout → {tool_name: authenticated}.

    Two output shapes are accepted (the Claude CLI has historically emitted
    both):

    1. ``{"servers": [{"name": "analyzer", "authenticated": true, ...}, ...]}``
       — current shape as of 2026-05.
    2. ``[{"name": "analyzer", "authenticated": true, ...}, ...]`` — older
       shape; still supported for backward-compat.

    Unknown / malformed shapes return ``{}`` (signalling «no tools ready»).
    The caller treats an empty mapping as «every required tool is missing»,
    so an authentic CLI breakage degrades safely into a halt-before-spawn
    rather than a false positive.
    """
    if not stdout.strip():
        return {}
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return {}

    items: list[dict[str, object]] | None = None
    if isinstance(data, dict):
        servers = data.get("servers")
        if isinstance(servers, list):
            items = [s for s in servers if isinstance(s, dict)]
    elif isinstance(data, list):
        items = [s for s in data if isinstance(s, dict)]

    if items is None:
        return {}

    result: dict[str, bool] = {}
    for entry in items:
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            continue
        authed = entry.get("authenticated")
        result[name] = authed is True
    return result


async def _run_mcp_list(argv: Sequence[str]) -> tuple[str, str | None]:
    """Run the CLI once; return (stdout, error_msg_or_None)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return "", f"spawn_failed: {exc}"
    try:
        stdout_b, stderr_b = await proc.communicate()
    except asyncio.CancelledError:
        proc.kill()
        raise
    if proc.returncode != 0:
        msg = stderr_b.decode("utf-8", errors="replace").strip()
        return "", f"rc={proc.returncode}: {msg}" if msg else f"rc={proc.returncode}"
    return stdout_b.decode("utf-8", errors="replace"), None


def _compute_missing(
    required: Sequence[str], state: dict[str, bool]
) -> list[str]:
    """Return required tools that are NOT authenticated. Preserves caller order."""
    return [name for name in required if not state.get(name, False)]


async def poll_mcp_ready(
    required_tools: Sequence[str],
    *,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    interval_ms: int = DEFAULT_INTERVAL_MS,
    cli_argv: Sequence[str] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> ReadinessResult:
    """Poll the Claude CLI until ``required_tools`` are authenticated or timeout.

    Args:
        required_tools: Names of MCP servers (matching the ``name`` field of
            ``claude mcp list --json``) that must be authenticated before
            the caller spawns a worker. Empty list → immediate ``ok=True``
            (no polling, no subprocess call).
        timeout_s: Hard upper bound on the poll loop, seconds. Default 30s.
        interval_ms: Sleep between polls, milliseconds. Default 500ms.
        cli_argv: Override the CLI invocation. ``None`` → auto-resolves
            ``claude`` from ``PATH``. Tests should pass an explicit argv
            (e.g. pointing at a stub script).
        clock: Monotonic clock function — injected for deterministic tests.
        sleep: Async sleep function — injected for fast tests.

    Returns:
        A :class:`ReadinessResult`. ``ok`` is True iff every required tool
        was observed as authenticated at least once during the poll window.
    """
    if not required_tools:
        return ReadinessResult(ok=True, missing=[], elapsed_ms=0, polls=0)

    if cli_argv is None:
        argv = _resolve_cli()
        if argv is None:
            return ReadinessResult(
                ok=False,
                missing=list(required_tools),
                elapsed_ms=0,
                polls=0,
                last_error="claude_binary_not_found",
            )
    else:
        argv = list(cli_argv)

    if timeout_s <= 0:
        return ReadinessResult(
            ok=False,
            missing=list(required_tools),
            elapsed_ms=0,
            polls=0,
            last_error="timeout_non_positive",
        )

    interval = max(0.001, interval_ms / 1000.0)
    started = clock()
    deadline = started + timeout_s
    polls = 0
    last_error: str | None = None
    missing: list[str] = list(required_tools)

    while True:
        polls += 1
        stdout, err = await _run_mcp_list(argv)
        if err is not None:
            last_error = err
            state: dict[str, bool] = {}
        else:
            state = parse_mcp_list_output(stdout)
            last_error = None if state else last_error  # keep prior error if parse empty

        missing = _compute_missing(required_tools, state)
        if not missing:
            elapsed_ms = int((clock() - started) * 1000)
            return ReadinessResult(
                ok=True,
                missing=[],
                elapsed_ms=elapsed_ms,
                polls=polls,
                last_error=None,
            )

        now = clock()
        if now >= deadline:
            elapsed_ms = int((now - started) * 1000)
            return ReadinessResult(
                ok=False,
                missing=missing,
                elapsed_ms=elapsed_ms,
                polls=polls,
                last_error=last_error,
            )
        # Don't oversleep past the deadline.
        remaining = max(0.0, deadline - now)
        await sleep(min(interval, remaining))


__all__ = [
    "DEFAULT_INTERVAL_MS",
    "DEFAULT_TIMEOUT_S",
    "MCP_LIST_BIN_DEFAULT",
    "ReadinessResult",
    "parse_mcp_list_output",
    "poll_mcp_ready",
]
