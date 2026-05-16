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

Real-mode semantics (FS4 B1 + W1):

- ``mock=True`` (default in CI / unit-tests) → DAG cascade pilot, no SDK.
- ``mock=False`` →
    1. Build options.
    2. ``_validate_sdk_options`` — instantiate ``ClaudeAgentOptions(**opts)``,
       raise ``RuntimeError`` on ``TypeError`` (NOT silent log-warning).
    3. Dispatch to ``_run_real_pilot`` (W1) — DAG → spawn worker → tail JSONL →
       bridge ``worker_completed`` to bus, with ``max_stories`` / ``max_spend_usd``
       hard caps and ``BMAD_REQUIRE_SANDBOX=1`` guard at entry.

Memory tool (``memory_20250818``) is server-managed via the
``context-management-2025-06-27`` beta and is NOT a `@tool`-decorated function.
It cannot live in ``mcp_servers`` (those carry user-defined SDK MCP tools). It
would need to flow as a raw tool-block via direct Messages API. For the SDK
path we omit it — the orchestrator's local ``read_memory``/``write_memory``
tools cover the workflow until SDK exposes server-managed tool blocks.
"""

from __future__ import annotations

import asyncio
import os
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
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
from bmad_orchestrator.runtime.budget import TokenUsage, usd_cost
from bmad_orchestrator.runtime.dag_planner import DagPlanner
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.runtime.sandbox import detect_sandbox
from bmad_orchestrator.runtime.worker_spawn import (
    WorkerHandle,
    tail_jsonl_events,
)
from bmad_orchestrator.runtime.worker_spawn import (
    spawn_worker as runtime_spawn_worker,
)
from bmad_orchestrator.state.db import StateDB

SESSION_ENV_VAR = "BMAD_ORCHESTRATOR_SESSION_ID"

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
    mock: bool = True,
    event_loop: EventLoop | None = None,
    max_stories: int = 50,
    max_spend_usd: float = 50.0,
) -> EventLoop:
    """Main orchestrator loop. Returns the EventLoop instance.

    ``mock=True`` (DEFAULT, N3 FS6) → DAG cascade only (no SDK). Used in tests
    / CI without ``ANTHROPIC_API_KEY``. ``max_stories`` and ``max_spend_usd``
    are real-mode caps (W1) and are ignored by the mock path which has its own
    ``max_rounds=6`` cap.

    ``mock=False`` (W1) → validates options against the real SDK and runs
    ``_run_real_pilot`` — DAG → spawn worker → tail JSONL → bridge
    ``worker_completed`` → bus, with caller-supplied ``max_stories`` /
    ``max_spend_usd`` hard caps and ``BMAD_REQUIRE_SANDBOX=1`` guard.
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

    state_db, session_id = await _resolve_session(
        target_project=project, wave=wave, max_parallel=max_parallel, db_path=settings.state_db
    )

    budget = BudgetGuard(settings.budget, event_loop=bus)
    if state_db is not None and session_id is not None:
        budget.attach_state_db(state_db, session_id)

    if mock:
        await _run_mock_pilot(bus, wave=wave, max_parallel=max_parallel, budget=budget)
        return bus

    # Real mode — FS4 B1: validate options shape against the SDK before any
    # subprocess work. Shape drift surfaces as RuntimeError here, not later.
    options = build_agent_options(
        project_root=settings.target_project,
        wave=wave,
        models=models,
    )
    _validate_sdk_options(options)

    await _run_real_pilot(
        bus,
        project=project,
        wave=wave,
        max_parallel=max_parallel,
        max_stories=max_stories,
        max_spend_usd=max_spend_usd,
        budget=budget,
        state_db=state_db,
        session_id=session_id,
        models=models,
        options=options,
    )
    return bus


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


