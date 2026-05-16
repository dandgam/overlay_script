"""Master orchestrator entry point (spec §2 + §4 + §11 + FS4 B1/B11).

Builds an SDK-compatible ``ClaudeAgentOptions`` kwargs dict:

- ``mcp_servers={"bmad_orchestrator": create_sdk_mcp_server(...all 34 tools...)}``
- ``allowed_tools=[]`` (empty = no SDK-level whitelist; Tool Search Tool beta
  handles defer-loading at API level — `@tool` decorator does not yet expose
  `defer_loading=True` per-tool; verified via `inspect.signature(tool)` 2026-05).
- ``ALWAYS_ON_TOOLS`` constant lists the 5 tools the spec considers preloaded:
  start_wave, stop_orchestrator, escalate_to_human, read_memory,
  read_sprint_status. Tests assert this matches the spec contract.
- ``system_prompt`` — string (SDK type is ``str | SystemPromptPreset |
  SystemPromptFile``); we concat the cached block list via ``blocks_to_string``.
  Anthropic CLI subprocess re-applies prompt caching at the API boundary when
  the system block crosses the size threshold.
- ``hooks={"PreToolUse": [HookMatcher(...)], "PostToolUse": [HookMatcher(...)]}``
  per SDK contract (raw callables in a list — not via the `HookMatcher`
  wrapper — silently no-op).
- ``betas=settings.beta_headers`` — 4 mandatory headers from agent/betas.py.

Real-mode semantics (FS4 B1):

- ``mock=True`` (default in CI / unit-tests) → DAG cascade pilot, no SDK.
- ``mock=False`` →
    1. Build options.
    2. ``_validate_sdk_options`` — instantiate ``ClaudeAgentOptions(**opts)``,
       raise ``RuntimeError`` on ``TypeError`` (NOT silent log-warning).
    3. ``raise NotImplementedError("real mode requires Wave 1a pilot wiring;
       use --mock for now")`` with ``log.error``. The full event-driven loop is
       deferred to the Odyssey Wave 1a pilot run (separate initiative). Real-mode
       returning silently was a security/correctness blocker (B1).

Memory tool (``memory_20250818``) is server-managed via the
``context-management-2025-06-27`` beta and is NOT a `@tool`-decorated function.
It cannot live in ``mcp_servers`` (those carry user-defined SDK MCP tools). It
would need to flow as a raw tool-block via direct Messages API. For the SDK
path we omit it — the orchestrator's local ``read_memory``/``write_memory``
tools cover the workflow until SDK exposes server-managed tool blocks.
"""

from __future__ import annotations

import asyncio
import secrets
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import structlog

if TYPE_CHECKING:
    from claude_agent_sdk.types import HookCallback

from bmad_orchestrator.agent.safety.budget_guard import BudgetGuard
from bmad_orchestrator.agent.safety.hooks import audit_tool_output, security_check_hook
from bmad_orchestrator.agent.skills import dispatch as dispatch_skills
from bmad_orchestrator.agent.skills import load_body as load_skill_body
from bmad_orchestrator.agent.system_prompt import blocks_to_string, build_system_prompt
from bmad_orchestrator.agent.tools import ALL_TOOLS
from bmad_orchestrator.config import ModelConfig, load_settings
from bmad_orchestrator.runtime.dag_planner import DagPlanner
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.runtime.worker_spawn import spawn_worker as runtime_spawn_worker

log = structlog.get_logger(__name__)


# 5 always-on tools per spec FS4 B11. Names match @tool registrations exactly
# (see agent/tools/*.py). When SDK exposes `defer_loading=True` per-tool, switch
# from the constant to per-tool flags. Until then this is the contract tests
# assert against.
ALWAYS_ON_TOOLS: tuple[str, ...] = (
    "start_wave",
    "stop_orchestrator",
    "escalate_to_human",
    "read_memory",
    "read_sprint_status",
)

MCP_SERVER_NAME: str = "bmad_orchestrator"


# ── public API ───────────────────────────────────────────────────────────────


