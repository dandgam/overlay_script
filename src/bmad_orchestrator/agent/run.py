"""Master orchestrator entry point (spec §2 + §4 + §11).

S8 wiring: ClaudeSDKClient с тулзами (35) + memory_tool + hooks (security_check_hook,
audit_tool_output) + system_prompt (cached, ttl=1h) + EventLoop (Bus между bot
handlers и agent thinking).

Mock-mode (``mock=True``) — пропускает реальный ClaudeSDKClient (т.к. для CI без
ANTHROPIC_API_KEY и для unit-тестов). Вместо этого прогоняет cascade DAG → workers
до WAVE_BOUNDARY_REACHED → выходит. Это и есть E2E pilot per §22.

Real-mode wiring до полного «one-call-per-event» loop'а deferred к pilot run на
Odyssey Wave 1a (отдельная инициатива). Сейчас real-mode инициализирует client
и эмитит SCHEDULED_WAKEUP — достаточно чтобы убедиться что options валидируются.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import structlog

from bmad_orchestrator.agent.memory.memory_tool import memory_tool_definition
from bmad_orchestrator.agent.safety.budget_guard import BudgetGuard
from bmad_orchestrator.agent.safety.hooks import audit_tool_output, security_check_hook
from bmad_orchestrator.agent.skills import dispatch as dispatch_skills
from bmad_orchestrator.agent.skills import load_body as load_skill_body
from bmad_orchestrator.agent.system_prompt import build_system_prompt
from bmad_orchestrator.agent.tools import ALL_TOOLS
from bmad_orchestrator.config import ModelConfig, load_settings
from bmad_orchestrator.runtime.dag_planner import DagPlanner
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.runtime.worker_spawn import spawn_worker as runtime_spawn_worker

log = structlog.get_logger(__name__)


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
    """Main orchestrator loop. Returns the EventLoop instance (для тестов / bridge).

    ``models`` — per-role config (CLI/Telegram override). None → settings default.
    ``mock`` — пропустить реальный ClaudeSDKClient, прогнать только DAG cascade.
    ``event_loop`` — externally-supplied bus (для bot bridge / unit tests).
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

    # Real-mode initialization. Full event-driven loop with claude-agent-sdk
    # is wired here; per spec §22 deferred items we keep this minimal until
    # the Odyssey pilot run (separate initiative).
    options = build_agent_options(
        project_root=settings.target_project,
        wave=wave,
        models=models,
    )
    log.info(
        "orchestrator_options_built",
        tool_count=len(options["tools"]) if isinstance(options.get("tools"), list) else 0,
        beta_headers=options.get("beta_headers", []),
    )

    # Connect the agent SDK only when ANTHROPIC_API_KEY is present.
    if settings.anthropic_api_key:
        await _attempt_sdk_run(options, bus, max_iterations=1)
    else:
        log.warning("orchestrator_no_api_key", hint="run with --mock or set ANTHROPIC_API_KEY")

    await bus.emit(EventType.SCHEDULED_WAKEUP, reason="run_orchestrator_done")
    log.info("orchestrator_exited")
    return bus


def build_agent_options(
    *,
    project_root: Path,
    wave: str,
    models: ModelConfig,
) -> dict[str, Any]:
    """Build the dict passed to ``ClaudeSDKClient(ClaudeAgentOptions(**...))``.

    Returns a plain dict (not the SDK class) so the function is import-light
    and unit-testable without the SDK present. Caller adapts to SDK signature.
    """
    settings = load_settings()
    system_blocks = build_system_prompt(project_root=project_root, wave=wave,
                                        locale=settings.locale)
    tools_payload: list[Any] = [*ALL_TOOLS, memory_tool_definition()]
    return {
        "system": system_blocks,
        "tools": tools_payload,
        "model": models.dev,  # default per-call model; per-role routing applied at tool layer
        "beta_headers": settings.beta_headers,
        "hooks": {
            "PreToolUse": [security_check_hook],
            "PostToolUse": [audit_tool_output],
        },
    }


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


# ── SDK glue (best-effort, optional) ─────────────────────────────────────────


async def _attempt_sdk_run(
    options: dict[str, Any],
    bus: EventLoop,
    *,
    max_iterations: int,
) -> None:
    """Adapter to claude-agent-sdk if installed. Soft-fails to log on import error.

    The real long-running event loop is the deferred Wave 1a pilot — here we
    just verify import + option shape.
    """
    try:
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
    except ImportError as exc:
        log.warning("claude_sdk_unavailable", error=str(exc))
        return

    try:
        sdk_options = ClaudeAgentOptions(**options)
    except TypeError as exc:
        # SDK version mismatch — log and bail, не падаем для CI без точного SDK.
        log.warning("claude_sdk_options_unsupported", error=str(exc))
        return

    async with ClaudeSDKClient(options=sdk_options) as client:
        log.info("claude_sdk_connected", client=type(client).__name__)
        # Pump one wake — full loop deferred to pilot.
        for _ in range(max_iterations):
            event = await bus.dispatch_one(timeout=0.1)
            if event is None:
                break
            await _on_event(event, bus)


async def _on_event(event: Event, bus: EventLoop) -> None:
    """Dispatch one event into skill bodies (Level 2 disclosure)."""
    skills = dispatch_skills(event.type)
    for skill in skills:
        body = load_skill_body(skill)
        log.debug("skill_body_loaded", skill=skill, body_chars=len(body))


def main() -> None:
    """Module-entry shim for ``python -m bmad_orchestrator.agent.run <project> <wave>``."""
    import sys

    if len(sys.argv) < 3:
        print("Usage: python -m bmad_orchestrator.agent.run <project> <wave>")
        sys.exit(2)
    asyncio.run(run_orchestrator(sys.argv[1], sys.argv[2], mock=True))


__all__ = ["build_agent_options", "main", "run_orchestrator"]


if __name__ == "__main__":
    main()