async def _session_exists(db: StateDB, session_id: int) -> bool:
    """Probe ``agent_session`` for ``session_id`` (running OR stopped).

    FS8: the env-supplied id may reference a DB the current process can't
    see (test isolation, distinct deployments). Validate before trusting it
    to avoid FK constraint failures when ``budget_tracker`` later inserts.
    """
    from bmad_orchestrator.state.db import connect

    async with connect(db.db_path) as conn:
        cur = await conn.execute(
            "SELECT 1 FROM agent_session WHERE id = ? LIMIT 1",
            (session_id,),
        )
        row = await cur.fetchone()
        return row is not None


async def _resolve_session(
    *,
    target_project: str,
    wave: str,
    max_parallel: int,
    db_path: Path,
) -> tuple[StateDB | None, int | None]:
    """FS8 NH1 — pick a shared cross-process session id.

    Resolution priority (highest wins):
    1. Env ``BMAD_ORCHESTRATOR_SESSION_ID`` set to an integer → use it. Lets a
       parent process (launcher script, systemd unit) hand the bot + agent
       the same id without either touching StateDB during startup race.
    2. ``StateDB.resolve_or_create_session(target_project, wave)`` — find
       existing running session for this project+wave, or create one. Atomic
       under SQLite write lock.

    Side effect: on success, exports ``BMAD_ORCHESTRATOR_SESSION_ID`` into
    ``os.environ`` so any child subprocesses (workers, bot if spawned from
    same shell) inherit it cheaply.

    Returns ``(state_db, session_id)`` on success, ``(None, None)`` if
    DB binding failed — caller falls back to unbound BudgetGuard (mock /
    CI path).
    """
    env_value = os.environ.get(SESSION_ENV_VAR)
    if env_value:
        try:
            session_id = int(env_value)
        except ValueError:
            # FS9 H8: malformed env value cannot reference any real session;
            # strip it so subsequent processes (or re-entry) don't trip on it.
            os.environ.pop(SESSION_ENV_VAR, None)
            log.warning(
                "session_env_var_invalid",
                env_var=SESSION_ENV_VAR,
                # FS9 R5 NH1: don't log raw env_value — future opaque session
                # tokens would leak as credentials. Length is enough signal.
                value_len=len(env_value),
                fallback="resolve_or_create",
                cleared=True,
            )
        else:
            try:
                db = StateDB(db_path=db_path)
                await db.init()
                if await _session_exists(db, session_id):
                    log.info(
                        "session_from_env",
                        env_var=SESSION_ENV_VAR,
                        session_id=session_id,
                    )
                    return db, session_id
                # FS9 H8: env points at a session this DB doesn't know about
                # (cross-DB / stale launcher state). Strip before re-resolve
                # to prevent a downstream consumer re-using the bogus id.
                os.environ.pop(SESSION_ENV_VAR, None)
                log.warning(
                    "session_env_var_stale",
                    env_var=SESSION_ENV_VAR,
                    # FS9 R5 NH1: avoid logging raw value (future credentials).
                    value_len=len(env_value),
                    db_path=str(db_path),
                    fallback="resolve_or_create",
                    cleared=True,
                )
            except Exception as exc:
                # FS9 H8: cross-DB failure leaves env pointing at an
                # unverifiable id. Clear so the next attempt starts clean
                # rather than re-trusting the stale value.
                os.environ.pop(SESSION_ENV_VAR, None)
                log.warning(
                    "state_db_unavailable",
                    db_path=str(db_path),
                    error=str(exc),
                    fallback="unbound_budget",
                    cleared=True,
                )
                return None, None

    try:
        db = StateDB(db_path=db_path)
        await db.init()
        session_id = await db.resolve_or_create_session(
            target_project=target_project,
            wave=wave,
            max_parallel=max_parallel,
        )
        os.environ[SESSION_ENV_VAR] = str(session_id)
        log.info(
            "session_resolved",
            target_project=target_project,
            wave=wave,
            session_id=session_id,
        )
        return db, session_id
    except Exception as exc:
        # FS9 H8: DB unavailable on the resolve_or_create path leaves no
        # valid session id; ensure env doesn't survive holding a stale value
        # from an earlier successful run.
        os.environ.pop(SESSION_ENV_VAR, None)
        log.warning(
            "state_db_unavailable",
            db_path=str(db_path),
            error=str(exc),
            fallback="unbound_budget",
            cleared=True,
        )
        return None, None


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

    # N2 (FS6) — daily cap pre-check per spawn cycle. Without a bound
    # state.db the atomic ``enforce_and_reserve_day`` cannot accumulate across
    # calls, so the mock pilot tracks a running daily total locally and feeds
    # it into the regular ``enforce_day`` evaluator. ``BMAD_DAILY_LIMIT_USD``
    # overrides ``settings.budget.daily_limit_usd``.
    daily_limit_override = os.environ.get("BMAD_DAILY_LIMIT_USD")
    if daily_limit_override:
        try:
            daily_limit = float(daily_limit_override)
        except ValueError:
            log.warning(
                "bmad_daily_limit_usd_invalid",
                value=daily_limit_override,
                fallback=budget.cfg.daily_limit_usd,
            )
        else:
            budget.cfg.daily_limit_usd = daily_limit
    today_utc = datetime.now(UTC).date().isoformat()
    daily_spent_usd = 0.0
    daily_reserve = budget.cfg.story_alarm_usd / 6.0  # mock spend ≈ $5/story

    daily_halt_reached = False
    while rounds < max_rounds and not daily_halt_reached:
        ready = [s for s in planner.find_ready(max_n=max_parallel * 2) if s["id"] not in spawned]
        if not ready:
            break

        batch = ready[:max_parallel]
        for story in batch:
            # N2 (FS6) — enforce_day BEFORE story-level reserve. Halt one
            # spawn cycle before the daily cap is breached so the orchestrator
            # stops cleanly rather than overruns.
            projected_daily = daily_spent_usd + daily_reserve
            day_res = await budget.enforce_day(projected_daily, today_utc)
            if day_res.level == "halt":
                log.warning(
                    "daily_budget_halt",
                    projected_usd=projected_daily,
                    halt_threshold=day_res.halt_threshold,
                    day=today_utc,
                )
                await bus.emit(
                    EventType.BUDGET_THRESHOLD_HIT,
                    scope="day",
                    level="halt",
                    spent_usd=projected_daily,
                    alarm_threshold=day_res.alarm_threshold,
                    halt_threshold=day_res.halt_threshold,
                    corrupted=day_res.corrupted,
                    story_id=story["id"],
                    day=today_utc,
                )
                daily_halt_reached = True
                break
            daily_spent_usd = projected_daily

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