async def run_orchestrator(
    project: str,
    wave: str,
    max_parallel: int = 2,
    *,
    models: ModelConfig | None = None,
    mock: bool = False,
    event_loop: EventLoop | None = None,
) -> EventLoop:
    """Main orchestrator loop. Returns the EventLoop instance.

    ``mock=True`` → DAG cascade only (no SDK). Used in tests / CI without
    ``ANTHROPIC_API_KEY``.

    ``mock=False`` → validates options against the real SDK and raises
    ``NotImplementedError`` because the full event-driven loop is deferred to
    the Wave 1a pilot. Silent no-op was a B1 blocker.
    """
    settings = load_settings()
    models = models or settings.models
    bus = event_loop or EventLoop()

    log.info(
        "orchestrator_starting",
        project=project,
        wave=wave,
        max_parallel=max_parallel,
        target=str(settings.target_project),
        planner=models.planner,
        dev=models.dev,
        mock=mock,
    )

    budget = BudgetGuard(settings.budget, event_loop=bus)

    if mock:
        await _run_mock_pilot(bus, wave=wave, max_parallel=max_parallel, budget=budget)
        return bus

    # Real mode — FS4 B1: validate options shape against the SDK and refuse
    # silently-succeeding no-op behavior.
    options = build_agent_options(
        project_root=settings.target_project,
        wave=wave,
        models=models,
    )
    _validate_sdk_options(options)

    log.error(
        "real_mode_not_implemented",
        hint="full event loop deferred to Odyssey Wave 1a pilot; use --mock for now",
        tool_count=len(ALL_TOOLS),
        always_on=list(ALWAYS_ON_TOOLS),
    )
    raise NotImplementedError(
        "real mode requires Wave 1a pilot wiring; use --mock for now"
    )


def build_agent_options(
    *,
    project_root: Path,
    wave: str,
    models: ModelConfig,
) -> dict[str, Any]:
    """Build the kwargs dict passed to ``ClaudeAgentOptions(**...)``.

    Returns a plain dict (not the SDK class) so unit tests can assert on shape
    without instantiating the SDK type. ``_validate_sdk_options`` is the
    instantiation gate before real-mode use.
    """
    from claude_agent_sdk import HookMatcher, create_sdk_mcp_server

    settings = load_settings()

    system_blocks = build_system_prompt(
        project_root=project_root, wave=wave, locale=settings.locale
    )
    system_prompt: str = blocks_to_string(system_blocks)

    mcp_server = create_sdk_mcp_server(
        name=MCP_SERVER_NAME,
        version="0.1.0",
        tools=list(ALL_TOOLS),
    )

    return {
        "system_prompt": system_prompt,
        "mcp_servers": {MCP_SERVER_NAME: mcp_server},
        "allowed_tools": [],
        "model": models.dev,
        "betas": list(settings.beta_headers),
        # Hook functions live in agent/safety/hooks.py with `dict[str, Any]`
        # signatures (predate SDK's typed HookInput union). Cast at the boundary
        # — SDK invokes them with the typed input dict at runtime; the cast
        # only relaxes mypy's strict check.
        "hooks": {
            "PreToolUse": [
                HookMatcher(
                    matcher=None,
                    hooks=[cast("HookCallback", security_check_hook)],
                )
            ],
            "PostToolUse": [
                HookMatcher(
                    matcher=None,
                    hooks=[cast("HookCallback", audit_tool_output)],
                )
            ],
        },
    }


def _validate_sdk_options(options: dict[str, Any]) -> None:
    """Instantiate ``ClaudeAgentOptions(**options)`` — raise ``RuntimeError`` on TypeError.

    FS4 B1: the previous `_attempt_sdk_run` swallowed `TypeError` as a warning
    log line, which masked SDK contract drift (e.g. renaming ``beta_headers`` →
    ``betas``). Promoting to ``RuntimeError`` surfaces real shape bugs before
    they reach the bot/agent flow.
    """
    from claude_agent_sdk import ClaudeAgentOptions

    try:
        ClaudeAgentOptions(**options)
    except TypeError as exc:
        raise RuntimeError(
            f"ClaudeAgentOptions shape mismatch (SDK version drift?): {exc}"
        ) from exc


# ── mock pilot ───────────────────────────────────────────────────────────────


