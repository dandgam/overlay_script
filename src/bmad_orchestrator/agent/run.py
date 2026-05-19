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
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import structlog

if TYPE_CHECKING:
    from claude_agent_sdk.types import HookCallback

from bmad_orchestrator.agent.file_conflict import split_batch
from bmad_orchestrator.agent.safety.budget_guard import BudgetGuard
from bmad_orchestrator.agent.safety.hooks import audit_tool_output, security_check_hook
from bmad_orchestrator.agent.skills import SkillError
from bmad_orchestrator.agent.skills import dispatch as dispatch_skills
from bmad_orchestrator.agent.skills import load_body as load_skill_body
from bmad_orchestrator.agent.system_prompt import blocks_to_string, build_system_prompt
from bmad_orchestrator.agent.tools import ALL_TOOLS
from bmad_orchestrator.config import ModelConfig, Settings, load_settings
from bmad_orchestrator.runtime.auto_split import (
    AutoSplitOutcome,
    DecomposeFn,
    auto_split_and_execute,
    auto_split_enabled,
)
from bmad_orchestrator.runtime.bmad_format import mark_sprint_status_done
from bmad_orchestrator.runtime.budget import TokenUsage, usd_cost
from bmad_orchestrator.runtime.budget_autodetect import (
    BudgetAutoDisableState,
    evaluate_budget_disabled,
)
from bmad_orchestrator.runtime.build_check import build_check_subscriber
from bmad_orchestrator.runtime.commit_recovery import recover_pre_merge
from bmad_orchestrator.runtime.cost_tracker import WorkerCostTracker
from bmad_orchestrator.runtime.dag_planner import DagPlanner
from bmad_orchestrator.runtime.deletion_safety import deletion_safety_subscriber
from bmad_orchestrator.runtime.diff_size_gate import (
    gate_verdict as _diff_size_gate_verdict,
)
from bmad_orchestrator.runtime.diff_size_gate import (
    load_diff_size_policy,
    measure_diff,
    measure_diff_per_file,
    partition_per_file,
)
from bmad_orchestrator.runtime.elicitation_routing import (
    load_engine as _load_elicitation_engine,
)
from bmad_orchestrator.runtime.elicitation_routing import (
    make_elicitation_subscriber,
)
from bmad_orchestrator.runtime.event_loop import Event, EventCallback, EventLoop, EventType
from bmad_orchestrator.runtime.file_list_parser import (
    collect_allow_list,
    has_explicit_file_list,
)
from bmad_orchestrator.runtime.live_tuning import (
    TuningProposal,
    apply_proposals,
    atomic_write_gates_yaml,
    evaluate_threshold,
)
from bmad_orchestrator.runtime.project_memory import (
    ProjectMemoryError,
    ProjectMemoryInvalidError,
    load_project_memory,
    save_project_memory,
)
from bmad_orchestrator.runtime.project_registry import (
    validate_project_path as _validate_project_path,
)
from bmad_orchestrator.runtime.sandbox import DEFAULT_CGROUP_LIMITS, detect_sandbox
from bmad_orchestrator.runtime.security_review import (
    SECURITY_REVIEW_SKILL_INVOCATION,
    parse_security_verdict_from_event,
    security_review_subscriber,
)
from bmad_orchestrator.runtime.security_review import (
    VERDICT_ERROR as SECURITY_VERDICT_ERROR,
)
from bmad_orchestrator.runtime.self_learning_subscriber import (
    load_self_learning_config as _load_self_learning_config,
)
from bmad_orchestrator.runtime.self_learning_subscriber import (
    make_self_learning_subscriber,
)
from bmad_orchestrator.runtime.stage5_completeness import stage5_completeness_subscriber
from bmad_orchestrator.runtime.supervisor_subscriber import (
    load_supervisor_engine as _load_supervisor_engine,
)
from bmad_orchestrator.runtime.supervisor_subscriber import (
    make_supervisor_subscriber,
)
from bmad_orchestrator.runtime.verdict_fallback import (
    parse_runner_review_log,
    read_runner_verdict,
)
from bmad_orchestrator.runtime.worker_events import (
    detect_stage_marker,
    merge_worktree_events,
    worktree_events_path,
)
from bmad_orchestrator.runtime.worker_silent_failure import (
    decide_cleanup_recovery,
    decide_worker_status,
    detect_reused_worktree_cleanup_failure,
    parse_inner_exit_code,
)
from bmad_orchestrator.runtime.worker_spawn import (
    WorkerHaltPrespawnError,
    WorkerHandle,
    tail_jsonl_events,
)
from bmad_orchestrator.runtime.worker_spawn import (
    spawn_worker as runtime_spawn_worker,
)
from bmad_orchestrator.runtime.worktree import cleanup_worktree
from bmad_orchestrator.self_learning.consolidator import Consolidator
from bmad_orchestrator.skills_repo import (
    CodeReviewGates,
    PolicyInvalidError,
    PolicyNotFoundError,
    load_policy,
)
from bmad_orchestrator.state.db import StateDB

SESSION_ENV_VAR = "BMAD_ORCHESTRATOR_SESSION_ID"

log = structlog.get_logger(__name__)


# Initiative #2C — auto-split pipeline hook. Module-level callable so tests can
# swap in a stub via ``set_decomposer`` without monkey-patching environment.
# Default ``None`` keeps the auto-split path inert even when ``BMAD_AUTO_SPLIT=1``
# is set in env — production callers must wire a real ``claude -p`` decomposer
# (or a multi-LLM router) before opting in. Until then large stories fall
# through to the legacy single-worker pipeline.
_DECOMPOSER: DecomposeFn | None = None


def set_decomposer(fn: DecomposeFn | None) -> None:
    """Inject (or clear) the auto-split decomposer used by ``_run_real_pilot_body``.

    Production: wire a callable that spawns ``claude -p --model opus`` and pipes
    the rendered prompt to its stdin. Tests: pass a synchronous async stub that
    returns canned JSON.
    """
    global _DECOMPOSER
    _DECOMPOSER = fn


def get_decomposer() -> DecomposeFn | None:
    """Return the currently-installed decomposer (or ``None``)."""
    return _DECOMPOSER


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

# NEW-8 — hard cap on the post-pilot shutdown sequence. A background task that
# refuses to cancel within this window triggers ``orchestrator_shutdown_timeout``
# + forced return instead of hanging the process (~13-min hang regression).
ORCHESTRATOR_SHUTDOWN_TIMEOUT_S: float = 30.0


# ── public API ───────────────────────────────────────────────────────────────