# ── real pilot (W1 — Wave 1a wiring) ─────────────────────────────────────────


async def _run_real_pilot(
    bus: EventLoop,
    *,
    project: str,
    wave: str,
    max_parallel: int,
    max_stories: int,
    max_spend_usd: float,
    budget: BudgetGuard,
    state_db: StateDB | None,
    session_id: int | None,
    models: ModelConfig,
    options: dict[str, Any],
) -> None:
    """Real-mode E2E pilot (W1).

    Diverges from ``_run_mock_pilot`` only in:
      * ``BMAD_REQUIRE_SANDBOX`` guard at entry (raises if NoSandbox + flag).
      * ``runtime_spawn_worker(mock=False, sandbox_network="full")`` — real
        ``claude -p /bmad-auto-dev`` subprocess inside bwrap.
      * ``tail_jsonl_events`` bridges each worker's JSONL → ``WORKER_COMPLETED``
        event on the bus (instead of synthetic emit).
      * Caller-supplied ``max_stories`` / ``max_spend_usd`` hard caps on top of
        :class:`BudgetGuard` policy.

    The per-story spend reserve here is a placeholder (story_alarm_usd / 6 ≈
    $5/story) — W3 wires real cost parsing from worker JSONL ``usage`` blocks.
    """
    # W1.3 — sandbox guard. ``detect_sandbox()`` itself enforces
    # ``BMAD_REQUIRE_SANDBOX=1`` + NoSandbox → RuntimeError (FS9 H5). Calling at
    # entry surfaces the missing-bwrap failure BEFORE any DAG / spawn work.
    _ = detect_sandbox()

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
    max_rounds = 6

    daily_limit_override = os.environ.get("BMAD_DAILY_LIMIT_USD")
    if daily_limit_override:
        try:
            daily_limit = float(daily_limit_override)
        except ValueError:
            log.warning(
                "bmad_daily_limit_usd_invalid",
                value=daily_limit_override,
                fallback=budget.cfg.daily_limit_usd,
            )
        else:
            budget.cfg.daily_limit_usd = daily_limit
    today_utc = datetime.now(UTC).date().isoformat()
    daily_spent_usd = 0.0
    # Placeholder reserve — W3 will replace with adaptive cost from
    # WorkerCostTracker history. Until then we use the same heuristic the mock
    # pilot uses so behaviour is symmetric across paths.
    story_reserve = budget.cfg.story_alarm_usd / 6.0

    bus.start_backstop_task()

    daily_halt_reached = False
    while (
        rounds < max_rounds
        and not daily_halt_reached
        and len(spawned) < max_stories
        and daily_spent_usd < max_spend_usd
    ):
        ready = [s for s in planner.find_ready(max_n=max_parallel * 2) if s["id"] not in spawned]
        if not ready:
            break

        remaining_slots = max_stories - len(spawned)
        effective_max = min(max_parallel, remaining_slots)
        batch = ready[:effective_max]
        if not batch:
            break

        handles: list[WorkerHandle] = []
        for story in batch:
            projected_daily = daily_spent_usd + story_reserve

            # Local user-supplied hard cap (W1.2 --max-spend-usd).
            if projected_daily > max_spend_usd:
                log.warning(
                    "max_spend_usd_cap_reached",
                    projected_usd=projected_daily,
                    max_spend_usd=max_spend_usd,
                    story_id=story["id"],
                )
                await bus.emit(
                    EventType.BUDGET_THRESHOLD_HIT,
                    scope="day",
                    level="halt",
                    spent_usd=projected_daily,
                    alarm_threshold=max_spend_usd,
                    halt_threshold=max_spend_usd,
                    corrupted=False,
                    story_id=story["id"],
                    reason="max_spend_usd_cap",
                )
                daily_halt_reached = True
                break

            day_res = await budget.enforce_day(projected_daily, today_utc)
            if day_res.level == "halt":
                log.warning(
                    "daily_budget_halt",
                    projected_usd=projected_daily,
                    halt_threshold=day_res.halt_threshold,
                    day=today_utc,
                )
                await bus.emit(
                    EventType.BUDGET_THRESHOLD_HIT,
                    scope="day",
                    level="halt",
                    spent_usd=projected_daily,
                    alarm_threshold=day_res.alarm_threshold,
                    halt_threshold=day_res.halt_threshold,
                    corrupted=day_res.corrupted,
                    story_id=story["id"],
                    day=today_utc,
                )
                daily_halt_reached = True
                break
            daily_spent_usd = projected_daily

            res = await budget.enforce_and_reserve_story(story["id"], story_reserve)
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
                mock=False,
                sandbox_network="full",
            )
            handles.append(handle)
            spawned.append(story["id"])

        if handles:
            await asyncio.gather(
                *[_tail_and_emit_completion(h, bus) for h in handles]
            )

        snap = read_sprint_status_yaml()
        for sid in spawned:
            for epic_block in (snap.get("epics") or {}).values():
                if isinstance(epic_block, dict) and sid in (epic_block.get("stories") or {}):
                    epic_block["stories"][sid] = "done"
        write_sprint_status_yaml(snap)
        planner.reload()
        rounds += 1

        # Per-batch budget aggregate (placeholder ≈ $5/story; replaced in W3).
        await budget.enforce_batch(spent_usd=story_reserve * len(spawned), wave=wave)

    await bus.emit(
        EventType.WAVE_BOUNDARY_REACHED,
        wave=wave,
        spawned=spawned,
        rounds=rounds,
    )
    log.info(
        "real_pilot_done",
        stories=len(spawned),
        rounds=rounds,
        daily_spent_usd=daily_spent_usd,
        halted=daily_halt_reached,
    )