async def _run_mock_pilot(
    bus: EventLoop,
    *,
    wave: str,
    max_parallel: int,
    budget: BudgetGuard,
) -> None:
    """E2E pilot per spec §22: DAG → ready → spawn → JSONL → wave_boundary.

    Никаких сетевых вызовов. Использует ``runtime.worker_spawn(mock=True)`` —
    те же samples что и tests/test_s3_runtime.py mock pilot.
    """
    from bmad_orchestrator.agent.tools._common import (
        read_sprint_status_yaml,
        write_sprint_status_yaml,
    )

    settings = load_settings()
    worktree_root = settings.target_project / ".worktrees"
    worktree_root.mkdir(parents=True, exist_ok=True)

    planner = DagPlanner.from_target()
    spawned: list[str] = []
    rounds = 0
    max_rounds = 6  # cap для unit-теста — 4 stories обычно дрова за 3 round'a

    while rounds < max_rounds:
        ready = [s for s in planner.find_ready(max_n=max_parallel * 2) if s["id"] not in spawned]
        if not ready:
            break

        batch = ready[:max_parallel]
        for story in batch:
            # C5 (round 2) — atomic check-and-reserve BEFORE spawn. Without
            # this, two concurrent ``get_budget → spawn`` pairs could both
            # observe ``spent < halt`` and overcommit. ``enforce_and_reserve_story``
            # serialises on the SQLite write lock; if the DB is unbound (CI
            # without state.db) the fallback path still returns a synthetic
            # decision so the mock pilot stays deterministic.
            reserve = budget.cfg.story_alarm_usd / 6.0  # mock spend ≈ $5/story
            res = await budget.enforce_and_reserve_story(story["id"], reserve)
            if not res.allowed:
                await bus.emit(
                    EventType.BUDGET_THRESHOLD_HIT,
                    scope=res.scope,
                    level="halt",
                    spent_usd=res.current_usd,
                    alarm_threshold=res.alarm_threshold,
                    halt_threshold=res.halt_threshold,
                    corrupted=False,
                    story_id=story["id"],
                    reason=res.reason,
                )
                continue
            wt = worktree_root / f"wt-{story['id']}"
            wt.mkdir(exist_ok=True)
            handle = await runtime_spawn_worker(
                worktree=str(wt),
                story_id=story["id"],
                branch=f"feature/{story['id']}",
                mock=True,
            )
            spawned.append(story["id"])
            await bus.emit(
                EventType.WORKER_COMPLETED,
                story_id=story["id"],
                worktree=str(wt),
                jsonl=str(handle.jsonl_path),
                mock=True,
            )

        # Mark them done in sprint-status so next round picks up dependents.
        snap = read_sprint_status_yaml()
        for sid in spawned:
            for epic_block in (snap.get("epics") or {}).values():
                if isinstance(epic_block, dict) and sid in (epic_block.get("stories") or {}):
                    epic_block["stories"][sid] = "done"
        write_sprint_status_yaml(snap)
        planner.reload()
        rounds += 1

        # Track per-batch budget (mock spend ≈ $5 / story for the cascade).
        await budget.enforce_batch(spent_usd=5.0 * len(spawned), wave=wave)

    await bus.emit(
        EventType.WAVE_BOUNDARY_REACHED,
        wave=wave,
        spawned=spawned,
        rounds=rounds,
    )
    log.info("mock_pilot_done", stories=len(spawned), rounds=rounds)


# ── HUMAN_QUERY / HUMAN_RESPONSE subscriber (FS4 B9 stub) ────────────────────


async def human_query_subscriber(event: Event, bus: EventLoop) -> None:
    """Stub subscriber: on USER_CHAT_MESSAGE / HUMAN_QUERY → emit HUMAN_RESPONSE.

    Full LLM dispatch through the intent-router skill body is deferred to the
    Wave 1a pilot (loud `log.warning` here). For now this echoes a placeholder
    response carrying the same `corr_id` so the bot's per-chat FIFO can
    resolve its pending future end-to-end in tests.
    """
    if event.type not in (EventType.USER_CHAT_MESSAGE, EventType.HUMAN_QUERY):
        return

    payload = event.payload or {}
    chat_id = payload.get("chat_id")
    corr_id = payload.get("corr_id") or secrets.token_hex(8)
    text = payload.get("text", "")

    skills = dispatch_skills(event.type)
    for skill in skills:
        body = load_skill_body(skill)
        log.debug("skill_body_loaded", skill=skill, body_chars=len(body))

    log.warning(
        "human_query_intent_router_deferred",
        hint="Wave 1a pilot will wire intent-router skill body to LLM dispatch",
        chat_id=chat_id,
        corr_id=corr_id,
        text_preview=text[:80],
    )

    await bus.emit(
        EventType.HUMAN_RESPONSE,
        chat_id=chat_id,
        corr_id=corr_id,
        text=f"(stub) принято: {text[:80]}",
    )


# ── helpers ──────────────────────────────────────────────────────────────────


def main() -> None:
    """Module-entry shim for ``python -m bmad_orchestrator.agent.run <project> <wave>``."""
    import sys

    if len(sys.argv) < 3:
        print("Usage: python -m bmad_orchestrator.agent.run <project> <wave>")
        sys.exit(2)
    asyncio.run(run_orchestrator(sys.argv[1], sys.argv[2], mock=True))


__all__ = [
    "ALWAYS_ON_TOOLS",
    "MCP_SERVER_NAME",
    "build_agent_options",
    "human_query_subscriber",
    "main",
    "run_orchestrator",
]


if __name__ == "__main__":
    main()