async def _shutdown_orchestrator(
    bus: EventLoop, *, pre_existing: set[asyncio.Task[Any]]
) -> None:
    """NEW-8 — explicit post-pilot shutdown so ``run_orchestrator`` returns
    promptly instead of hanging on the event-bus backstop task / orphan
    background coroutines (validation-replay finding: ~13-min hang after
    ``real_pilot_done``).

    Stops the event bus (cancels its backstop task), then cancels every
    background task spawned *during* this orchestrator run — identified as
    ``all_tasks() - pre_existing - {current}`` so a caller's TUI / parent
    coroutine (the ``--watch`` path) is never cancelled. ``asyncio.wait`` with
    a hard timeout bounds the wait genuinely: a task that swallows cancellation
    is left ``pending`` (logged as ``orchestrator_shutdown_timeout``) and the
    function returns regardless rather than blocking the process.
    """
    started = time.monotonic()
    await bus.stop()
    current = asyncio.current_task()
    leftover = [
        t
        for t in asyncio.all_tasks()
        if t not in pre_existing and t is not current and not t.done()
    ]
    for task in leftover:
        task.cancel()
    if leftover:
        _, pending = await asyncio.wait(
            leftover, timeout=ORCHESTRATOR_SHUTDOWN_TIMEOUT_S
        )
        if pending:
            log.warning(
                "orchestrator_shutdown_timeout",
                timeout_s=ORCHESTRATOR_SHUTDOWN_TIMEOUT_S,
                pending=len(pending),
                elapsed_sec=round(time.monotonic() - started, 2),
            )
    log.info(
        "orchestrator_shutdown_complete",
        elapsed_sec=round(time.monotonic() - started, 2),
    )


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
    stories: tuple[str, ...] | None = None,
    settings: Settings | None = None,
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

    Auth model: real-mode runs on Claude subscription via the `claude -p`
    CLI binary spawned by `runtime_spawn_worker`. ANTHROPIC_API_KEY is only
    needed for the bot's NL intent-router (graceful slash-command fallback
    without it). When multi-LLM support lands, this comment becomes outdated.
    See memory: feedback_no_anthropic_api.

    ``settings`` — when provided (e.g. by the CLI after resolving an explicit
    ``--project`` flag through the registry), it is used verbatim instead of
    ``load_settings()``. This is how ``--project`` deterministically beats the
    ``ORCHESTRATOR_TARGET_PROJECT`` env var (NEW-1): the CLI hands down a
    Settings whose ``target_project`` is already the registry-resolved path.
    """
    settings = settings or load_settings()
    models = models or settings.models
    bus = event_loop or EventLoop()

    # NEW-8 — snapshot tasks alive *before* the pilot so the post-pilot
    # shutdown only cancels orchestrator-spawned background tasks and never a
    # caller's TUI / parent coroutine.
    _pre_existing_tasks = asyncio.all_tasks()

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

    # E7 — prime BudgetGuard rolling windows from persisted per-project memory
    # so live tuning has historical signal from the very first story of the
    # new run instead of starting empty. Malformed memory files are tolerated
    # (logged, then run continues with empty windows) — a corrupt file must
    # not block a real-mode pilot launch.
    try:
        memory = load_project_memory(
            project, orchestrator_home=settings.orchestrator_home
        )
        budget.prime_from_memory(memory)
    except ProjectMemoryInvalidError as exc:
        log.warning("project_memory_load_failed", project=project, error=str(exc))

    if mock:
        await _run_mock_pilot(
            bus, wave=wave, max_parallel=max_parallel, budget=budget, settings=settings
        )
        await _shutdown_orchestrator(bus, pre_existing=_pre_existing_tasks)
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
        story_filter=stories,
        settings=settings,
    )
    await _shutdown_orchestrator(bus, pre_existing=_pre_existing_tasks)
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
    settings: Settings,
) -> None:
    """E2E pilot per spec §22: DAG → ready → spawn → JSONL → wave_boundary.

    Никаких сетевых вызовов. Использует ``runtime.worker_spawn(mock=True)`` —
    те же samples что и tests/test_s3_runtime.py mock pilot.

    ``settings`` — NEW-1-completion: resolved Settings прокинуты из
    ``run_orchestrator`` (registry-resolved ``--project``). НЕ перечитывать
    config заново — иначе ``ORCHESTRATOR_TARGET_PROJECT`` из env снова
    перебивает явный ``--project`` и worktrees уходят в чужой проект.
    """
    from bmad_orchestrator.agent.tools._common import (
        read_sprint_status_yaml,
        write_sprint_status_yaml,
    )

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
        completed_stories=len(spawned),
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
    settings: Settings,
    story_filter: tuple[str, ...] | None = None,
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
    # Review finding P1-E — L1 forbidden-path gate at single-project entry.
    # ``register_project`` enforces this at registry insertion, but a stale
    # registry from a prior version (or test fixture) can still hold a
    # poisoned entry; refuse here before any subprocess spawns.
    # NEW-1-completion: ``settings`` arrive resolved from ``run_orchestrator``
    # — никакого повторного чтения config здесь, иначе env перебивает
    # явный ``--project``.
    _validate_project_path(settings.target_project)

    # Review finding H-2 — sweep stale ``/tmp/bmad-worker-*`` snapshots from
    # prior crashes that left live OAuth tokens on disk. Best-effort; never
    # raises (cleanup helper returns a count, logs internally).
    from bmad_orchestrator.runtime.worker_spawn import (
        cleanup_stale_worker_homes,
    )

    cleanup_stale_worker_homes()

    # Phase 0 Task 0.1 — pre-cleanup of stale orchestrator processes from
    # prior failed runs. Excludes self + PPID to avoid suicide. See
    # spec_parallelism_initiatives §Phase 0 / Task 0.1.
    killed_stale = _kill_stale_orchestrators(project)
    if killed_stale:
        log.info("stale_orchestrators_killed", project=project, count=killed_stale)

    # W1.3 — sandbox guard. ``detect_sandbox()`` itself enforces
    # ``BMAD_REQUIRE_SANDBOX=1`` + NoSandbox → RuntimeError (FS9 H5). Calling at
    # entry surfaces the missing-bwrap failure BEFORE any DAG / spawn work.
    _ = detect_sandbox()

    # Initiative #2C — wire the production auto-split decomposer. Only when no
    # decomposer was pre-injected (tests inject stubs via ``set_decomposer`` and
    # must keep them). The auto-split path itself stays gated by
    # ``auto_split_enabled()`` (``BMAD_AUTO_SPLIT=1``) + ``evaluate_split``, so
    # installing the decomposer unconditionally here is inert until opted in.
    if get_decomposer() is None:
        from bmad_orchestrator.runtime.decomposer import claude_decomposer

        set_decomposer(claude_decomposer)

    spawned_handles: list[WorkerHandle] = []
    # Mutable carry — body updates as spend accrues so the finally block can
    # still emit a partial spend report when the body raises mid-pilot.
    # Without this the P1-A handshake is silent on BudgetHalt / SDK error /
    # network drop, and the multi-run parent's SharedSpendTracker stays at
    # zero for the slot.
    spend_carry: list[float] = [0.0]
    try:
        await _run_real_pilot_body(
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
            story_filter=story_filter,
            spawned_handles=spawned_handles,
            spend_carry=spend_carry,
            settings=settings,
        )
    finally:
        # Phase 0 Task 0.1 — kill child processes (claude -p, bwrap) if the
        # orchestrator crashes mid-pilot. Without this, zombie workers
        # accumulate and collide on next launch.
        _kill_orphan_workers(spawned_handles)
        # Review finding P1-A follow-up — flush spend even on body exception
        # so the multi-run parent's SharedSpendTracker sees the partial spend
        # that already occurred before the crash.
        _emit_spend_report(spend_carry[0])


def _detect_orphan_stories(story_ids: list[str]) -> list[str]:
    """Return story_ids that don't appear in target project's epics.md.

    Patch BB 2026-05-18: orphan-spike detection. A story is "orphan" when its
    standalone ``stories/<id>.md`` exists (so DAG sees it) but the heading
    ``#### Story <N.M>:`` is missing from ``epics.md`` (so the Gauntlet's
    text-extraction loses its primary source). The Gauntlet's Patch-AA
    fallback handles execution; this detector just surfaces the gap so the
    operator can backfill epics.md when convenient.

    Returns ``[]`` when epics.md is unreadable (degrades gracefully — the
    pre-flight check should never block a real run).
    """
    import re as _re

    from bmad_orchestrator.agent.tools._common import stories_dir

    epics_md = stories_dir().parent / "epics.md"
    try:
        text = epics_md.read_text(encoding="utf-8")
    except (OSError, FileNotFoundError):
        return []
    heading_re = _re.compile(
        r"^#{4,5}\s+Story\s+(\d+(?:\.\d+[a-z]?))\s*:", _re.MULTILINE
    )
    present_dotted = {m.group(1) for m in heading_re.finditer(text)}
    orphans: list[str] = []
    for sid in story_ids:
        m = _re.match(r"^(\d+)-(\d+[a-z]?)(?:-.*)?$", sid)
        dotted = f"{m.group(1)}.{m.group(2)}" if m else sid
        if dotted not in present_dotted and sid not in present_dotted:
            orphans.append(sid)
    return orphans


def _cmdline_matches_project(pid: int, project: str) -> bool:
    """Return True iff ``/proc/<pid>/cmdline`` has ``--project <project>`` exactly.

    Review finding H-3: ``pgrep -f "bmad-orchestrator run --project odyssey"``
    also matches ``odyssey-staging`` / ``odyssey-prod`` siblings (substring
    match). Multi-project registries (Init #3) make this a destructive misfire
    waiting to happen — staging cleanup SIGKILLs production.

    Resolves by reading the target proc's own argv (NUL-separated) and
    asserting one token equals ``--project`` followed by an exact-match next
    token equal to ``project``. Returns False on any read error (proc gone,
    permission denied) — better to miss a kill than kill the wrong sibling.
    """
    cmdline_path = Path(f"/proc/{pid}/cmdline")
    try:
        raw = cmdline_path.read_bytes()
    except OSError:
        return False
    args = [a.decode("utf-8", errors="replace") for a in raw.split(b"\x00") if a]
    for i, arg in enumerate(args):
        if arg == "--project" and i + 1 < len(args) and args[i + 1] == project:
            return True
        if arg.startswith("--project=") and arg.removeprefix("--project=") == project:
            return True
    return False


def _kill_stale_orchestrators(project: str) -> int:
    """Kill orchestrator processes for the same project, excluding self/PPID.

    Two-stage matching to avoid the substring hazard from review finding H-3:
    pgrep produces a candidate set with a loose ``bmad-orchestrator run``
    pattern (cheap pre-filter), then each pid's ``/proc/<pid>/cmdline`` is
    parsed and we kill only those whose argv has ``--project`` followed
    *exactly* by ``project`` (no ``odyssey`` matching ``odyssey-staging``).
    Returns number killed.
    """
    exclude_pids = {os.getpid(), os.getppid()}
    pgrep_bin = shutil.which("pgrep")
    if pgrep_bin is None:
        return 0
    try:
        # Loose pre-filter — exact-match decision lives in
        # ``_cmdline_matches_project`` so substring overlap can never reach
        # the SIGKILL below.
        proc = subprocess.run(  # noqa: S603
            [pgrep_bin, "-f", "bmad-orchestrator run"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 0
    killed = 0
    for line in proc.stdout.split():
        line = line.strip()
        if not line.isdigit():
            continue
        pid = int(line)
        if pid in exclude_pids:
            continue
        if not _cmdline_matches_project(pid, project):
            continue
        try:
            os.kill(pid, signal.SIGKILL)
            killed += 1
        except (ProcessLookupError, PermissionError):
            continue
    return killed


def _kill_orphan_workers(handles: list[WorkerHandle]) -> None:
    """SIGKILL any worker subprocess still alive when the pilot exits.

    Called from ``_run_real_pilot`` finally — covers the case where the
    orchestrator crashes between spawn and ``_tail_and_emit_completion``
    completion, leaving claude/bwrap children alive.
    """
    for handle in handles:
        proc = handle.process
        if proc is None or proc.returncode is not None:
            continue
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            continue
        log.warning(
            "orphan_worker_killed",
            story_id=handle.story_id,
            pid=handle.pid,
        )


_TRUTHY_ENV = {"1", "true", "yes", "on"}
_FALSY_ENV = {"0", "false", "no", "off"}


def _resolve_auto_clean_dirty_worktree(settings: Settings) -> bool:
    """NEW-5 — effective dirty-worktree policy.

    ``BMAD_AUTO_CLEAN_DIRTY_WORKTREE`` env var wins when set to a recognised
    truthy/falsy token; otherwise ``Settings.auto_clean_dirty_worktree`` (the
    programmatic default, True). An unrecognised env value is ignored (logged)
    so a typo never silently flips the destructive auto-clean path.
    """
    raw = os.environ.get("BMAD_AUTO_CLEAN_DIRTY_WORKTREE")
    if raw is None:
        return settings.auto_clean_dirty_worktree
    token = raw.strip().lower()
    if token in _TRUTHY_ENV:
        return True
    if token in _FALSY_ENV:
        return False
    log.warning(
        "bmad_auto_clean_dirty_worktree_invalid",
        value=raw,
        fallback=settings.auto_clean_dirty_worktree,
    )
    return settings.auto_clean_dirty_worktree


def _wire_pipeline_subscribers(
    bus: EventLoop, *, settings: Settings, wave: str
) -> None:
    """Wire the W4 gate context + full Phase-4 pipeline subscriber chain.

    Extracted from ``_run_real_pilot_body`` so the NEW-19 replay path
    (:func:`run_replay`) reuses the identical wiring. Registers, in
    canonical cheapest-first order:

      stage5 → build_check → deletion_safety → code_review → security_review
      → merge_to_integration → quarterly_sweep

    plus the elicitation / supervisor / self-learning / BMad-canonical
    subscribers. Subscribers take ``(event, bus)`` while ``EventLoop``
    dispatches with ``(event,)`` only, so ``partial`` binds the bus to satisfy
    the ``EventCallback`` contract.
    """
    # W4 — share the gate context with code_review / merge subscribers so they
    # know which target project + wave to merge into.
    configure_code_review_gate(
        target_project=settings.target_project,
        wave=wave,
        escalation_chat_id=getattr(getattr(settings, "bot", None), "escalation_chat_id", None),
    )

    # Final canonical-patches order (after P3 — stage5 → build → deletion →
    # code_review → merge → quarterly_sweep). The two halters (build_check,
    # deletion_safety) mutate ``payload['status']``; downstream gates
    # (code_review, merge_to_integration) short-circuit on non-success status.
    # Patch S: stage5_completeness runs FIRST (auto-stages Stage 5 residue).
    # Patch N: build_check runs second (cheap pytest+ruff guard before Opus).
    # Patch C: deletion_safety runs third (unsafe-deletion halt before review).
    # Patch Q: diff size gate embedded INSIDE code_review_subscriber.
    # Patch R: commit recovery embedded INSIDE merge_to_integration_subscriber.
    # Patch X: security_review sits AFTER code_review and BEFORE merge.
    bus.on(cast(EventCallback, partial(stage5_completeness_subscriber, bus=bus)))
    bus.on(cast(EventCallback, partial(build_check_subscriber, bus=bus)))
    bus.on(cast(EventCallback, partial(deletion_safety_subscriber, bus=bus)))
    bus.on(cast(EventCallback, partial(code_review_subscriber, bus=bus)))
    bus.on(
        cast(
            EventCallback,
            partial(
                security_review_subscriber,
                bus=bus,
                runner=_real_security_review_runner,
            ),
        )
    )
    bus.on(cast(EventCallback, partial(merge_to_integration_subscriber, bus=bus)))
    bus.on(cast(EventCallback, partial(quarterly_sweep_subscriber, bus=bus)))

    # Auto-elicitation engine (Phase 3 — P2 Routing). ``bus`` bound via a
    # positional closure (a keyword bind tripped mypy ``call-arg``).
    elicitation_policy_path = getattr(settings, "elicitation_policy_path", None)
    elicitation_engine = _load_elicitation_engine(elicitation_policy_path)
    _elicitation_sub = make_elicitation_subscriber(elicitation_engine)
    bus.on(lambda e: _elicitation_sub(e, bus))

    # Supervisor LLM-loop (Phase 4 #9 — P4 Orchestrator-Workers + P2 Routing).
    supervisor_policy_path = getattr(settings, "supervisor_policy_path", None)
    supervisor_engine = _load_supervisor_engine(supervisor_policy_path)
    _supervisor_sub = make_supervisor_subscriber(supervisor_engine)
    bus.on(lambda e: _supervisor_sub(e, bus))

    # Self-learning consolidation loop (Phase 5 — P5 Evaluator-Optimizer).
    self_learning_policy_path = getattr(settings, "self_learning_policy_path", None)
    sl_config = _load_self_learning_config(self_learning_policy_path)
    sl_consolidator = Consolidator(config=sl_config)
    _self_learning_sub = make_self_learning_subscriber(sl_consolidator)
    bus.on(lambda e: _self_learning_sub(e, bus))

    # BMad Phase 4 canonical-workflow subscribers (gap-closure 2026-05-19).
    from bmad_orchestrator.runtime.phase4_subscribers import (
        correct_course_subscriber,
        investigate_subscriber,
    )
    bus.on(cast(EventCallback, partial(correct_course_subscriber, bus=bus)))
    bus.on(cast(EventCallback, partial(investigate_subscriber, bus=bus)))


async def run_replay(
    *,
    worktree: Path,
    story_id: str,
    integration_branch: str,
    settings: Settings,
    auto_commit_dev: bool = False,
    bus: EventLoop | None = None,
    wire_subscribers: bool = True,
) -> dict[str, Any]:
    """NEW-19 — replay the post-dev pipeline tail against an existing worktree.

    Skips ``spawn_worker`` entirely (the ~30-min worker-dev phase). Takes a
    worktree that already carries a dev commit — or synthesizes one from a
    dirty worktree when ``auto_commit_dev`` is set — and drives it through the
    same chain a real pilot runs post-dev: stage5 → build-check → merge-gate →
    reconcile → merge. Used to validate merge-gate / stage5 / metrics fixes in
    seconds (spec_pilot_findings_closure_v6 §1).

    ``wire_subscribers=False`` lets tests pre-register their own (stub)
    subscribers on ``bus``; production callers leave it ``True`` so the real
    Phase-4 pipeline subscribers run.

    Returns a result dict: story_id, worktree, integration_branch, base_sha,
    dev_commits (list), synthesized (bool), reconciled (list), verdict (str |
    None — last CODE_REVIEW_VERDICT for the story), merge_skipped (bool),
    story_merged (bool — git-verified ancestry of the dev work in the
    integration branch), spawned_worker (always False).
    """
    from bmad_orchestrator.runtime.replay import (
        commit_is_merged,
        prepare_replay_worktree,
    )

    wave = integration_branch.removeprefix("integration/")
    bus = bus or EventLoop()

    rw = await prepare_replay_worktree(
        worktree=worktree,
        story_id=story_id,
        integration_branch=integration_branch,
        auto_commit_dev=auto_commit_dev,
    )

    if wire_subscribers:
        _wire_pipeline_subscribers(bus, settings=settings, wave=wave)

    log.info(
        "replay_mode_active",
        worktree=str(rw.path),
        story=story_id,
        integration_branch=integration_branch,
        dev_commits=len(rw.dev_commits),
        synthesized=rw.synthesized,
    )
    await bus.emit(
        EventType.REPLAY_MODE_STARTED,
        story_id=story_id,
        worktree=str(rw.path),
        integration_branch=integration_branch,
        base_sha=rw.base_sha,
        dev_commits=len(rw.dev_commits),
        synthesized=rw.synthesized,
    )

    # Drive the post-dev tail: emit WORKER_COMPLETED (success) so the wired
    # gate chain runs, drain it, then reconcile + drain — exactly the sequence
    # ``_run_real_pilot_body`` runs after its workers finish.
    await bus.emit(
        EventType.WORKER_COMPLETED,
        story_id=story_id,
        worktree=str(rw.path),
        jsonl="",
        exit_code=0,
        status="success",
        mock=False,
        replay=True,
    )
    dispatched = await bus.drain()

    handle = WorkerHandle(
        worktree=str(rw.path),
        story_id=story_id,
        branch=f"feature/{story_id}",
        pid=0,
        jsonl_path=Path(""),
        process=None,
        mock=False,
        sandbox_kind="n/a-replay",
        base_sha=rw.base_sha,
    )
    reconciled = await _reconcile_success_verdicts(
        bus, succeeded=[story_id], handles=[handle], dispatched=dispatched
    )
    post = await bus.drain()

    all_events = dispatched + post
    verdict: str | None = None
    merge_skipped = False
    for ev in all_events:
        ep = ev.payload or {}
        if str(ep.get("story_id") or "") != story_id:
            continue
        if ev.type == EventType.CODE_REVIEW_VERDICT:
            verdict = str(ep.get("verdict") or "") or verdict
        elif ev.type == EventType.INTEGRATION_MERGE_SKIPPED:
            merge_skipped = True

    # Ground truth — is the dev work an ancestor of the integration branch?
    story_merged = False
    if rw.dev_commits:
        story_merged = await commit_is_merged(
            settings.target_project, rw.dev_commits[-1], integration_branch
        )

    log.info(
        "replay_mode_done",
        story=story_id,
        verdict=verdict,
        story_merged=story_merged,
        merge_skipped=merge_skipped,
        reconciled=reconciled,
    )
    return {
        "story_id": story_id,
        "worktree": str(rw.path),
        "integration_branch": integration_branch,
        "base_sha": rw.base_sha,
        "dev_commits": list(rw.dev_commits),
        "synthesized": rw.synthesized,
        "reconciled": reconciled,
        "verdict": verdict,
        "merge_skipped": merge_skipped,
        "story_merged": story_merged,
        "spawned_worker": False,
    }


async def _run_real_pilot_body(
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
    story_filter: tuple[str, ...] | None,
    spawned_handles: list[WorkerHandle],
    spend_carry: list[float],
    settings: Settings,
) -> None:
    """Body of ``_run_real_pilot`` — wrapped in try/finally for orphan cleanup.

    ``spawned_handles`` accumulates every WorkerHandle spawned across all
    rounds so the finally block in the outer function can SIGKILL leftovers.
    ``spend_carry[0]`` is mutated after each story-budget projection so the
    caller's finally block can emit a partial spend report even on a
    body-mid-pilot exception (P1-A follow-up).

    ``settings`` — NEW-1-completion: resolved Settings прокинуты насквозь из
    ``run_orchestrator``. Повторное чтение config здесь игнорировало бы
    registry-resolved ``--project`` (env-override bug).
    """

    from bmad_orchestrator.agent.tools._common import (
        list_stories,
        read_sprint_status_yaml,
        write_sprint_status_yaml,
    )

    worktree_root = settings.target_project / ".worktrees"
    worktree_root.mkdir(parents=True, exist_ok=True)

    # W4 + P0-1 — wire the shared gate context + the full Phase-4 pipeline
    # subscriber chain onto the live bus. Extracted into a module-level helper
    # (_wire_pipeline_subscribers) so the NEW-19 replay path (run_replay)
    # reuses the exact same wiring instead of duplicating it
    # (spec_pilot_findings_closure_v6 §1).
    _wire_pipeline_subscribers(bus, settings=settings, wave=wave)

    planner = DagPlanner.from_target()
    spawned: list[str] = []
    # Spec #1 + #9 (pilot findings closure 2026-05-19) — track terminal outcome
    # per spawned story so mark-done only flips successful ones and
    # ``real_pilot_done`` can break out spawned vs succeeded vs failed counters.
    succeeded: list[str] = []
    failed: list[str] = []
    rounds = 0
    max_rounds = 6

    # Manual story-filter mode: caller specified exact story IDs via
    # ``run_orchestrator(stories=...)`` (typically from CLI ``--story <id>``).
    # Bypass the DAG planner because its dependency resolution may not match
    # the target project's BMad layout when ``story_filter`` is explicit.
    # Operator owns dependency correctness — intended for one-shot smoke
    # pilots on a single known-ready story.
    manual_stories: list[dict[str, Any]] = []
    if story_filter:
        all_stories = list_stories()
        by_id = {s["id"]: s for s in all_stories}
        missing = [sid for sid in story_filter if sid not in by_id]
        if missing:
            raise RuntimeError(
                f"--story {missing!r} not found in target project's stories dir "
                f"({len(all_stories)} stories present). Check the id spelling."
            )
        manual_stories = [by_id[sid] for sid in story_filter]
        log.info(
            "story_filter_active",
            count=len(manual_stories),
            ids=[s["id"] for s in manual_stories],
            dag_planner_bypassed=True,
        )
        # Patch BB 2026-05-18: orphan-story pre-flight warning. A story file may
        # exist on disk without a matching Epic-section breakdown in epics.md
        # (Phase-3.5 spikes are common offenders). The Gauntlet's Patch-AA
        # fallback handles this gracefully, but operator should know that the
        # canonical artifact chain is incomplete.
        orphan_ids = _detect_orphan_stories(list(story_filter))
        if orphan_ids:
            log.warning(
                "story_orphan_in_epics_md",
                story_ids=orphan_ids,
                note=(
                    "stories not found in epics.md — gauntlet will fall back "
                    "to standalone story files (Patch AA). Consider backfilling "
                    "epics.md for canonical BMad chain."
                ),
            )

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
    # W3 — adaptive per-story reservation. ``adaptive_story_reserve`` returns
    # ``story_alarm_usd / 2`` until the first worker reports a finalised cost,
    # then switches to ``min(story_alarm_usd, p95(last_3))``. Recomputed inside
    # the round loop so updates from completed workers flow into the next
    # ``enforce_and_reserve_story`` call.
    worker_model = models.dev

    bus.start_backstop_task()

    # NEW-5 — dirty reused worktree policy. ``BMAD_AUTO_CLEAN_DIRTY_WORKTREE``
    # env var (truthy/falsy) overrides ``Settings.auto_clean_dirty_worktree``;
    # absent → the Settings default (True = auto-clean residue before spawn).
    auto_clean_dirty_worktree = _resolve_auto_clean_dirty_worktree(settings)

    daily_halt_reached = False
    # Initiative pilot_findings_closure S6 (#6 P2) — one auto-disable state
    # per pilot run. evaluate_budget_disabled keeps the BUDGET_AUTO_DISABLED
    # emission idempotent across spawn rounds and across stories within a
    # round.
    budget_autodisable_state = BudgetAutoDisableState()
    while (
        rounds < max_rounds
        and not daily_halt_reached
        and len(spawned) < max_stories
        and daily_spent_usd < max_spend_usd
    ):
        if manual_stories:
            ready = [s for s in manual_stories if s["id"] not in spawned]
        else:
            ready = [
                s for s in planner.find_ready(max_n=max_parallel * 2)
                if s["id"] not in spawned
            ]
        if not ready:
            break

        remaining_slots = max_stories - len(spawned)
        effective_max = min(max_parallel, remaining_slots)
        # Initiative #1 Task 1.2 — file-conflict pre-check. ``planner.find_ready``
        # is round-based and not informed by ``in_flight_touches`` here, so two
        # ready stories that touch the same file would otherwise spawn in
        # parallel and race their writes. ``split_batch`` keeps the first owner
        # of each file in the parallel slot; later collisions land in
        # ``deferred`` and naturally re-appear in the next round (they are not
        # in ``spawned``, and their conflicting peer has finished by then).
        batch, deferred = split_batch(ready[:effective_max * 2], effective_max)
        if deferred:
            log.info(
                "file_conflict_split",
                parallel=[s.get("id") for s in batch],
                deferred=[s.get("id") for s in deferred],
                effective_max=effective_max,
            )
        if not batch:
            break

        handles: list[WorkerHandle] = []
        for story in batch:
            story_reserve_decimal = budget.adaptive_story_reserve()
            story_reserve = float(story_reserve_decimal)
            projected_daily = daily_spent_usd + story_reserve

            # Subscription-mode bypass: на Claude subscription нет per-token
            # billing'а — $ метрика фантомная. Initiative pilot_findings_closure
            # S6 (#6 P2) — теперь авто-детект: отсутствие ANTHROPIC_API_KEY
            # эквивалентно ручному BMAD_DISABLE_BUDGET=1, но с одноразовым
            # BUDGET_AUTO_DISABLED event для аудита. Manual flag не event'ится.
            _budget_disabled = await evaluate_budget_disabled(
                budget_autodisable_state, bus
            )

            # Local user-supplied hard cap (W1.2 --max-spend-usd).
            if not _budget_disabled and projected_daily > max_spend_usd:
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

            day_res = (
                await budget.enforce_day(projected_daily, today_utc)
                if not _budget_disabled
                else None
            )
            if day_res is not None and day_res.level == "halt":
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
            # P1-A follow-up — mirror running total into caller's mutable
            # carry so the outer finally still emits spend on body crash
            # (BudgetHalt, SDK error, network drop). Without this, the
            # multi-run parent's SharedSpendTracker stays at zero for the
            # slot even after real spend has occurred.
            spend_carry[0] = daily_spent_usd

            if not _budget_disabled:
                res = await budget.enforce_and_reserve_story(
                    story["id"], story_reserve_decimal
                )
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
            branch_name = f"feature/{story['id']}"
            base_sha = await _ensure_git_worktree(
                target_project=settings.target_project,
                worktree=wt,
                branch=branch_name,
            )
            # Initiative #2C — auto-split diversion. Opt-in via ``BMAD_AUTO_SPLIT=1``
            # AND a registered decomposer (``set_decomposer``). When both gates are
            # open and ``evaluate_split`` says ``split``, the parent story flows
            # through decomposer → sub-story executor → squash instead of the
            # legacy single-worker spawn. On success a synthetic
            # ``WORKER_COMPLETED`` event is emitted so downstream subscribers
            # (code-review → security-review → merge-to-integration) treat the
            # squashed parent commit as if a single worker had produced it.
            # On any failure the diversion falls through to the legacy spawn so
            # the story is not lost.
            auto_split_outcome: AutoSplitOutcome | None = None
            if auto_split_enabled() and _DECOMPOSER is not None:
                try:
                    auto_split_outcome = await auto_split_and_execute(
                        story=story,
                        worktree=wt,
                        branch=branch_name,
                        base_sha=base_sha,
                        decompose_fn=_DECOMPOSER,
                        bus=bus,
                        spawn_kwargs={
                            "mock": False,
                            "sandbox_network": "full",
                            "embedded_skills_root": settings.skills_resolution_root,
                            "allowed_worktree_root": worktree_root,
                        },
                    )
                except Exception as exc:
                    log.warning(
                        "auto_split_failed_falling_back",
                        story_id=story["id"],
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    auto_split_outcome = None
                    # Review finding P1-F — auto_split_and_execute may have
                    # already committed K of N sub-stories to ``branch_name``
                    # before raising. Without a hard reset to ``base_sha`` the
                    # legacy spawn below would land its commits on top of a
                    # partial sub-story history — frankencommit that the
                    # merge-gate may rubber-stamp. Reset to the original DAG
                    # batch base so the legacy worker sees a clean slate.
                    _reset_worktree_to_base(wt, base_sha)
                if auto_split_outcome is not None and auto_split_outcome.succeeded:
                    squashed_sha = (
                        auto_split_outcome.squash.squashed_sha
                        if auto_split_outcome.squash
                        else ""
                    )
                    await bus.emit(
                        EventType.WORKER_COMPLETED,
                        story_id=story["id"],
                        worktree=str(wt),
                        jsonl="",
                        exit_code=0,
                        status="success",
                        mock=False,
                        auto_split=True,
                        sub_ids=list(auto_split_outcome.sub_ids),
                        squashed_sha=squashed_sha,
                    )
                    spawned.append(story["id"])
                    # Auto-split squash already verified — count as success so
                    # mark-done flips this story to "done" (Spec #9).
                    succeeded.append(story["id"])
                    log.info(
                        "auto_split_completed",
                        story_id=story["id"],
                        sub_ids=list(auto_split_outcome.sub_ids),
                        squashed_sha=squashed_sha,
                    )
                    continue
            # Initiative #1 Task 1.3+1.4 — when running >1 worker in parallel,
            # opt in to per-worker HOME snapshot + cgroup scope so concurrent
            # ``claude -p`` processes don't race on shared ``~/.claude*`` state
            # and don't blow past the host's per-UID RLIMIT_NPROC.
            parallel_isolation = max_parallel > 1
            # NEW-5 — dirty reused worktree handling lives inside spawn_worker;
            # in safe mode (auto_clean_dirty_worktree=False) it raises
            # WorkerHaltPrespawnError instead of spawning. Catch it here so a
            # single dirty story halts cleanly rather than aborting the whole
            # pilot loop (the same guard also covers the #7 halt-reason gate).
            try:
                handle = await runtime_spawn_worker(
                    worktree=str(wt),
                    story_id=story["id"],
                    branch=branch_name,
                    mock=False,
                    sandbox_network="full",
                    embedded_skills_root=settings.skills_resolution_root,
                    allowed_worktree_root=worktree_root,
                    base_sha=base_sha,
                    isolated_home=parallel_isolation,
                    cgroup_limits=DEFAULT_CGROUP_LIMITS if parallel_isolation else None,
                    auto_clean_dirty_worktree=auto_clean_dirty_worktree,
                )
            except WorkerHaltPrespawnError as exc:
                log.warning(
                    "worker_spawn_halted_prespawn",
                    story_id=story["id"],
                    reason=exc.reason,
                )
                failed.append(story["id"])
                continue
            handles.append(handle)
            spawned_handles.append(handle)
            spawned.append(story["id"])

        if handles:
            # Spec #9 — bucket each round's worker outcomes so mark-done flips
            # only verified successes and the post-pilot counter splits
            # spawned/succeeded/failed.
            outcomes = await asyncio.gather(
                *[
                    _tail_and_emit_completion(
                        h, bus, budget=budget, model=worker_model
                    )
                    for h in handles
                ]
            )
            for h, outcome in zip(handles, outcomes, strict=True):
                if outcome == "completed":
                    succeeded.append(h.story_id)
                else:
                    failed.append(h.story_id)

        # NEW-3-completion — flip each succeeded story to "done" in
        # sprint-status. ``mark_sprint_status_done`` dispatches on layout:
        # legacy ``epics:`` nested AND upstream BMad flat
        # ``development_status:``. The previous inline loop only handled the
        # nested shape, so on real BMad projects (Antares 1a, flat layout)
        # every story fell through to ``pilot_mark_done_unresolved`` and
        # resume re-spawned already-done stories.
        snap = read_sprint_status_yaml()
        for sid in succeeded:
            resolved = mark_sprint_status_done(snap, sid)
            if resolved is not None:
                log.info(
                    "pilot_mark_done",
                    spawned_id=sid,
                    resolved_key=resolved,
                )
            else:
                log.warning(
                    "pilot_mark_done_unresolved",
                    spawned_id=sid,
                    reason="no matching sprint-status key in any epic block",
                )
        write_sprint_status_yaml(snap)
        planner.reload()
        rounds += 1

        # Per-batch budget aggregate — sum of realised story costs (W3) with a
        # fallback to the adaptive reserve when no real cost has landed yet.
        recent_costs = list(budget.recent_story_costs())
        batch_spent = (
            float(sum(recent_costs))
            if recent_costs
            else float(budget.adaptive_story_reserve()) * len(spawned)
        )
        await budget.enforce_batch(spent_usd=batch_spent, wave=wave)

    await bus.emit(
        EventType.WAVE_BOUNDARY_REACHED,
        wave=wave,
        spawned=spawned,
        succeeded=succeeded,
        failed=failed,
        rounds=rounds,
        completed_stories=len(succeeded),
    )

    # NEW-7 — verdict→integration pipeline. In real mode nothing else drains
    # the bus, so the WORKER_COMPLETED → CODE_REVIEW_VERDICT →
    # merge_to_integration subscriber chain never ran: workers committed to
    # feature/<id> but integration/<wave> was never created (validation-replay
    # finding NEW-7 — root cause variant (b): events were emitted onto the
    # queue, the bus never processed them before the pilot loop returned).
    #
    # Drain #1 processes the queued WORKER_COMPLETED / WAVE_BOUNDARY events
    # (and their cascades) through the registered subscribers. The post-worker
    # reconcile then enforces the spec invariant: any succeeded story with
    # commits past base_sha that the merge gate failed to verdict receives a
    # synthetic approve. Drain #2 processes those synthetic verdicts so the
    # ff-merge actually lands before ``real_pilot_done``.
    dispatched = await bus.drain()
    await _reconcile_success_verdicts(
        bus, succeeded=succeeded, handles=spawned_handles, dispatched=dispatched
    )
    await bus.drain()

    # P1-5 — persist a fresh project_memory snapshot so the next pilot of the
    # same project boots with primed BudgetGuard windows (E7 prime_from_memory)
    # instead of an empty deque. Failure must not abort the pilot — log and
    # continue; a missing snapshot only delays live-tuning convergence by a
    # few stories at the next launch.
    try:
        _persist_project_memory_snapshot(
            project=project,
            wave=wave,
            budget=budget,
            orchestrator_home=settings.orchestrator_home,
        )
    except ProjectMemoryError as exc:
        log.warning(
            "project_memory_save_failed",
            project=project,
            wave=wave,
            error=f"{type(exc).__name__}: {exc}",
        )

    log.info(
        "real_pilot_done",
        spawned=len(spawned),
        succeeded=len(succeeded),
        failed=len(failed),
        # Spec #9 — keep legacy `stories` key for backwards-compat with
        # downstream parsers (operators have grep'd the old line for months).
        stories=len(spawned),
        rounds=rounds,
        daily_spent_usd=daily_spent_usd,
        halted=daily_halt_reached,
    )

    # Review finding P1-A — feed total spend back to a multi-run parent (if
    # spawned via cli/main.py::_subprocess_runner) so the shared
    # ``SharedSpendTracker`` aggregate halts subsequent waves at the configured
    # cap. Single-project runs leave the env unset and skip this hop. Emit
    # also lives in ``_run_real_pilot``'s finally via ``spend_carry`` so that
    # a body-mid-pilot exception still flushes partial spend to the parent
    # (P1-A follow-up).
    spend_carry[0] = daily_spent_usd


def _emit_spend_report(spent_usd: float) -> None:
    """Write final cumulative spend to ``$BMAD_MULTI_SPEND_REPORT`` if set."""
    target = os.environ.get("BMAD_MULTI_SPEND_REPORT")
    if not target:
        return
    try:
        Path(target).write_text(
            json.dumps({"spent_usd": float(spent_usd)}),
            encoding="utf-8",
        )
    except OSError as exc:
        log.warning("spend_report_write_failed", path=target, error=str(exc))


def _persist_project_memory_snapshot(
    *,
    project: str,
    wave: str,
    budget: BudgetGuard,
    orchestrator_home: Path,
) -> Path:
    """P1-5 — atomically rewrite ``memory.yaml`` from BudgetGuard windows.

    Loads the existing memory file (or fresh defaults), refreshes:
      * ``last_wave`` to the just-completed wave id
      * the four ``recent_*`` rolling windows from BudgetGuard snapshots
      * matching medians (story cost, p0 coverage, test coverage)

    Other aggregates (success_rate, compliance_findings_count,
    lessons_files_count) are touched only by their owning subsystems and
    preserved as-is. Returns the on-disk path so callers / tests can
    inspect.
    """
    from statistics import median as _median

    memory = load_project_memory(project, orchestrator_home=orchestrator_home)
    story_costs = [float(c) for c in budget.recent_story_costs()]
    p0_window = list(budget.recent_p0_coverages())
    tc_window = list(budget.recent_test_coverages())
    it_window = list(budget.recent_review_iterations())

    fresh = memory.model_copy(
        update={
            "last_wave": wave,
            "recent_story_costs": story_costs,
            "recent_p0_coverages": p0_window,
            "recent_test_coverages": tc_window,
            "recent_review_iterations": it_window,
            "median_story_cost_usd": (
                float(_median(story_costs)) if story_costs else memory.median_story_cost_usd
            ),
            "median_review_p0": (
                float(_median(p0_window)) if p0_window else memory.median_review_p0
            ),
            "median_test_count": (
                float(_median(tc_window)) if tc_window else memory.median_test_count
            ),
        }
    )
    return save_project_memory(fresh, orchestrator_home=orchestrator_home)


def _reset_worktree_to_base(worktree: Path, base_sha: str) -> None:
    """Hard-reset ``worktree`` HEAD back to ``base_sha``.

    Used by review finding P1-F's auto-split fallback path: if
    ``auto_split_and_execute`` committed K of N sub-stories before raising,
    the branch is left with partial history. Resetting before the legacy
    spawn lands its commits ensures the merge-gate never sees a
    frankencommit of half-decomposition + legacy worker output.

    Best-effort: a failure here is logged (not raised) because the legacy
    fallback should still attempt — a poisoned branch is better surfaced as
    a review gate rejection than as a pilot abort.
    """
    if not base_sha:
        log.warning(
            "auto_split_fallback_no_base_sha",
            worktree=str(worktree),
        )
        return
    git_bin = shutil.which("git")
    if git_bin is None:
        log.warning(
            "auto_split_fallback_reset_no_git",
            worktree=str(worktree),
        )
        return
    try:
        result = subprocess.run(  # noqa: S603
            [git_bin, "-C", str(worktree), "reset", "--hard", base_sha],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if result.returncode != 0:
            log.warning(
                "auto_split_fallback_reset_failed",
                worktree=str(worktree),
                base_sha=base_sha,
                stderr=result.stderr[:400],
            )
        else:
            log.info(
                "auto_split_fallback_reset_ok",
                worktree=str(worktree),
                base_sha=base_sha,
            )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log.warning(
            "auto_split_fallback_reset_error",
            worktree=str(worktree),
            error=f"{type(exc).__name__}: {exc}",
        )


async def _ensure_git_worktree(
    *,
    target_project: Path,
    worktree: Path,
    branch: str,
) -> str:
    """Create (or reuse) a git worktree at ``worktree`` based on ``branch``.

    Returns the resolved HEAD SHA of the worktree branch (base reference
    for post-worker silent-failure detection — Phase 0 Task 0.2). On
    reuse, also runs the Phase 0 Task 0.4 freshness check (warns if the
    worktree has untracked / modified files left over from a prior run).

    First real-mode primitive — без него worker'ы спавнились в пустых
    папках и не имели доступа ни к story file, ни к source code,
    ни к BMad artifacts. Реализация:

    * Если ``worktree/.git`` уже есть → ничего не делаем (reuse).
    * Иначе: если ``branch`` существует в target → ``git worktree add wt branch``.
    * Иначе: ``git worktree add -b branch wt HEAD`` (новая ветка от HEAD).

    Ошибки логируются и поднимаются как RuntimeError — pilot должен
    halt'нуться явно, а не молча запускать worker в пустой папке.
    """
    if (worktree / ".git").exists():
        log.info(
            "git_worktree_reused",
            worktree=str(worktree),
            branch=branch,
        )
        await _warn_if_worktree_dirty(worktree)
        return await _resolve_worktree_head(worktree)

    # If a plain dir exists from a previous failed spawn — leave it; git
    # worktree add will fail on non-empty paths, which is the right loud
    # signal. Operator clears the dir manually.
    worktree.parent.mkdir(parents=True, exist_ok=True)

    # Check if branch already exists. ``git rev-parse`` exits 0 if yes.
    rev_proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(target_project), "rev-parse", "--verify", branch,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await rev_proc.wait()
    branch_exists = rev_proc.returncode == 0

    if branch_exists:
        args = ["git", "-C", str(target_project), "worktree", "add",
                str(worktree), branch]
    else:
        args = ["git", "-C", str(target_project), "worktree", "add",
                "-b", branch, str(worktree), "HEAD"]

    add_proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await add_proc.communicate()
    if add_proc.returncode != 0:
        msg = (
            f"git worktree add failed: rc={add_proc.returncode} "
            f"stderr={stderr.decode(errors='replace').strip()!r}"
        )
        log.error("git_worktree_add_failed", error=msg, branch=branch,
                  worktree=str(worktree))
        raise RuntimeError(msg)
    log.info(
        "git_worktree_created",
        worktree=str(worktree),
        branch=branch,
        new_branch=not branch_exists,
    )
    await _warn_if_worktree_dirty(worktree)
    return await _resolve_worktree_head(worktree)


async def _warn_if_worktree_dirty(worktree: Path) -> list[str]:
    """Phase 0 Task 0.4 — emit warn log if worktree has untracked/modified files.

    Pre-existing residue (e.g. leftover commits, stray files from a prior
    aborted spawn) can collide with the new worker's output. Returns the
    list of porcelain status lines for callers/tests; empty when clean.
    """
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(worktree), "status", "--porcelain",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return []
    raw = stdout.decode(errors="replace")
    lines = [line for line in raw.splitlines() if line.strip()]
    if lines:
        log.warning(
            "worktree_dirty_pre_spawn",
            worktree=str(worktree),
            entries=lines[:20],
            count=len(lines),
            note="pre-existing untracked/modified files may collide with story output",
        )
    return lines


async def _resolve_worktree_head(worktree: Path) -> str:
    """Return ``git -C <worktree> rev-parse HEAD`` or empty string on failure."""
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(worktree), "rev-parse", "HEAD",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return ""
    return stdout.decode(errors="replace").strip()


async def _count_new_commits(worktree: str, base_sha: str) -> int:
    """Count commits on the worker's branch since ``base_sha`` (Phase 0 Task 0.2)."""
    if not base_sha:
        return 0
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", worktree, "rev-list", "--count", f"{base_sha}..HEAD",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return 0
    try:
        return int(stdout.decode(errors="replace").strip())
    except ValueError:
        return 0


async def _reconcile_success_verdicts(
    bus: EventLoop,
    *,
    succeeded: list[str],
    handles: list[WorkerHandle],
    dispatched: list[Event],
) -> list[str]:
    """NEW-7 — emit a synthetic ``CODE_REVIEW_VERDICT`` for stranded successes.

    Invariant (spec_pilot_findings_closure_v3 §1 #1): any story with a
    ``worker_completed status=success`` AND ≥1 commit on ``feature/<id>``
    past ``base_sha`` MUST receive a ``CODE_REVIEW_VERDICT`` before the pilot
    loop ends — otherwise ``merge_to_integration_subscriber`` never fires and
    the work is silently stranded on the feature branch.

    ``dispatched`` is the event list from the post-worker :meth:`EventLoop.drain`
    — when the merge-gate ``code_review_subscriber`` already surfaced a verdict
    (any verdict, including ``reject``) the story is skipped: a real review
    decision must not be overridden by a synthetic approve. Reconcile only
    rescues the case where NO verdict surfaced at all (merge-gate worker error
    + runner-log fallback miss — root cause variant (a)/(b)).

    Returns the story_ids that received a synthetic verdict.
    """
    verdict_seen = {
        str((e.payload or {}).get("story_id") or "")
        for e in dispatched
        if e.type == EventType.CODE_REVIEW_VERDICT
    }
    handle_by_story = {h.story_id: h for h in handles}
    reconciled: list[str] = []
    for sid in succeeded:
        if sid in verdict_seen:
            continue
        handle = handle_by_story.get(sid)
        if handle is None or not handle.base_sha:
            # success but no WorkerHandle/base_sha to verify commits — can't
            # reconcile a verdict. Surface it instead of dropping silently.
            log.warning(
                "integration_merge_skipped",
                story_id=sid,
                reason="verdict_missing",
                note="no WorkerHandle/base_sha — cannot verify commits",
            )
            await bus.emit(
                EventType.INTEGRATION_MERGE_SKIPPED,
                story_id=sid,
                reason="verdict_missing",
                worktree=handle.worktree if handle is not None else "",
                commits=0,
            )
            continue
        commits = await _count_new_commits(handle.worktree, handle.base_sha)
        if commits <= 0:
            # success + zero commits — nothing to merge. Emit the NEW-7
            # observability event so a no-op success is visible, not silent.
            log.warning(
                "integration_merge_skipped",
                story_id=sid,
                reason="no_commits",
                worktree=handle.worktree,
            )
            await bus.emit(
                EventType.INTEGRATION_MERGE_SKIPPED,
                story_id=sid,
                reason="no_commits",
                worktree=handle.worktree,
                commits=0,
            )
            continue
        log.warning(
            "integration_reconcile_synthetic_verdict",
            story_id=sid,
            commits=commits,
            note="no merge-gate verdict surfaced — emitting synthetic approve",
        )
        await bus.emit(
            EventType.CODE_REVIEW_VERDICT,
            story_id=sid,
            verdict="approve",
            source="success_path_reconcile",
            commits=commits,
            summary=(
                f"post-worker reconcile: {commits} commit(s) on feature/{sid} "
                "past base_sha, no merge-gate verdict surfaced — synthetic "
                "approve to recover the work (NEW-7)"
            ),
            worktree=handle.worktree,
            review_iteration=1,
        )
        reconciled.append(sid)
    return reconciled


# #2 NEW-2 Layer B — how many trailing stdout lines to retain for the runner
# Stage 7 cleanup-failure scan. The refusal is logged once near the end of the
# run; ~50 lines is the spec target, 300 gives generous headroom for noisy
# trailing output without unbounded memory growth on long workers.
_STDOUT_TAIL_CAP = 300


async def _tail_and_emit_completion(
    handle: WorkerHandle,
    bus: EventLoop,
    *,
    budget: BudgetGuard | None = None,
    model: str | None = None,
) -> str:
    """Tail a worker's JSONL until terminal event; bridge to bus.

    Returns a terminal-outcome tag for callers that want to bucket workers by
    success/failure (pilot mark-done #1, real_pilot_done counter split #9):

    * ``"completed"`` — ``worker_completed`` with exit_code 0 AND new commits
      (or no base_sha to verify, e.g., mock paths).
    * ``"silent_failure"`` — ``worker_completed`` exit_code 0 but zero new
      commits on integration branch (caught as halt downstream).
    * ``"halted"`` — explicit ``worker_halt_file`` event from the worker.
    * ``"failed"`` — ``worker_completed`` with non-zero exit_code.

    The generator returns after seeing ``worker_completed`` or
    ``worker_halt_file``. We re-emit only the success / failure terminal to
    keep the bus surface narrow — intermediate ``claude_event`` / ``stdout_line``
    rows stay in the JSONL for forensics but don't fan out to subscribers.

    W3 — when ``budget`` and ``model`` are supplied, every event is fed through
    a :class:`WorkerCostTracker` so the running cost is attributed to the day
    cap via :meth:`BudgetGuard.attribute_usd` (scope ``worker:<story_id>``) and
    the realised total is pushed back into the adaptive reservation via
    :meth:`BudgetGuard.record_story_cost`. Tests that drive this helper with
    pure mock-mode handles can omit both and get the legacy bridge-only path.
    """
    # Phase 0 Task 0.3 — subscription mode: ANTHROPIC_API_KEY absent or
    # BMAD_DISABLE_BUDGET=1 means per-token billing is unavailable. We still
    # run the tracker so any usage data that does appear in JSONL events is
    # captured, but on terminal events with zero tokens we emit
    # ``cost_tracking_unavailable`` instead of the misleading
    # ``worker_cost_final total_usd=0.00``.
    subscription_mode = _is_subscription_mode()
    tracker: WorkerCostTracker | None = None
    if budget is not None and model:
        tracker = WorkerCostTracker(model=model)

    # #2 NEW-2 Layer B — keep a bounded tail of plain stdout lines so that, on
    # the terminal event, we can detect the runner's reused-worktree Stage 7
    # cleanup failure (``cannot delete branch ... used by worktree``).
    stdout_tail: list[str] = []

    async for ev in tail_jsonl_events(handle.jsonl_path):
        if tracker is not None and budget is not None:
            delta = tracker.feed(ev)
            if delta > 0:
                await budget.attribute_usd(
                    scope=f"worker:{handle.story_id}", spent=delta
                )
        event_type = ev.get("event_type")
        if event_type == "stdout_line":
            text = ev.get("text")
            if isinstance(text, str):
                stdout_tail.append(text)
                if len(stdout_tail) > _STDOUT_TAIL_CAP:
                    del stdout_tail[: len(stdout_tail) - _STDOUT_TAIL_CAP]
                # #10 NEW-10 — surface stage transitions so the orchestrator
                # log does not go silent for the full worker lifetime.
                stage = detect_stage_marker(text)
                if stage is not None:
                    log.info(
                        "worker_stage_progress",
                        story_id=handle.story_id,
                        worktree=handle.worktree,
                        stage=stage,
                    )
        if event_type == "worker_completed":
            # #10 NEW-10 — propagate worktree-internal worker events into the
            # orchestrator-side runs/ events.jsonl so the run is diagnosable
            # post-hoc (pilot run #3 left the main file with a stale mtime).
            try:
                merged = merge_worktree_events(
                    worktree_events_path(handle.worktree),
                    handle.jsonl_path,
                )
                if merged:
                    log.info(
                        "worker_events_merged",
                        story_id=handle.story_id,
                        worktree=handle.worktree,
                        events_merged=merged,
                    )
            except OSError as exc:
                log.warning(
                    "worker_events_merge_failed",
                    story_id=handle.story_id,
                    error=str(exc),
                )
            if tracker is not None and budget is not None:
                if subscription_mode and _tracker_has_no_usage(tracker):
                    await _emit_cost_tracking_unavailable(
                        bus, handle.story_id, reason="subscription_mode"
                    )
                else:
                    _emit_worker_cost_final(tracker, handle.story_id)
                    budget.record_story_cost(tracker.total_cost)
            # #2 NEW-2 Layer B — detect the runner's reused-worktree Stage 7
            # cleanup failure. When the feature branch still carries commits
            # past base_sha, recover the work via a synthetic approve verdict;
            # otherwise fall through to the silent-failure / halt path below.
            cleanup_branch = detect_reused_worktree_cleanup_failure(stdout_tail)
            if cleanup_branch is not None:
                cleanup_commits = (
                    await _count_new_commits(handle.worktree, handle.base_sha)
                    if handle.base_sha
                    else 0
                )
                decision = decide_cleanup_recovery(cleanup_commits)
                log.warning(
                    "runner_cleanup_failed_reused_worktree",
                    story_id=handle.story_id,
                    worktree=handle.worktree,
                    branch=cleanup_branch,
                    commits=decision.commits,
                    recover=decision.recover,
                )
                await bus.emit(
                    EventType.RUNNER_CLEANUP_FAILED_REUSED_WORKTREE,
                    story_id=handle.story_id,
                    worktree=handle.worktree,
                    branch=cleanup_branch,
                    commits=decision.commits,
                    jsonl=str(handle.jsonl_path),
                )
                if decision.recover:
                    review_iter = int(ev.get("review_iteration", 1) or 1)
                    await bus.emit(
                        EventType.CODE_REVIEW_VERDICT,
                        story_id=handle.story_id,
                        verdict="approve",
                        source="runner_cleanup_recovery",
                        commits=decision.commits,
                        summary=(
                            f"runner Stage 7 cleanup skipped (branch "
                            f"{cleanup_branch} held by a reused worktree); "
                            f"{decision.commits} commit(s) recovered past "
                            "base_sha"
                        ),
                        worktree=handle.worktree,
                        review_iteration=review_iter,
                    )
                    await bus.emit(
                        EventType.WORKER_COMPLETED,
                        story_id=handle.story_id,
                        worktree=handle.worktree,
                        jsonl=str(handle.jsonl_path),
                        exit_code=ev.get("exit_code", 0),
                        status="success",
                        mock=False,
                        review_iteration=review_iter,
                    )
                    return "completed"
                # no commits past base — preserve the existing halt behaviour.
            exit_code = ev.get("exit_code", 0)
            # #4 NEW-4 — outer/inner exit-code race: the outer ``claude -p``
            # can exit 0 while the inner ``bmad-auto-dev-runner.sh`` exited
            # non-zero (it echoes ``Exit code: N`` to stdout). The inner code
            # stays a SIGNAL, but #9 NEW-9 demotes it from sole decider —
            # ``decide_worker_status`` resolves the terminal status from the
            # Stage 6 verdict + commit count, with exit codes as fallback.
            inner_exit_code = parse_inner_exit_code(stdout_tail)
            inner_exit_failure = (
                exit_code == 0
                and inner_exit_code is not None
                and inner_exit_code != 0
            )
            # #9 NEW-9 — commit count + Stage 6 verdict for the verdict
            # source-of-truth decision. ``new_commits_count`` is also reused by
            # the silent-failure guard below.
            new_commits_count = 0
            if handle.base_sha:
                new_commits_count = await _count_new_commits(
                    handle.worktree, handle.base_sha
                )
            runner_verdict = read_runner_verdict(
                handle.worktree, handle.story_id
            )
            if (
                exit_code == 0
                and handle.base_sha
                and new_commits_count == 0
            ):
                log.warning(
                    "worker_silent_failure",
                    story_id=handle.story_id,
                    worktree=handle.worktree,
                    base_sha=handle.base_sha,
                    note="exit_code=0 but zero new commits — treating as halt",
                )
                await bus.emit(
                    EventType.WORKER_SILENT_FAILURE,
                    story_id=handle.story_id,
                    worktree=handle.worktree,
                    jsonl=str(handle.jsonl_path),
                    exit_code=exit_code,
                    base_sha=handle.base_sha,
                )
                await bus.emit(
                    EventType.WORKER_HALT_FILE,
                    story_id=handle.story_id,
                    worktree=handle.worktree,
                    jsonl=str(handle.jsonl_path),
                    reason="silent_failure_zero_commits",
                )
                return "silent_failure"
            status = decide_worker_status(
                verdict=runner_verdict,
                new_commits_count=new_commits_count,
                inner_exit=inner_exit_code,
                outer_exit=exit_code,
            )
            status_decided_by = (
                "verdict" if runner_verdict is not None else "exit_code_fallback"
            )
            completion_extra: dict[str, object] = {
                "verdict": runner_verdict,
                "new_commits_count": new_commits_count,
                "status_decided_by": status_decided_by,
            }
            if inner_exit_failure:
                completion_extra["inner_exit_code"] = inner_exit_code
                completion_extra["outer_exit_code"] = exit_code
                log.warning(
                    "worker_inner_exit_mismatch",
                    story_id=handle.story_id,
                    worktree=handle.worktree,
                    inner_exit_code=inner_exit_code,
                    outer_exit_code=exit_code,
                    final_status=status,
                    status_decided_by=status_decided_by,
                    note=(
                        "inner runner exit != 0 with outer exit 0; "
                        "status resolved by " + status_decided_by
                    ),
                )
            await bus.emit(
                EventType.WORKER_COMPLETED,
                story_id=handle.story_id,
                worktree=handle.worktree,
                jsonl=str(handle.jsonl_path),
                exit_code=exit_code,
                status=status,
                mock=False,
                review_iteration=int(ev.get("review_iteration", 1) or 1),
                **completion_extra,
            )
            return "completed" if status == "success" else "failed"
        if event_type == "worker_halt_file":
            if tracker is not None and budget is not None:
                if subscription_mode and _tracker_has_no_usage(tracker):
                    await _emit_cost_tracking_unavailable(
                        bus, handle.story_id, reason="subscription_mode"
                    )
                else:
                    _emit_worker_cost_final(tracker, handle.story_id)
                    budget.record_story_cost(tracker.total_cost)
            await bus.emit(
                EventType.WORKER_HALT_FILE,
                story_id=handle.story_id,
                worktree=handle.worktree,
                jsonl=str(handle.jsonl_path),
            )
            return "halted"
    return "halted"


def _emit_worker_cost_final(tracker: WorkerCostTracker, story_id: str) -> None:
    """Structured ``worker_cost_final`` log emitted on terminal event (W3)."""
    log.info(
        "worker_cost_final",
        story_id=story_id,
        total_usd=str(tracker.total_cost),
        cache_hit_ratio=round(tracker.cache_hit_ratio, 4),
        input_tokens=tracker.cumulative.input_tokens,
        cache_read_tokens=tracker.cumulative.cache_read_input_tokens,
        cache_write_tokens=tracker.cumulative.cache_creation_input_tokens,
        output_tokens=tracker.cumulative.output_tokens,
    )


def _tracker_has_no_usage(tracker: WorkerCostTracker) -> bool:
    """Return True when no usage block has landed yet (all token counts zero)."""
    cumulative = tracker.cumulative
    return (
        cumulative.input_tokens == 0
        and cumulative.output_tokens == 0
        and cumulative.cache_read_input_tokens == 0
        and cumulative.cache_creation_input_tokens == 0
    )


def _is_subscription_mode() -> bool:
    """Phase 0 Task 0.3 — detect Claude subscription (no per-token billing).

    Either ANTHROPIC_API_KEY is absent (subscription auth) or the explicit
    BMAD_DISABLE_BUDGET=1 escape hatch is set. Returns ``False`` when an
    API key is present and budget gates are enabled — the only mode where
    ``worker_cost_final`` numbers are honest.
    """
    if os.environ.get("BMAD_DISABLE_BUDGET") == "1":
        return True
    return not os.environ.get("ANTHROPIC_API_KEY")


async def _emit_cost_tracking_unavailable(
    bus: EventLoop, story_id: str, *, reason: str
) -> None:
    """Phase 0 Task 0.3 — emit ``cost_tracking_unavailable`` on terminal event.

    Replaces the misleading ``worker_cost_final total_usd=0.00`` log when
    cost tracking is unavailable (subscription mode). Emits both a
    structured log and a bus event so observers can distinguish "free run"
    from "genuine zero spend".
    """
    log.info(
        "cost_tracking_unavailable",
        story_id=story_id,
        reason=reason,
    )
    await bus.emit(
        EventType.COST_TRACKING_UNAVAILABLE,
        story_id=story_id,
        reason=reason,
    )


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
    except (SkillError, OSError) as exc:  # skill registry malformed → loud stub
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
    except (TimeoutError, ConnectionError, OSError) as exc:  # network/transport → stub
        log.error(
            "intent_router_transport_error",
            error_type=type(exc).__name__,
            error=str(exc)[:200],
            chat_id=chat_id,
            corr_id=corr_id,
        )
        await _stub_human_response(
            bus, chat_id=chat_id, corr_id=corr_id, text=text, reason="transport_error"
        )
        return
    except Exception as exc:  # anthropic.APIError + unexpected → stub
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
            except (RuntimeError, ValueError, TypeError, OSError, KeyError) as exc:  # tool failure → loud, but emit response
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

    # Subscription-mode supported by design: workers spawn via `claude -p`
    # CLI which uses Claude subscription auth. ANTHROPIC_API_KEY is required
    # ONLY for this intent-router (NL → action mapping via SDK). Without it,
    # bot degrades to slash-commands. See memory: feedback_no_anthropic_api.
    # TODO: restore SDK path when multi-LLM support (OpenAI / local) lands.
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


# ── CODE_REVIEW gate + auto-merge subscribers (W4) ───────────────────────────

CODE_REVIEW_SKILL_INVOCATION: str = "/bmad-code-review"
CODE_REVIEW_VERDICTS: frozenset[str] = frozenset({"approve", "request_changes", "reject"})
_VERDICT_LINE_RE = re.compile(
    r"verdict\s*[:=]\s*(approve|request_changes|reject)\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class CodeReviewGateConfig:
    """Caller context shared between the two W4 subscribers."""

    target_project: Path
    wave: str
    escalation_chat_id: int | None = None
    gates_override: CodeReviewGates | None = None
    # E6 — L2 live tuning. When both ``budget`` and ``gates_path`` are wired,
    # the review subscriber feeds each story's metrics into BudgetGuard's
    # rolling windows and persists tuned thresholds back to the YAML. None
    # disables live tuning (tests + unconfigured paths).
    budget: BudgetGuard | None = None
    gates_path: Path | None = None


_CODE_REVIEW_GATE: CodeReviewGateConfig | None = None


def configure_code_review_gate(
    *,
    target_project: Path | None,
    wave: str | None,
    escalation_chat_id: int | None = None,
    gates_override: CodeReviewGates | None = None,
    budget: BudgetGuard | None = None,
    gates_path: Path | None = None,
) -> None:
    """Wire the W4 gate to a target project + wave.

    Called once from ``_run_real_pilot`` so both subscribers share the live
    project root, wave name and escalation chat id. Passing ``None`` for either
    required field clears the gate (tests use this to assert the unconfigured
    branch).

    ``gates_override`` lets callers (mostly tests + future L2 live tuning)
    bypass the on-disk ``skills/policy/code-review-gates.yaml`` file. When
    ``None`` (the default), :func:`_load_review_gates` reads the YAML at
    subscriber call time.
    """
    global _CODE_REVIEW_GATE
    if target_project is None or wave is None:
        _CODE_REVIEW_GATE = None
        return
    _CODE_REVIEW_GATE = CodeReviewGateConfig(
        target_project=target_project,
        wave=wave,
        escalation_chat_id=escalation_chat_id,
        gates_override=gates_override,
        budget=budget,
        gates_path=gates_path,
    )


# ── E5 — 4 code-review gates ─────────────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class ReviewMetrics:
    """Aggregated review metrics extracted from the review JSONL stream.

    Populated by :func:`_extract_metrics_from_event` from `claude_event`
    payloads emitted by the ``/bmad-code-review`` skill. Accepts either an
    ``metrics: {...}`` sub-object OR top-level fields. Fields accumulate across
    events (last-write wins for ``expected_n_tests``).
    """

    p0_found: int = 0
    p0_fixed: int = 0
    compliance_tags: tuple[str, ...] = ()
    test_files_count: int = 0
    todo_placeholders: int = 0
    expected_n_tests: int = 0


def _extract_metrics_from_event(ev: dict[str, Any]) -> ReviewMetrics | None:
    """Return :class:`ReviewMetrics` if event carries metrics fields; else None.

    Reads either ``ev["metrics"]`` (preferred — keeps the review event tidy)
    or top-level fields on the event itself. Returns ``None`` when no metrics
    fields are present so the caller can distinguish «no metrics emitted» from
    «metrics all zero».
    """
    if not isinstance(ev, dict):
        return None
    src: dict[str, Any]
    metrics_obj = ev.get("metrics")
    if isinstance(metrics_obj, dict):
        src = metrics_obj
    else:
        # Fall back to top-level — accept if AT LEAST one known key is present.
        keys = {
            "p0_found",
            "p0_fixed",
            "compliance_tags",
            "test_files_count",
            "todo_placeholders",
            "expected_n_tests",
        }
        if not keys.intersection(ev.keys()):
            return None
        src = ev

    tags_raw = src.get("compliance_tags") or ()
    if isinstance(tags_raw, str):
        tags: tuple[str, ...] = (tags_raw,)
    else:
        try:
            tags = tuple(str(t) for t in tags_raw)
        except TypeError:
            tags = ()

    def _to_int(key: str) -> int:
        v = src.get(key, 0)
        try:
            return max(0, int(v))
        except (TypeError, ValueError):
            return 0

    return ReviewMetrics(
        p0_found=_to_int("p0_found"),
        p0_fixed=_to_int("p0_fixed"),
        compliance_tags=tags,
        test_files_count=_to_int("test_files_count"),
        todo_placeholders=_to_int("todo_placeholders"),
        expected_n_tests=_to_int("expected_n_tests"),
    )


def _merge_metrics(acc: ReviewMetrics | None, new: ReviewMetrics) -> ReviewMetrics:
    """Accumulate metrics across multiple review events.

    Counts add up; tags union; ``expected_n_tests`` takes the latest non-zero
    value (the spec'ed count is per-story, not per-event).
    """
    if acc is None:
        return new
    expected = new.expected_n_tests or acc.expected_n_tests
    merged_tags = tuple(sorted({*acc.compliance_tags, *new.compliance_tags}))
    return ReviewMetrics(
        p0_found=acc.p0_found + new.p0_found,
        p0_fixed=acc.p0_fixed + new.p0_fixed,
        compliance_tags=merged_tags,
        test_files_count=acc.test_files_count + new.test_files_count,
        todo_placeholders=acc.todo_placeholders + new.todo_placeholders,
        expected_n_tests=expected,
    )


def _gate_p0_threshold(metrics: ReviewMetrics, threshold: float) -> str | None:
    """P0-count gate. Returns reason string when tripped, ``None`` otherwise.

    ``fixed / found < threshold`` → trip. ``found == 0`` is a no-op (nothing to
    fix). ``threshold == 0`` is also a no-op (gate disabled).
    """
    if metrics.p0_found == 0 or threshold <= 0:
        return None
    coverage = metrics.p0_fixed / metrics.p0_found
    if coverage + 1e-9 < threshold:
        return (
            f"P0 auto-fix coverage {coverage:.0%} < {threshold:.0%} "
            f"({metrics.p0_fixed}/{metrics.p0_found} P0 findings fixed)"
        )
    return None


def _gate_compliance(
    metrics: ReviewMetrics, compliance_tags: list[str] | tuple[str, ...]
) -> str | None:
    """Compliance gate. Returns reason when any policy tag is in findings."""
    if not compliance_tags or not metrics.compliance_tags:
        return None
    policy = {t.strip() for t in compliance_tags if t.strip()}
    hits = sorted({t for t in metrics.compliance_tags if t in policy})
    if hits:
        return f"compliance tags require mandatory fix: {hits}"
    return None


def _gate_iteration_cap(review_iteration: int, cap: int) -> str | None:
    """P5 Evaluator-Optimizer hard cap gate.

    Returns a reason string when ``review_iteration > cap`` — trip the verdict
    into a human escalation regardless of other gate outcomes. ``cap <= 0``
    disables the check (treat as «no formal cap»). ``review_iteration <= 0``
    is also a no-op (worker didn't report — assume single-pass).
    """
    if cap <= 0 or review_iteration <= 0:
        return None
    if review_iteration > cap:
        return (
            f"review iteration {review_iteration} exceeds cap {cap} — "
            f"runaway loop protection; escalating for human review"
        )
    return None


def _gate_test_coverage(metrics: ReviewMetrics, threshold: float) -> str | None:
    """Test-coverage gate. Returns reason when ratio < threshold OR todo!() > 0.

    Disabled when ``expected_n_tests == 0`` (no spec'ed test count → cannot
    judge coverage). ``threshold == 0`` disables the ratio check but the
    ``todo!()`` placeholder check still trips on any non-zero count.
    """
    reasons: list[str] = []
    if metrics.expected_n_tests > 0 and threshold > 0:
        ratio = metrics.test_files_count / metrics.expected_n_tests
        if ratio + 1e-9 < threshold:
            reasons.append(
                f"test coverage {ratio:.0%} < {threshold:.0%} "
                f"({metrics.test_files_count}/{metrics.expected_n_tests} test files)"
            )
    if metrics.todo_placeholders > 0:
        reasons.append(
            f"{metrics.todo_placeholders} todo!() placeholder(s) in tests"
        )
    return "; ".join(reasons) if reasons else None


# Phase 4 hardening #3 — banned-phrase linter.
# Default set mirrors skills/policy/banned-phrases.yaml and is used when the
# YAML file is absent or unreadable (defence-in-depth: gate must not fail open).
BANNED_PHRASES: frozenset[str] = frozenset({
    "should work", "probably works", "seems to work", "appears to",
    "i think this", "this might", "great!", "done!", "perfect!",
    "all good", "everything works", "no issues", "looks good to me",
})


def _load_banned_phrases(override_path: Path | None = None) -> frozenset[str]:
    """Load banned phrases from YAML policy file.

    Resolution order:
      1. ``override_path`` — if provided and exists, load from there.
      2. ``settings.orchestrator_home / skills/policy/banned-phrases.yaml``.
      3. Fallback to hard-coded ``BANNED_PHRASES`` constant (YAML absent).

    The override EXTENDS the defaults (union), not replaces them.
    """
    import yaml as _yaml

    base = set(BANNED_PHRASES)

    def _read_yaml(path: Path) -> set[str]:
        try:
            raw = _yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            return set()
        if not isinstance(raw, dict):
            return set()
        phrases = raw.get("phrases", [])
        if not isinstance(phrases, list):
            return set()
        return {str(p).lower() for p in phrases if p}

    # Try the default policy file (sibling of other policy YAMLs).
    try:
        settings = load_settings()
        default_path = settings.orchestrator_home / "skills" / "policy" / "banned-phrases.yaml"
    except Exception:
        default_path = None

    if default_path is not None and default_path.exists():
        base = _read_yaml(default_path)
        if not base:
            base = set(BANNED_PHRASES)

    if override_path is not None and override_path.exists():
        extra = _read_yaml(override_path)
        base = base | extra

    return frozenset(base)


def _gate_banned_phrases(worker_summary: str, override_path: Path | None = None) -> str | None:
    """Banned-phrase gate. Returns reason string when tripped, ``None`` otherwise.

    Trips when ``worker_summary`` contains any phrase from the active
    banned-phrase list (case-insensitive substring match). Designed to block
    premature-completion claims that lack concrete evidence.

    Args:
        worker_summary: Text emitted by the worker as its completion summary.
        override_path: Optional path to a project-specific YAML that EXTENDS
            the default phrase list. Mirrors Settings.banned_phrases_path.

    Returns:
        A reason string of the form
        ``banned_phrases_in_completion:<phrase1>,<phrase2>,...`` (up to 3
        phrases shown) when the gate trips, or ``None`` when clean.
    """
    phrases = _load_banned_phrases(override_path)
    lower = worker_summary.lower()
    hits = sorted(p for p in phrases if p in lower)
    if hits:
        return f"banned_phrases_in_completion:{','.join(hits[:3])}"
    return None


def _load_review_gates(cfg: CodeReviewGateConfig | None) -> CodeReviewGates:
    """Resolve the active gate config: override → policy YAML → built-in defaults."""
    if cfg is not None and cfg.gates_override is not None:
        return cfg.gates_override
    try:
        return load_policy().code_review_gates
    except (PolicyNotFoundError, PolicyInvalidError) as exc:
        log.warning("code_review_gates_fallback_defaults", error=str(exc))
        return CodeReviewGates.model_validate({})


async def _apply_live_tuning(
    *,
    bus: EventLoop,
    cfg: CodeReviewGateConfig,
    gates: CodeReviewGates,
    metrics: ReviewMetrics,
    story_id: str,
    review_iteration: int = 1,
) -> None:
    """Feed per-story samples into ``budget`` and persist tuned thresholds.

    Pre-conditions checked by the caller: ``cfg.budget is not None``.

    Workflow:

    1. ``budget.record_review_metrics`` pushes coverage samples into the
       rolling windows (skips metrics with no signal — zero p0_found, zero
       expected_n_tests, etc.).
    2. :func:`evaluate_threshold` computes a proposal per tunable metric from
       the current window; returns ``None`` when the window holds fewer than
       ``MIN_SAMPLES_FOR_TUNING`` samples.
    3. :func:`apply_proposals` partitions proposals into «safe to write» vs
       «out-of-bounds» (movement > 50% of max(current, proposed)).
    4. Safe proposals are written via :func:`atomic_write_gates_yaml` when
       ``cfg.gates_path`` is configured; out-of-bounds proposals emit
       ``HUMAN_QUERY`` so the operator approves a large drift explicitly.
    """
    budget = cfg.budget
    if budget is None:  # pragma: no cover — gated by caller, kept for mypy
        return

    budget.record_review_metrics(
        p0_found=metrics.p0_found,
        p0_fixed=metrics.p0_fixed,
        test_files_count=metrics.test_files_count,
        expected_n_tests=metrics.expected_n_tests,
        iterations=max(1, review_iteration),
    )

    proposals: list[TuningProposal] = []
    p0_prop = evaluate_threshold(
        metric="p0_threshold",
        samples=budget.recent_p0_coverages(),
        current_value=gates.p0_threshold,
    )
    if p0_prop is not None:
        proposals.append(p0_prop)
    tc_prop = evaluate_threshold(
        metric="test_coverage_threshold",
        samples=budget.recent_test_coverages(),
        current_value=gates.test_coverage_threshold,
    )
    if tc_prop is not None:
        proposals.append(tc_prop)

    if not proposals:
        return

    # P1-7 — split off proposals that *tighten* the gate (proposed > current).
    # Both p0_threshold and test_coverage_threshold are "minimum required" —
    # raising them makes the gate stricter, which can silently block stories
    # that would have passed yesterday. Escalate every tightening through
    # HUMAN_QUERY regardless of bounds; only loosening / no-op moves are
    # eligible for silent apply.
    tighten_props = tuple(
        p for p in proposals if p.proposed_value > p.current_value
    )
    safe_props = tuple(
        p for p in proposals if p.proposed_value <= p.current_value
    )

    new_gates, bound_escalations = apply_proposals(gates, safe_props)
    changed_metrics = tuple(
        prop.metric for prop in safe_props if prop.within_bounds
    )

    if changed_metrics and cfg.gates_path is not None:
        try:
            atomic_write_gates_yaml(new_gates, cfg.gates_path)
        except OSError as exc:
            log.warning(
                "live_tuning_write_failed",
                story_id=story_id,
                path=str(cfg.gates_path),
                error=f"{type(exc).__name__}: {exc}",
            )
        else:
            log.info(
                "live_tuning_applied",
                story_id=story_id,
                metrics=list(changed_metrics),
                new_p0_threshold=new_gates.p0_threshold,
                new_test_coverage_threshold=new_gates.test_coverage_threshold,
            )

    for prop in bound_escalations:
        log.info(
            "live_tuning_bounds_exceeded",
            story_id=story_id,
            metric=prop.metric,
            current=prop.current_value,
            proposed=prop.proposed_value,
            iqr=prop.iqr,
            max_movement_fraction=prop.max_movement_fraction,
        )
        await bus.emit(
            EventType.HUMAN_QUERY,
            chat_id=cfg.escalation_chat_id,
            text=(
                f"Live tuning bounds breach для {prop.metric} (story {story_id}):\n\n"
                f"Current: {prop.current_value:.3f}\n"
                f"Proposed: {prop.proposed_value:.3f} "
                f"(median of last {len(prop.samples)} coverage samples)\n"
                f"Movement {abs(prop.proposed_value - prop.current_value):.3f} "
                f"exceeds {prop.max_movement_fraction:.0%} bound.\n"
                f"Approve update or keep current threshold?"
            ),
            story_id=story_id,
            verdict="live_tuning_bounds",
            metric=prop.metric,
            current_value=prop.current_value,
            proposed_value=prop.proposed_value,
            iqr=prop.iqr,
            actions=["approve_update", "keep_current"],
        )

    # P1-7 — tighten escalations get a dedicated verdict and a message that
    # explicitly flags «strictening» so the operator can distinguish from a
    # bounds-only breach (which may be a loosening that overshot).
    for prop in tighten_props:
        log.info(
            "live_tuning_tighten_escalated",
            story_id=story_id,
            metric=prop.metric,
            current=prop.current_value,
            proposed=prop.proposed_value,
            iqr=prop.iqr,
        )
        await bus.emit(
            EventType.HUMAN_QUERY,
            chat_id=cfg.escalation_chat_id,
            text=(
                f"Live tuning хочет ужесточить {prop.metric} "
                f"(story {story_id}):\n\n"
                f"Current: {prop.current_value:.3f}\n"
                f"Proposed: {prop.proposed_value:.3f} "
                f"(median of last {len(prop.samples)} coverage samples)\n"
                f"Tightening раньше предыдущего значения может silently отклонить "
                f"stories, которые проходили вчера — apply требует ручного "
                f"подтверждения.\n"
                f"Approve update or keep current threshold?"
            ),
            story_id=story_id,
            verdict="live_tuning_tighten",
            metric=prop.metric,
            current_value=prop.current_value,
            proposed_value=prop.proposed_value,
            iqr=prop.iqr,
            actions=["approve_update", "keep_current"],
        )


def _verdict_from_text(text: str) -> str | None:
    """Return ``approve|request_changes|reject`` if a verdict line is present."""
    if not text:
        return None
    m = _VERDICT_LINE_RE.search(text)
    if m is None:
        return None
    return m.group(1).lower()


def _extract_verdict_from_event(ev: dict[str, Any]) -> tuple[str, str] | None:
    """Return ``(verdict, summary)`` if event carries verdict info; else None.

    Supports two shapes emitted by the ``/bmad-code-review`` skill:
      * Explicit JSON key: ``{"verdict": "approve", "summary": "…"}``.
      * Text body (``text`` / ``summary`` field) matching
        ``verdict: <approve|request_changes|reject>``.
    """
    if not isinstance(ev, dict):
        return None
    explicit = ev.get("verdict")
    if isinstance(explicit, str) and explicit.lower() in CODE_REVIEW_VERDICTS:
        return explicit.lower(), str(ev.get("summary") or ev.get("text") or "")
    for key in ("summary", "text", "content"):
        val = ev.get(key)
        if isinstance(val, str):
            v = _verdict_from_text(val)
            if v is not None:
                return v, val
    return None


async def _spawn_code_review_worker(
    *,
    worktree: str,
    story_id: str,
    wave: str,
) -> WorkerHandle:
    """Spawn ``claude -p /bmad-code-review`` in ``worktree`` with a distinct JSONL.

    The JSONL path is derived from ``BMAD_CURRENT_WAVE`` inside
    :func:`worker_jsonl_path`. We pivot the env var to a wave-scoped review
    namespace so the review stream lands at
    ``runs_dir / <wave>__review_<story_id> / <basename>.events.jsonl`` instead
    of clobbering (and being clobbered by) the dev worker's terminal event.
    """
    original_wave = os.environ.get("BMAD_CURRENT_WAVE")
    os.environ["BMAD_CURRENT_WAVE"] = f"{wave}__review_{story_id}"
    try:
        handle = await runtime_spawn_worker(
            worktree=worktree,
            story_id=story_id,
            branch=f"feature/{story_id}",
            skill_invocation=CODE_REVIEW_SKILL_INVOCATION,
            sandbox_network="none",
        )
    finally:
        if original_wave is None:
            os.environ.pop("BMAD_CURRENT_WAVE", None)
        else:
            os.environ["BMAD_CURRENT_WAVE"] = original_wave
    return handle


async def _spawn_security_review_worker(
    *,
    worktree: str,
    story_id: str,
    wave: str,
) -> WorkerHandle:
    """Spawn ``claude -p /bmad-security-review --auto`` in ``worktree``.

    Same JSONL-namespace trick as :func:`_spawn_code_review_worker` — the
    ``BMAD_CURRENT_WAVE`` env var is pivoted to a security-scoped namespace so
    the hunter stream lands at
    ``runs_dir / <wave>__security_<story_id> / <basename>.events.jsonl`` without
    clobbering the dev worker's or the code-review's JSONL.
    """
    original_wave = os.environ.get("BMAD_CURRENT_WAVE")
    os.environ["BMAD_CURRENT_WAVE"] = f"{wave}__security_{story_id}"
    try:
        handle = await runtime_spawn_worker(
            worktree=worktree,
            story_id=story_id,
            branch=f"feature/{story_id}",
            skill_invocation=SECURITY_REVIEW_SKILL_INVOCATION,
            sandbox_network="none",
        )
    finally:
        if original_wave is None:
            os.environ.pop("BMAD_CURRENT_WAVE", None)
        else:
            os.environ["BMAD_CURRENT_WAVE"] = original_wave
    return handle


async def _real_security_review_runner(
    worktree: Path, story_id: str, wave: str
) -> tuple[str, str]:
    """Production runner — spawn the security-review worker, aggregate verdict."""
    handle = await _spawn_security_review_worker(
        worktree=str(worktree), story_id=story_id, wave=wave
    )
    verdict = SECURITY_VERDICT_ERROR
    findings = ""
    async for ev in tail_jsonl_events(handle.jsonl_path):
        extracted = parse_security_verdict_from_event(ev)
        if extracted is not None:
            verdict, findings = extracted
    return verdict, findings


# ── Phase 4 hardening #5 — Two-stage merge-gate split ───────────────────────

MERGE_GATE_SPEC_SKILL: str = "/bmad-code-review"
MERGE_GATE_QUALITY_SKILL: str = "/bmad-code-review"


def _merge_verdicts(spec_verdict: str, quality_verdict: str) -> str:
    """Merge two stage verdicts using worst-wins rule.

    Rules:
      approve + approve        → approve
      approve + request_changes → request_changes
      request_changes + anything → request_changes
      error + anything          → error (unless other is request_changes)

    The priority order is: error > request_changes > reject > approve.
    """
    order = {"approve": 0, "reject": 1, "request_changes": 2, "error": 3}
    spec_rank = order.get(spec_verdict, 2)
    quality_rank = order.get(quality_verdict, 2)
    if spec_rank >= quality_rank:
        return spec_verdict
    return quality_verdict


async def _spawn_merge_gate_spec_worker(
    *,
    worktree: str,
    story_id: str,
    wave: str,
) -> WorkerHandle:
    """Spawn spec-stage review worker (AC coverage + story completeness)."""
    original_wave = os.environ.get("BMAD_CURRENT_WAVE")
    os.environ["BMAD_CURRENT_WAVE"] = f"{wave}__gate_spec_{story_id}"
    try:
        handle = await runtime_spawn_worker(
            worktree=worktree,
            story_id=story_id,
            branch=f"feature/{story_id}",
            skill_invocation=MERGE_GATE_SPEC_SKILL,
            sandbox_network="none",
        )
    finally:
        if original_wave is None:
            os.environ.pop("BMAD_CURRENT_WAVE", None)
        else:
            os.environ["BMAD_CURRENT_WAVE"] = original_wave
    return handle


async def _spawn_merge_gate_quality_worker(
    *,
    worktree: str,
    story_id: str,
    wave: str,
) -> WorkerHandle:
    """Spawn quality-stage review worker (lints, tests, security, perf)."""
    original_wave = os.environ.get("BMAD_CURRENT_WAVE")
    os.environ["BMAD_CURRENT_WAVE"] = f"{wave}__gate_quality_{story_id}"
    try:
        handle = await runtime_spawn_worker(
            worktree=worktree,
            story_id=story_id,
            branch=f"feature/{story_id}",
            skill_invocation=MERGE_GATE_QUALITY_SKILL,
            sandbox_network="none",
        )
    finally:
        if original_wave is None:
            os.environ.pop("BMAD_CURRENT_WAVE", None)
        else:
            os.environ["BMAD_CURRENT_WAVE"] = original_wave
    return handle


async def _run_merge_gate_spec_stage(
    *,
    worktree: str,
    story_id: str,
    wave: str,
    bus: EventLoop,
) -> tuple[str, str, ReviewMetrics | None]:
    """Run Stage 1 (spec) of two-stage merge gate.

    Returns ``(verdict, summary, metrics)``.
    On spawn failure returns ``("error", reason, None)`` and does NOT emit — caller
    emits the final CODE_REVIEW_VERDICT.
    """
    try:
        handle = await _spawn_merge_gate_spec_worker(
            worktree=worktree, story_id=story_id, wave=wave
        )
    except (OSError, RuntimeError) as exc:
        log.exception(
            "merge_gate_spec_spawn_failed", story_id=story_id, worktree=worktree
        )
        return "error", f"spec stage spawn failed: {type(exc).__name__}: {exc}", None

    verdict = "error"
    summary = ""
    metrics: ReviewMetrics | None = None
    async for ev in tail_jsonl_events(handle.jsonl_path):
        extracted = _extract_verdict_from_event(ev)
        if extracted is not None:
            verdict, summary = extracted
        new_metrics = _extract_metrics_from_event(ev)
        if new_metrics is not None:
            metrics = _merge_metrics(metrics, new_metrics)

    await bus.emit(
        EventType.MERGE_GATE_STAGE_COMPLETED,
        stage="spec",
        story_id=story_id,
        verdict=verdict,
        findings_count=metrics.p0_found if metrics is not None else 0,
    )
    log.info(
        "merge_gate_spec_stage_done",
        story_id=story_id,
        verdict=verdict,
    )
    return verdict, summary, metrics


async def _run_merge_gate_quality_stage(
    *,
    worktree: str,
    story_id: str,
    wave: str,
    bus: EventLoop,
) -> tuple[str, str, ReviewMetrics | None]:
    """Run Stage 2 (quality) of two-stage merge gate.

    Returns ``(verdict, summary, metrics)``.
    Called ONLY when spec stage verdict == "approve".
    """
    try:
        handle = await _spawn_merge_gate_quality_worker(
            worktree=worktree, story_id=story_id, wave=wave
        )
    except (OSError, RuntimeError) as exc:
        log.exception(
            "merge_gate_quality_spawn_failed", story_id=story_id, worktree=worktree
        )
        return "error", f"quality stage spawn failed: {type(exc).__name__}: {exc}", None

    verdict = "error"
    summary = ""
    metrics: ReviewMetrics | None = None
    async for ev in tail_jsonl_events(handle.jsonl_path):
        extracted = _extract_verdict_from_event(ev)
        if extracted is not None:
            verdict, summary = extracted
        new_metrics = _extract_metrics_from_event(ev)
        if new_metrics is not None:
            metrics = _merge_metrics(metrics, new_metrics)

    await bus.emit(
        EventType.MERGE_GATE_STAGE_COMPLETED,
        stage="quality",
        story_id=story_id,
        verdict=verdict,
        findings_count=metrics.p0_found if metrics is not None else 0,
    )
    log.info(
        "merge_gate_quality_stage_done",
        story_id=story_id,
        verdict=verdict,
    )
    return verdict, summary, metrics


async def code_review_subscriber(event: Event, bus: EventLoop) -> None:
    """On ``WORKER_COMPLETED(success)`` → run two-stage merge gate, emit verdict.

    Phase 4 hardening #5 — two-stage split:
      Stage 1 (spec): AC coverage + story completeness.
      Stage 2 (quality): code quality (lints, tests, security, perf).
    Quality stage runs ONLY when spec stage approves (saves cost).
    Final verdict = worst-wins merge of both stages.

    Filtering: anything other than ``WORKER_COMPLETED`` with
    ``payload['status'] == 'success'`` is a no-op (failures bypass review and
    flow straight to W5 escalation). The verdict is extracted from the LAST
    matching ``claude_event`` block in the review JSONL — supports both an
    explicit ``verdict`` JSON key and a ``verdict: <X>`` text pattern.
    """
    if event.type != EventType.WORKER_COMPLETED:
        return
    payload = event.payload or {}
    if payload.get("status") != "success":
        return

    story_id = str(payload.get("story_id") or "")
    worktree = str(payload.get("worktree") or "")
    if not story_id or not worktree:
        log.warning("code_review_skip_missing_fields", payload=payload)
        return

    # P5 Evaluator-Optimizer — worker reports how many review→fix rounds
    # bmad-auto-dev cycled through (env var ORCHESTRATOR_WORKER_REVIEW_ITERATION,
    # surfaced into the WORKER_COMPLETED payload). Defaults to 1 (single-pass).
    try:
        review_iteration = max(1, int(payload.get("review_iteration", 1) or 1))
    except (TypeError, ValueError):
        review_iteration = 1

    cfg = _CODE_REVIEW_GATE
    wave = (cfg.wave if cfg is not None else None) or os.environ.get(
        "BMAD_CURRENT_WAVE", "default"
    )

    # ── Phase 4 hardening #5 — Stage 1: spec (AC coverage + story completeness).
    spec_verdict, spec_summary, spec_metrics = await _run_merge_gate_spec_stage(
        worktree=worktree, story_id=story_id, wave=wave, bus=bus
    )

    # ── S2 (spec_pilot_findings_closure §1 #2) — runner-log fallback.
    # When the merge-gate spec worker fails to surface a parseable verdict
    # event (JSONL stream had no `verdict: ...` token), fall back to reading
    # the bmad-auto-dev runner's own Stage 6 review log from the worktree.
    # The runner records PASS / NEEDS-FIX / BLOCKED there — re-use that signal
    # instead of escalating every event-stream miss to a human.
    verdict_source = "merge_gate_spec"
    if spec_verdict == "error":
        fallback = parse_runner_review_log(worktree, story_id)
        if fallback is not None:
            fb_verdict, fb_summary = fallback
            log.info(
                "code_review_runner_log_fallback",
                story_id=story_id,
                worktree=worktree,
                fallback_verdict=fb_verdict,
            )
            spec_verdict = fb_verdict
            spec_summary = fb_summary
            verdict_source = "runner_log_fallback"

    if spec_verdict != "approve":
        # Spec stage failed — skip quality stage entirely (saves cost).
        log.info(
            "merge_gate_quality_stage_skipped",
            story_id=story_id,
            spec_verdict=spec_verdict,
        )
        await bus.emit(
            EventType.CODE_REVIEW_VERDICT,
            story_id=story_id,
            verdict=spec_verdict,
            summary=spec_summary,
            worktree=worktree,
            review_iteration=review_iteration,
            gate_stage="spec",
            source=verdict_source,
        )
        if spec_metrics is not None and cfg is not None and cfg.budget is not None:
            gates_for_tuning = _load_review_gates(cfg)
            await _apply_live_tuning(
                bus=bus,
                cfg=cfg,
                gates=gates_for_tuning,
                metrics=spec_metrics,
                story_id=story_id,
                review_iteration=review_iteration,
            )
        return

    # ── Stage 2: quality (lints, tests, security, perf).
    quality_verdict, quality_summary, quality_metrics = await _run_merge_gate_quality_stage(
        worktree=worktree, story_id=story_id, wave=wave, bus=bus
    )

    # Merge verdicts: worst wins.
    verdict = _merge_verdicts(spec_verdict, quality_verdict)
    summary = quality_summary if quality_summary else spec_summary
    metrics: ReviewMetrics | None = quality_metrics if quality_metrics is not None else spec_metrics

    # Dummy handle reference for review_jsonl field in emit (quality stage is last).
    # Use spec_summary for the combined summary if quality is empty.
    handle_jsonl_str = ""

    gates = _load_review_gates(cfg)

    # ── Compliance gate — fires regardless of verdict; escalates HUMAN_QUERY
    #    directly (defer запрещён, no merge_to_integration approve path).
    if metrics is not None:
        compliance_hit = _gate_compliance(metrics, gates.compliance_tags)
        if compliance_hit is not None:
            log.info(
                "code_review_compliance_gate_tripped",
                story_id=story_id,
                tags=list(metrics.compliance_tags),
                policy_tags=list(gates.compliance_tags),
            )
            await bus.emit(
                EventType.HUMAN_QUERY,
                chat_id=cfg.escalation_chat_id if cfg is not None else None,
                text=(
                    f"Code-review compliance violation для {story_id}:\n\n"
                    f"{compliance_hit}\n\n"
                    f"Original verdict: {verdict}\nSummary: {summary}\n\n"
                    f"Worktree: {worktree}"
                ),
                story_id=story_id,
                verdict="compliance_violation",
                gate_reason=compliance_hit,
                compliance_tags=list(metrics.compliance_tags),
                worktree=worktree,
                review_jsonl=handle_jsonl_str,
                actions=["mandatory_fix", "abandon"],
            )
            return

    # ── P0 + test-coverage gates — override approve → reject if tripped.
    gate_reasons: list[str] = []
    if metrics is not None and verdict == "approve":
        p0_reason = _gate_p0_threshold(metrics, gates.p0_threshold)
        if p0_reason is not None:
            gate_reasons.append(p0_reason)
        tc_reason = _gate_test_coverage(metrics, gates.test_coverage_threshold)
        if tc_reason is not None:
            gate_reasons.append(tc_reason)

    # ── P5 iteration cap — fires regardless of verdict; runaway-loop guard.
    iter_reason = _gate_iteration_cap(review_iteration, gates.max_review_iterations)
    if iter_reason is not None:
        gate_reasons.append(iter_reason)
        log.info(
            "code_review_iteration_cap_tripped",
            story_id=story_id,
            review_iteration=review_iteration,
            cap=gates.max_review_iterations,
        )

    # ── Phase 4 hardening #3 — banned-phrase gate.
    # Fires only when verdict == "approve" (approve → request_changes flip).
    # Catches premature-completion summaries that lack concrete evidence.
    if verdict == "approve" and summary:
        bp_override: Path | None = None
        try:
            _settings = load_settings()
            bp_override = _settings.banned_phrases_path
        except Exception as _exc:
            log.warning("banned_phrase_gate_settings_load_failed", error=str(_exc))
        bp_reason = _gate_banned_phrases(summary, override_path=bp_override)
        if bp_reason is not None:
            gate_reasons.append(bp_reason)
            log.info(
                "code_review_banned_phrase_gate_tripped",
                story_id=story_id,
                reason=bp_reason,
            )

    # ── Patch Q (diff size) + Patch W (scope by File List allow-list).
    #    Even if metrics are missing (e.g. review emitted no structured block),
    #    the size + scope checks run purely on git so they catch runaway scope
    #    regardless of the review parser's coverage. The allow-list is built
    #    from the story's `### File List` section ∪ infra paths; when the
    #    target_project is unconfigured (tests), Patch W silently degrades to
    #    pure Patch Q (P3 behaviour).
    #    Permissive when the story declares NO explicit File List entries
    #    (BMad v6+ stories populate File List as a post-condition). See
    #    code-review finding 6.4.
    if verdict == "approve":
        try:
            diff_policy = load_diff_size_policy()
        except (PolicyNotFoundError, PolicyInvalidError) as e:
            log.warning("diff_size_policy_load_failed", error=str(e))
        else:
            if diff_policy.enabled:
                out_of_scope_paths: list[str] | None = None
                diff_metrics = await measure_diff(
                    Path(worktree), range_spec=diff_policy.range_spec
                )
                if cfg is not None and cfg.target_project is not None:
                    explicit = has_explicit_file_list(cfg.target_project, story_id)
                    if not explicit:
                        log.info(
                            "scope_check_skipped_no_file_list",
                            story_id=story_id,
                            reason="story declares no explicit ### File List",
                        )
                    else:
                        allow_list = collect_allow_list(cfg.target_project, story_id)
                        per_file = await measure_diff_per_file(
                            Path(worktree), range_spec=diff_policy.range_spec
                        )
                        in_scope_metrics, out_paths = partition_per_file(
                            per_file, allow_list
                        )
                        out_of_scope_paths = out_paths
                        diff_metrics = in_scope_metrics
                diff_reason = _diff_size_gate_verdict(
                    diff_metrics,
                    diff_policy,
                    out_of_scope_paths=out_of_scope_paths,
                )
                if diff_reason is not None:
                    gate_reasons.append(diff_reason)

    if gate_reasons:
        # iteration-cap reason trips regardless of original verdict; the other
        # gates only override when verdict was already "approve". When any
        # reason is present and verdict was approve → force reject; when it was
        # already non-approve, the reasons annotate the summary but the verdict
        # stays as the worker reported.
        if verdict == "approve":
            verdict = "reject"
        prefix = "; ".join(gate_reasons)
        summary = f"{prefix}\n\n(original: {summary})" if summary else prefix
        log.info(
            "code_review_gate_override",
            story_id=story_id,
            reasons=gate_reasons,
            review_iteration=review_iteration,
        )

    log.info(
        "code_review_dispatched",
        story_id=story_id,
        verdict=verdict,
        worktree=worktree,
        review_jsonl=handle_jsonl_str,
    )
    emit_payload: dict[str, Any] = {
        "story_id": story_id,
        "verdict": verdict,
        "summary": summary,
        "worktree": worktree,
        "review_jsonl": handle_jsonl_str,
        "review_iteration": review_iteration,
        "source": verdict_source,
    }
    if gate_reasons:
        emit_payload["gate_reasons"] = gate_reasons
    await bus.emit(EventType.CODE_REVIEW_VERDICT, **emit_payload)

    # ── E6 — L2 live tuning. Feed metrics into BudgetGuard's rolling windows
    #    and (every ``MIN_SAMPLES_FOR_TUNING`` informative stories per metric)
    #    recompute + persist tuned thresholds. Out-of-bounds proposals
    #    (>50% movement) emit HUMAN_QUERY instead of writing.
    if metrics is not None and cfg is not None and cfg.budget is not None:
        await _apply_live_tuning(
            bus=bus,
            cfg=cfg,
            gates=gates,
            metrics=metrics,
            story_id=story_id,
            review_iteration=review_iteration,
        )


async def _ff_merge_to_integration(
    *,
    target_project: Path,
    integration_branch: str,
    feature_branch: str,
) -> str:
    """Fast-forward merge ``feature_branch`` → ``integration_branch``.

    Returns the commit hash that integration now points at. Raises whatever
    gitpython raises on conflict / non-ff / missing branch — caller wraps the
    error into a ``HUMAN_QUERY`` escalation.

    Hard rules per CLAUDE.md + spec §W4: this helper is restricted to ff-only
    plus signoff. Disabling pre-commit hooks, forcing the ref forward, or
    rewriting history with destructive resets are all out of scope.
    """
    from git import Repo

    repo = Repo(str(target_project))

    existing = {b.name for b in repo.branches}
    if integration_branch not in existing:
        base = "main" if "main" in existing else repo.active_branch.name
        repo.git.branch(integration_branch, base)

    repo.git.checkout(integration_branch)
    repo.git.merge(feature_branch, "--ff-only", "--signoff")
    head_sha: str = repo.head.commit.hexsha
    return head_sha


async def merge_to_integration_subscriber(event: Event, bus: EventLoop) -> None:
    """On ``CODE_REVIEW_VERDICT`` → fast-forward merge OR escalate.

    Behaviour matrix:

    * ``verdict == "approve"``  → ``git merge --ff-only --signoff`` of
      ``feature/<story>`` into ``integration/<wave>`` in the target project.
      On success: emit a ``story_merged`` audit log + cleanup worktree under
      the project's ``.worktrees/`` root. On failure (conflict, non-ff, missing
      branch): emit ``HUMAN_QUERY`` with the diagnostic payload.
    * ``verdict in {"request_changes", "reject", "error"}`` → emit
      ``HUMAN_QUERY`` with the review summary; worktree stays put for human
      inspection.
    """
    if event.type != EventType.CODE_REVIEW_VERDICT:
        return

    cfg = _CODE_REVIEW_GATE
    if cfg is None:
        log.warning("merge_subscriber_unconfigured")
        return

    payload = event.payload or {}
    story_id = str(payload.get("story_id") or "")
    verdict = str(payload.get("verdict") or "")
    summary = str(payload.get("summary") or "")
    worktree = str(payload.get("worktree") or "")

    if not story_id:
        log.warning("merge_subscriber_missing_story_id", payload=payload)
        return

    # NEW-7 — a synthetic reconcile verdict (success_path_reconcile) may carry
    # an empty ``worktree`` payload. Resolve it from the project worktree
    # registry (``<project>/.worktrees/wt-<story_id>``) instead of failing the
    # pre-merge recovery silently. Same convention as worker spawn (run.py:635).
    if not worktree and cfg.target_project is not None:
        candidate = cfg.target_project / ".worktrees" / f"wt-{story_id}"
        if candidate.exists():
            worktree = str(candidate)
            log.info(
                "merge_subscriber_worktree_resolved_from_registry",
                story_id=story_id,
                worktree=worktree,
            )

    if verdict != "approve":
        await bus.emit(
            EventType.HUMAN_QUERY,
            chat_id=cfg.escalation_chat_id,
            text=(
                f"Code-review {verdict} для {story_id}:\n\n"
                f"{summary}\n\nWorktree: {worktree}"
            ),
            story_id=story_id,
            verdict=verdict,
            worktree=worktree,
            actions=["approve_override", "abandon", "edit_in_human_loop"],
        )
        return

    # Patch R — auto-stage any uncommitted residue in the worker's worktree
    # before merge. Only fires when ``worktree`` is an actual git worktree
    # (has its own ``.git`` entry — file for ``git worktree add`` worktrees,
    # directory for ordinary clones). Plain marker directories inside an
    # outer repo (some tests pass these) MUST NOT trigger a recovery commit:
    # ``git -C plain-dir`` walks up the tree to the outer repo and would
    # commit on whatever branch is currently checked out there, diverging
    # the merge target. Best-effort: failures here do NOT block the merge.
    if worktree and (Path(worktree) / ".git").exists():
        # Patch W — scope recovery to the story's File List allow-list when
        # the project root is configured. Out-of-scope dirty paths stay in
        # the working tree (caller surfaces them via gate_reasons / human
        # query downstream); only allow-listed paths get auto-staged.
        allow_list = (
            collect_allow_list(cfg.target_project, story_id)
            if cfg.target_project is not None
            else None
        )
        recovery = await recover_pre_merge(
            Path(worktree), allow_list=allow_list
        )
        if recovery.recovered:
            log.info(
                "patch_r_pre_merge_recovery",
                story_id=story_id,
                worktree=worktree,
                commit_sha=recovery.commit_sha,
                staged=list(recovery.staged_paths),
                out_of_scope=list(recovery.out_of_scope_paths),
            )
        elif recovery.error:
            log.warning(
                "patch_r_pre_merge_recovery_failed",
                story_id=story_id,
                worktree=worktree,
                error=recovery.error,
                out_of_scope=list(recovery.out_of_scope_paths),
            )
        elif recovery.out_of_scope_paths:
            log.info(
                "patch_w_all_dirty_out_of_scope",
                story_id=story_id,
                worktree=worktree,
                out_of_scope=list(recovery.out_of_scope_paths),
            )

    integration_branch = f"integration/{cfg.wave}"
    feature_branch = f"feature/{story_id}"
    try:
        merge_sha = await _ff_merge_to_integration(
            target_project=cfg.target_project,
            integration_branch=integration_branch,
            feature_branch=feature_branch,
        )
    except Exception as exc:  # git library errors (GitCommandError) + subprocess
        log.exception(
            "merge_to_integration_failed",
            story_id=story_id,
            feature=feature_branch,
            integration=integration_branch,
        )
        # NEW-7 observability — the fast-forward merge failed; the work is
        # stranded on feature/<story>. Surface it before the human query.
        await bus.emit(
            EventType.INTEGRATION_MERGE_SKIPPED,
            story_id=story_id,
            reason="ff_conflict",
            worktree=worktree,
            commits=0,
        )
        await bus.emit(
            EventType.HUMAN_QUERY,
            chat_id=cfg.escalation_chat_id,
            text=(
                f"Merge conflict для {story_id}:\n\n"
                f"{type(exc).__name__}: {exc}\n\nWorktree: {worktree}"
            ),
            story_id=story_id,
            verdict="merge_conflict",
            worktree=worktree,
            actions=["manual_resolve", "abandon"],
        )
        return

    log.info(
        "story_merged",
        story_id=story_id,
        feature=feature_branch,
        integration=integration_branch,
        sha=merge_sha,
    )

    if worktree:
        try:
            cleanup_worktree(
                Path(worktree), root=cfg.target_project / ".worktrees"
            )
        except OSError as exc:  # cleanup failure must not block the merge
            log.warning(
                "worktree_cleanup_failed",
                story_id=story_id,
                worktree=worktree,
                error=f"{type(exc).__name__}: {exc}",
            )


# ── E5 — Quarterly compliance sweep ──────────────────────────────────────────


async def quarterly_sweep_subscriber(event: Event, bus: EventLoop) -> None:
    """On ``WAVE_BOUNDARY_REACHED`` emit ``COMPLIANCE_SWEEP_NEEDED`` каждые N stories.

    Threshold ``sweep_every_stories`` from ``code-review-gates.yaml`` (default
    50). Trips when ``completed_stories > 0 and completed_stories % N == 0``.
    Payload carries ``wave`` + ``completed_stories`` for downstream consumers.
    """
    if event.type != EventType.WAVE_BOUNDARY_REACHED:
        return
    payload = event.payload or {}
    raw = payload.get("completed_stories", 0)
    try:
        completed = int(raw)
    except (TypeError, ValueError):
        return
    if completed <= 0:
        return

    gates = _load_review_gates(_CODE_REVIEW_GATE)
    n = gates.sweep_every_stories
    if n <= 0 or completed % n != 0:
        return

    wave = str(payload.get("wave") or "")
    log.info(
        "compliance_sweep_emitted",
        wave=wave,
        completed_stories=completed,
        threshold=n,
    )
    await bus.emit(
        EventType.COMPLIANCE_SWEEP_NEEDED,
        wave=wave,
        completed_stories=completed,
        threshold=n,
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
    "run_replay",
]


if __name__ == "__main__":
    main()