async def _tail_and_emit_completion(handle: WorkerHandle, bus: EventLoop) -> None:
    """Tail a worker's JSONL until terminal event; bridge to bus.

    The generator returns after seeing ``worker_completed`` or
    ``worker_halt_file``. We re-emit only the success / failure terminal to
    keep the bus surface narrow — intermediate ``claude_event`` / ``stdout_line``
    rows stay in the JSONL for forensics but don't fan out to subscribers.
    """
    async for ev in tail_jsonl_events(handle.jsonl_path):
        event_type = ev.get("event_type")
        if event_type == "worker_completed":
            await bus.emit(
                EventType.WORKER_COMPLETED,
                story_id=handle.story_id,
                worktree=handle.worktree,
                jsonl=str(handle.jsonl_path),
                exit_code=ev.get("exit_code", 0),
                status=ev.get("status", "success"),
                mock=False,
            )
            return
        if event_type == "worker_halt_file":
            await bus.emit(
                EventType.WORKER_HALT_FILE,
                story_id=handle.story_id,
                worktree=handle.worktree,
                jsonl=str(handle.jsonl_path),
            )
            return


# ── HUMAN_QUERY / HUMAN_RESPONSE subscriber (W2 — intent-router dispatch) ────

INTENT_ROUTER_SYSTEM_PROMPT: str = (
    "You are the bmad-orchestrator intent router. Parse the user's free-text "
    "request (Russian or English) and decide whether to: (a) call exactly one "
    "tool to fulfil it, or (b) reply with a plain-text clarification when the "
    "request is ambiguous or out-of-scope. Read-only intents → call the matching "
    "tool directly. Destructive intents (stop, kill, delete, rollback) → reply "
    "with text asking the user to confirm via the bot's inline buttons rather "
    "than calling the tool. Unknown intents → reply with 'не знаю' plus the "
    "closest valid options."
)

