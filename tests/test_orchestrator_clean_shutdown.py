"""Initiative pilot_findings_closure v3 (#6 NEW-8) — clean orchestrator shutdown.

Covers :func:`agent.run._shutdown_orchestrator` and the post-pilot shutdown
wiring in :func:`agent.run.run_orchestrator`:

  * shutdown cancels orchestrator-spawned background tasks (event-bus backstop
    + any orphan coroutine) — fixing the ~13-min hang after ``real_pilot_done``;
  * cooperative tasks → shutdown completes well within the timeout;
  * a task that swallows cancellation → shutdown returns anyway (bounded by the
    hard timeout, ``orchestrator_shutdown_timeout`` logged);
  * integration — a mock pilot's ``run_orchestrator`` returns without hanging
    and leaves no live backstop task.

Spec target: +4 tests.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

import bmad_orchestrator.agent.run as run_mod
from bmad_orchestrator.agent.run import _shutdown_orchestrator, run_orchestrator
from bmad_orchestrator.config import Settings
from bmad_orchestrator.runtime.event_loop import EventLoop


@pytest.mark.asyncio
async def test_shutdown_cancels_leftover_tasks() -> None:
    """Backstop task + an orphan coroutine spawned during the run are cancelled."""
    bus = EventLoop()
    pre_existing = asyncio.all_tasks()

    bus.start_backstop_task()

    async def _orphan() -> None:
        await asyncio.sleep(3600)

    orphan = asyncio.create_task(_orphan())
    await asyncio.sleep(0)  # let both tasks start

    await _shutdown_orchestrator(bus, pre_existing=pre_existing)

    assert bus._stopped.is_set()
    assert bus._backstop_task is None
    assert orphan.cancelled() or orphan.done()


@pytest.mark.asyncio
async def test_shutdown_completes_within_timeout() -> None:
    """Cooperative tasks → shutdown returns quickly, far under the timeout."""
    bus = EventLoop()
    pre_existing = asyncio.all_tasks()
    bus.start_backstop_task()

    started = time.monotonic()
    await _shutdown_orchestrator(bus, pre_existing=pre_existing)
    elapsed = time.monotonic() - started

    assert elapsed < 5.0
    assert bus._backstop_task is None


@pytest.mark.asyncio
async def test_shutdown_timeout_forces_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A task that swallows cancellation → shutdown still returns (hard timeout)."""
    monkeypatch.setattr(run_mod, "ORCHESTRATOR_SHUTDOWN_TIMEOUT_S", 0.3)

    bus = EventLoop()
    pre_existing = asyncio.all_tasks()
    stop_evt = asyncio.Event()

    async def _stubborn() -> None:
        while True:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                if stop_evt.is_set():
                    raise
                # swallow — simulate a task that ignores cancellation

    stubborn = asyncio.create_task(_stubborn())
    await asyncio.sleep(0)

    started = time.monotonic()
    await _shutdown_orchestrator(bus, pre_existing=pre_existing)
    elapsed = time.monotonic() - started

    # Bounded — shutdown did NOT hang on the cancel-proof task.
    assert elapsed < 5.0
    assert not stubborn.done(), "stubborn task swallowed cancellation as designed"

    # Cleanup — release the task for real.
    stop_evt.set()
    stubborn.cancel()
    await asyncio.gather(stubborn, return_exceptions=True)


def _make_mock_target(tmp_path: Path) -> Path:
    """Minimal target project tree for a mock pilot."""
    target = tmp_path / "proj"
    artifacts = target / "_bmad-output" / "planning-artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "sprint-status.yaml").write_text(
        "wave: w\n"
        "epics:\n"
        "  e1:\n"
        "    stories:\n"
        "      s1: ready-for-dev\n",
        encoding="utf-8",
    )
    stories_dir = artifacts / "stories"
    stories_dir.mkdir()
    (stories_dir / "s1.md").write_text(
        "# Story s1\n\n"
        "- **epic:** 1\n"
        "- **status:** ready\n"
        "- **risk:** low\n"
        "- **estimated_tokens:** 1000\n"
        "- **estimated_minutes:** 5\n"
        "- **touches_files:** []\n"
        "- **touches_shared:** []\n"
        "- **depends_on:** []\n",
        encoding="utf-8",
    )
    return target


@pytest.mark.asyncio
async def test_run_orchestrator_mock_returns_without_hang(tmp_path: Path) -> None:
    """A mock pilot's run_orchestrator returns promptly and stops the bus."""
    target = _make_mock_target(tmp_path)
    settings = Settings(
        target_project=target,
        state_db=tmp_path / "state.db",
    )

    bus = await asyncio.wait_for(
        run_orchestrator(
            project="proj",
            wave="w",
            max_parallel=1,
            mock=True,
            settings=settings,
        ),
        timeout=15,
    )

    assert bus._stopped.is_set()
    assert bus._backstop_task is None
    live_backstop = [
        t
        for t in asyncio.all_tasks()
        if t.get_name() == "event_loop_backstop" and not t.done()
    ]
    assert not live_backstop