INTENT_ROUTER_TOOL_WHITELIST: frozenset[str] = frozenset({
    "start_wave",
    "stop_orchestrator",
    "escalate_to_human",
    "read_sprint_status",
})

# Module-level injection points so unit tests can configure dispatch without
# touching env / load_settings(). Production code calls
# ``configure_intent_router(budget=…, models=…)`` once during orchestrator
# start-up; tests monkeypatch ``_intent_router_client_factory`` to return a
# stub AsyncAnthropic.
_INTENT_ROUTER_BUDGET: BudgetGuard | None = None
_INTENT_ROUTER_MODELS: ModelConfig | None = None
_intent_router_client_factory: Callable[[], Any] | None = None


def configure_intent_router(
    *,
    budget: BudgetGuard | None,
    models: ModelConfig | None,
    client_factory: Callable[[], Any] | None = None,
) -> None:
    """Wire the intent-router subscriber to a budget guard + model selection.

    Called once from ``run_orchestrator`` (real-mode) so subsequent
    ``human_query_subscriber`` invocations have access to the live
    :class:`BudgetGuard` and active :class:`ModelConfig`. Tests pass a custom
    ``client_factory`` returning a stub AsyncAnthropic.
    """
    global _INTENT_ROUTER_BUDGET, _INTENT_ROUTER_MODELS, _intent_router_client_factory
    _INTENT_ROUTER_BUDGET = budget
    _INTENT_ROUTER_MODELS = models
    _intent_router_client_factory = client_factory


def _default_anthropic_client_factory() -> Any:
    """Build a fresh ``AsyncAnthropic`` client picking up ``ANTHROPIC_API_KEY``.

    Imported lazily so the test suite can run without ``anthropic`` installed
    in any unusual env. Production hosts ship it via ``pyproject.toml``.
    """
    from anthropic import AsyncAnthropic

    return AsyncAnthropic()


def _intent_router_tools() -> list[dict[str, Any]]:
    """Return the tool subset exposed to the intent router (Messages API schema).

    Filters ``ALL_TOOLS`` down to ``INTENT_ROUTER_TOOL_WHITELIST`` so the router
    cannot accidentally dispatch destructive tools (spawn / merge / control)
    from a free-text user message. The schema follows the Anthropic Messages
    API tool spec: ``{"name", "description", "input_schema": {…}}``.
    """
    tools: list[dict[str, Any]] = []
    for t in ALL_TOOLS:
        if t.name not in INTENT_ROUTER_TOOL_WHITELIST:
            continue
        schema = getattr(t, "input_schema", None) or {"type": "object", "properties": {}}
        # SDK tools store input_schema either as the dict form already or as a
        # callable producing it; tolerate both.
        if callable(schema):
            schema = schema()
        if not isinstance(schema, dict) or schema.get("type") != "object":
            schema = {"type": "object", "properties": dict(schema or {})}
        tools.append({
            "name": t.name,
            "description": t.description,
            "input_schema": schema,
        })
    return tools


def _tool_by_name(name: str) -> Any:
    """Return the registered ``@tool`` for ``name`` (or None if absent)."""
    for t in ALL_TOOLS:
        if t.name == name:
            return t
    return None


def _extract_usage(response: Any) -> TokenUsage:
    """Build :class:`TokenUsage` from an Anthropic Messages response.

    Tolerates dict-shaped and pydantic-shaped ``usage`` blocks so tests can
    stub with plain dicts.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return TokenUsage()

    def _get(field: str) -> int:
        if isinstance(usage, dict):
            return int(usage.get(field, 0) or 0)
        return int(getattr(usage, field, 0) or 0)

    return TokenUsage(
        input_tokens=_get("input_tokens"),
        cache_creation_input_tokens=_get("cache_creation_input_tokens"),
        cache_read_input_tokens=_get("cache_read_input_tokens"),
        output_tokens=_get("output_tokens"),
    )


def _iter_content_blocks(response: Any) -> list[Any]:
    """Yield ``response.content`` blocks across SDK and dict-shaped responses."""
    content = getattr(response, "content", None)
    if content is None and isinstance(response, dict):
        content = response.get("content")
    return list(content or [])


def _block_kind(block: Any) -> str:
    if isinstance(block, dict):
        return str(block.get("type") or "")
    return str(getattr(block, "type", "") or "")


def _block_text(block: Any) -> str:
    if isinstance(block, dict):
        return str(block.get("text") or "")
    return str(getattr(block, "text", "") or "")


def _tool_use_payload(block: Any) -> tuple[str, dict[str, Any]]:
    if isinstance(block, dict):
        return str(block.get("name") or ""), dict(block.get("input") or {})
    return str(getattr(block, "name", "") or ""), dict(getattr(block, "input", {}) or {})


async def _stub_human_response(
    bus: EventLoop,
    *,
    chat_id: Any,
    corr_id: str,
    text: str,
    reason: str,
) -> None:
    """Emit the legacy stub HUMAN_RESPONSE (no LLM call).

    Triggered when (a) ``ANTHROPIC_API_KEY`` is unset, (b) the budget guard
    halted dispatch, or (c) the Anthropic call raised — the bot's per-chat
    FIFO future must still resolve, so we mirror the FS4 stub contract.
    """
    log.warning(
        "human_query_stub_fallback",
        reason=reason,
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


async def _dispatch_intent_router(
    *,
    bus: EventLoop,
    chat_id: Any,
    corr_id: str,
    text: str,
    budget: BudgetGuard,
    models: ModelConfig,
) -> None:
    """Real LLM dispatch path — Anthropic Messages API + tool_use routing.

    Layers:
      1. Daily-cap pre-check via ``budget.attribute_usd`` snapshot — halt
         before any call when cumulative is already over the daily limit.
      2. Build cached system blocks (router prompt + intent-router skill body),
         restrict tools to the whitelist, call ``messages.create``.
      3. Attribute the post-call cost (via ``usd_cost`` + ``attribute_usd``);
         emit BUDGET_THRESHOLD_HIT on the day cap automatically through
         ``BudgetGuard._publish``.
      4. Walk ``response.content`` — dispatch the first whitelisted ``tool_use``
         block via its registered handler; concatenate text blocks for the
         HUMAN_RESPONSE payload.
    """
    # Pre-check: cumulative intent-router spend may already exceed daily cap.
    if budget.attributed_total() >= Decimal(str(budget.cfg.daily_limit_usd)):
        log.warning(
            "intent_router_daily_cap_blocked",
            chat_id=chat_id,
            corr_id=corr_id,
            attributed_total=str(budget.attributed_total()),
        )
        await bus.emit(
            EventType.BUDGET_THRESHOLD_HIT,
            scope="day",
            level="halt",
            spent_usd=float(budget.attributed_total()),
            alarm_threshold=budget.cfg.daily_limit_usd,
            halt_threshold=budget.cfg.daily_limit_usd,
            corrupted=False,
            attribution_scope="intent_router",
        )
        await _stub_human_response(
            bus,
            chat_id=chat_id,
            corr_id=corr_id,
            text=text,
            reason="daily_cap_halt",
        )
        return

    factory = _intent_router_client_factory or _default_anthropic_client_factory
    client = factory()

    try:
        body = load_skill_body("intent-router")
    except Exception as exc:  #skill registry malformed → loud stub
        log.error("intent_router_skill_load_failed", error=str(exc))
        await _stub_human_response(
            bus, chat_id=chat_id, corr_id=corr_id, text=text, reason="skill_load_failed"
        )
        return

    system_blocks = [
        {
            "type": "text",
            "text": INTENT_ROUTER_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": body,
            "cache_control": {"type": "ephemeral"},
        },
    ]
    messages = [{"role": "user", "content": text}]
    tools = _intent_router_tools()

    try:
        response = await client.messages.create(
            model=models.routine,
            max_tokens=512,
            system=system_blocks,
            tools=tools,
            messages=messages,
        )
    except Exception as exc:  #network / API errors → stub
        log.error(
            "intent_router_api_error",
            error_type=type(exc).__name__,
            error=str(exc)[:200],
            chat_id=chat_id,
            corr_id=corr_id,
        )
        await _stub_human_response(
            bus, chat_id=chat_id, corr_id=corr_id, text=text, reason="api_error"
        )
        return

    usage = _extract_usage(response)
    try:
        cost = usd_cost(models.routine, usage)
    except ValueError:
        # Unknown model — log loud, attribute 0, continue with dispatch so the
        # user still receives a response. Production deploys pin
        # ``models.routine`` to a known model so this branch never trips.
        log.error(
            "intent_router_unknown_model_pricing",
            model=models.routine,
        )
        cost = Decimal("0")

    cap_result = await budget.attribute_usd(scope="intent_router", spent=cost)

    total = usage.input_tokens + usage.cache_read_input_tokens
    cache_hit_ratio = (
        float(usage.cache_read_input_tokens) / float(total) if total > 0 else 0.0
    )
    log.info(
        "intent_router_dispatched",
        model=models.routine,
        chat_id=chat_id,
        corr_id=corr_id,
        input_tokens=usage.input_tokens,
        cache_read_tokens=usage.cache_read_input_tokens,
        cache_write_tokens=usage.cache_creation_input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=str(cost),
        cache_hit_ratio=round(cache_hit_ratio, 4),
        daily_attributed_total=str(budget.attributed_total()),
        daily_level=cap_result.level,
    )

    # Walk response.content — dispatch first whitelisted tool_use; collect text.
    text_parts: list[str] = []
    tool_results: list[str] = []
    for block in _iter_content_blocks(response):
        kind = _block_kind(block)
        if kind == "tool_use":
            tool_name, tool_input = _tool_use_payload(block)
            if tool_name not in INTENT_ROUTER_TOOL_WHITELIST:
                log.warning(
                    "intent_router_tool_outside_whitelist",
                    tool=tool_name,
                    chat_id=chat_id,
                )
                continue
            handler_tool = _tool_by_name(tool_name)
            if handler_tool is None:
                continue
            try:
                result = await handler_tool.handler(tool_input)
            except Exception as exc:  #tool failure → loud, but emit response
                log.error(
                    "intent_router_tool_dispatch_error",
                    tool=tool_name,
                    error_type=type(exc).__name__,
                    error=str(exc)[:200],
                )
                tool_results.append(f"(tool {tool_name} failed: {type(exc).__name__})")
                continue
            tool_results.append(f"tool:{tool_name} → {_summarise_tool_result(result)}")
        elif kind == "text":
            text_parts.append(_block_text(block))

    if tool_results:
        reply = "; ".join(tool_results)
    elif text_parts:
        reply = "\n".join(t.strip() for t in text_parts if t).strip()
    else:
        reply = "(no response)"

    await bus.emit(
        EventType.HUMAN_RESPONSE,
        chat_id=chat_id,
        corr_id=corr_id,
        text=reply or "(empty)",
    )


def _summarise_tool_result(result: Any) -> str:
    """Compact one-line summary of an SDK tool envelope for HUMAN_RESPONSE."""
    if isinstance(result, dict):
        if result.get("isError"):
            content = result.get("content")
            if isinstance(content, list) and content:
                first = content[0]
                if isinstance(first, dict):
                    return f"error:{first.get('text', '')[:120]}"
            return "error"
        content = result.get("content")
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict):
                return str(first.get("text", "ok"))[:120]
        return "ok"
    return str(result)[:120]


async def human_query_subscriber(event: Event, bus: EventLoop) -> None:
    """Route USER_CHAT_MESSAGE / HUMAN_QUERY through the intent-router skill.

    Real-mode dispatch when ``ANTHROPIC_API_KEY`` is set and
    :func:`configure_intent_router` has wired a :class:`BudgetGuard` +
    :class:`ModelConfig`. Otherwise falls back to the legacy stub
    (FS4 B9 contract) so the bot's per-chat FIFO future can still resolve in
    tests / CI without secrets.
    """
    if event.type not in (EventType.USER_CHAT_MESSAGE, EventType.HUMAN_QUERY):
        return

    payload = event.payload or {}
    chat_id = payload.get("chat_id")
    corr_id = payload.get("corr_id") or secrets.token_hex(8)
    text = payload.get("text", "")

    # Touch skill metadata so dispatcher trigger graph stays asserted at
    # runtime (and unknown event-types fail loud before hitting Anthropic).
    skills = dispatch_skills(event.type)
    for skill in skills:
        body = load_skill_body(skill)
        log.debug("skill_body_loaded", skill=skill, body_chars=len(body))

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    budget = _INTENT_ROUTER_BUDGET
    models = _INTENT_ROUTER_MODELS

    if not api_key or budget is None or models is None:
        await _stub_human_response(
            bus,
            chat_id=chat_id,
            corr_id=corr_id,
            text=text,
            reason="no_api_key" if not api_key else "intent_router_unconfigured",
        )
        return

    await _dispatch_intent_router(
        bus=bus,
        chat_id=chat_id,
        corr_id=corr_id,
        text=text,
        budget=budget,
        models=models,
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
    "INTENT_ROUTER_SYSTEM_PROMPT",
    "INTENT_ROUTER_TOOL_WHITELIST",
    "MCP_SERVER_NAME",
    "SESSION_ENV_VAR",
    "build_agent_options",
    "configure_intent_router",
    "human_query_subscriber",
    "main",
    "run_orchestrator",
]


if __name__ == "__main__":
    main()
