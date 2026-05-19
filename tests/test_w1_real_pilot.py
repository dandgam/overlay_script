"""W1 acceptance tests — real-mode event loop core (wave_1a_pilot_wiring).

Coverage (per spec/spec_wave_1a_pilot_wiring.md §W1):
- CLI flags ``--max-stories`` / ``--max-spend-usd`` present with correct defaults.
- ``run_orchestrator(mock=False)`` no longer raises NotImplementedError;
  dispatches to ``_run_real_pilot`` with the user-supplied caps.
- ``_run_real_pilot`` enforces ``BMAD_REQUIRE_SANDBOX=1`` via ``detect_sandbox()``.
- Fake spawn_worker → ``WORKER_COMPLETED`` event bridged to bus via JSONL tail.
- ``max_stories`` hard cap blocks further spawns.
- ``max_spend_usd`` hard cap emits ``BUDGET_THRESHOLD_HIT`` with
  ``reason=max_spend_usd_cap`` and halts.
- ``WAVE_BOUNDARY_REACHED`` emitted at end of real pilot.
- Mock path (``mock=True``) unchanged — no regression.
"""

from __future__ import annotations

import inspect
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from bmad_orchestrator.agent import run as run_module
from bmad_orchestrator.agent.run import (
    _run_real_pilot,
    _tail_and_emit_completion,
    run_orchestrator,
)
from bmad_orchestrator.agent.safety.budget_guard import BudgetGuard
from bmad_orchestrator.cli import main as cli_main
from bmad_orchestrator.config import ModelConfig, load_settings
from bmad_orchestrator.runtime.event_loop import EventLoop, EventType
from bmad_orchestrator.runtime.worker_spawn import WorkerHandle

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_target_with_stories(tmp_path: Path, *story_ids: str) -> Path:
    """Build a minimal target project tree with N ready-for-dev stories."""
    target = tmp_path / "proj"
    target.mkdir(exist_ok=True)
    # Patch Y 2026-05-18: _ensure_git_worktree (commit 358d77f) needs a real
    # git repo with HEAD commit at the target — initialise here.
    subprocess.run(["git", "init", "-q", "-b", "main", str(target)], check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.name", "t"], check=True)
    (target / "README").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(target), "add", "README"], check=True)
    subprocess.run(["git", "-C", str(target), "commit", "-q", "-m", "seed"], check=True)
    artifacts = target / "_bmad-output" / "planning-artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    stories_block = "\n".join(f"      {sid}: ready-for-dev" for sid in story_ids)
    (artifacts / "sprint-status.yaml").write_text(
        "wave: w\n"
        "epics:\n"
        "  e1:\n"
        "    stories:\n"
        f"{stories_block}\n",
        encoding="utf-8",
    )
    stories_dir = artifacts / "stories"
    stories_dir.mkdir(exist_ok=True)
    for sid in story_ids:
        (stories_dir / f"{sid}.md").write_text(
            f"# Story {sid}\n\n"
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


def _seed_jsonl_completed(jsonl_path: Path, story_id: str, status: str = "success") -> None:
    """Pre-populate a JSONL file with a terminal worker_completed event."""
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "event_type": "worker_completed",
        "worktree": str(jsonl_path.parent),
        "story_id": story_id,
        "exit_code": 0 if status == "success" else 1,
        "status": status,
        "ts": "2026-05-16T00:00:00+00:00",
    }
    jsonl_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _make_fake_spawn(jsonl_root: Path) -> Any:
    """Return an async fake of ``runtime_spawn_worker`` that pre-emits completed."""

    async def _fake_spawn(
        *,
        worktree: str,
        story_id: str,
        branch: str,
        mock: bool = False,
        sandbox_network: str = "full",
        **_: Any,
    ) -> WorkerHandle:
        jsonl_path = jsonl_root / f"{story_id}.jsonl"
        _seed_jsonl_completed(jsonl_path, story_id)
        return WorkerHandle(
            worktree=worktree,
            story_id=story_id,
            branch=branch,
            pid=12345,
            jsonl_path=jsonl_path,
            process=None,
            mock=mock,
            sandbox_kind="bwrap",
        )

    return _fake_spawn


async def _drain(bus: EventLoop) -> list[Any]:
    """Drain queued events so subscribers fire; return captured events.

    Also stops the bus so its backstop task is cancelled — without this,
    ``EventLoop.start_backstop_task``'s ``suppress(CancelledError)`` loop
    survives pytest-asyncio teardown and hangs subsequent tests.
    """
    captured: list[Any] = []

    async def _capture(ev: Any) -> None:
        captured.append(ev)

    bus.on(_capture)
    while await bus.dispatch_one(timeout=0.01) is not None:
        pass
    await bus.stop()
    return captured


def _record_events(bus: EventLoop) -> list[Any]:
    """Attach a capture subscriber BEFORE the pilot runs.

    Since NEW-7 the real pilot drains its own bus (``EventLoop.drain``) before
    returning, so the queue is empty post-run. Capture must therefore subscribe
    up front and observe events as the production drain dispatches them.
    """
    captured: list[Any] = []

    async def _capture(ev: Any) -> None:
        captured.append(ev)

    bus.on(_capture)
    return captured


async def _run_pilot_and_stop(bus: EventLoop, **kwargs: Any) -> None:
    """Call ``_run_real_pilot`` with sensible defaults, then stop the bus.

    Stopping the bus is mandatory in tests — its backstop task suppresses
    ``CancelledError`` in a loop, so pytest-asyncio teardown can't cancel it.
    """
    defaults: dict[str, Any] = {
        "project": "proj",
        "wave": "w",
        "max_parallel": 1,
        "max_stories": 5,
        "max_spend_usd": 50.0,
        "state_db": None,
        "session_id": None,
        "models": ModelConfig(),
        "options": {},
        "settings": load_settings(),
    }
    defaults.update(kwargs)
    try:
        await _run_real_pilot(bus, **defaults)
    finally:
        await bus.stop()


# ── CLI flag presence / defaults ─────────────────────────────────────────────


def test_w1_cli_run_has_max_stories_flag() -> None:
    sig = inspect.signature(cli_main.run)
    assert "max_stories" in sig.parameters
    assert sig.parameters["max_stories"].default.default == 50


def test_w1_cli_run_has_max_spend_usd_flag() -> None:
    sig = inspect.signature(cli_main.run)
    assert "max_spend_usd" in sig.parameters
    assert sig.parameters["max_spend_usd"].default.default == 50.0


def test_w1_cli_run_help_shows_caps() -> None:
    runner = CliRunner()
    result = runner.invoke(cli_main.app, ["run", "--help"])
    assert result.exit_code == 0
    out = result.stdout
    assert "--max-stories" in out
    assert "--max-spend-usd" in out


def test_w1_cli_run_help_real_text_no_not_implemented() -> None:
    """The ``--real`` help blurb must no longer reference NotImplementedError."""
    runner = CliRunner()
    result = runner.invoke(cli_main.app, ["run", "--help"])
    assert "NotImplementedError" not in result.stdout


# ── Grep validation ──────────────────────────────────────────────────────────


def test_w1_no_not_implemented_in_run_py() -> None:
    """``raise NotImplementedError`` must be gone from run.py after W1."""
    src = Path("src/bmad_orchestrator/agent/run.py").read_text(encoding="utf-8")
    assert "raise NotImplementedError" not in src, (
        "real-mode no longer raises NotImplementedError — strip the stale raise"
    )


def test_w1_run_real_pilot_defined() -> None:
    """``_run_real_pilot`` must exist as an async function in run.py."""
    assert inspect.iscoroutinefunction(run_module._run_real_pilot)


# ── run_orchestrator signature ───────────────────────────────────────────────


def test_w1_run_orchestrator_signature_has_caps() -> None:
    sig = inspect.signature(run_orchestrator)
    assert sig.parameters["max_stories"].default == 50
    assert sig.parameters["max_spend_usd"].default == 50.0


def test_w1_run_orchestrator_real_dispatches_with_caps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``run_orchestrator(mock=False, max_stories=N, max_spend_usd=M)`` must
    forward the caps to ``_run_real_pilot`` (skip real init path via stub)."""
    captured: dict[str, Any] = {}

    async def _stub(bus: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(run_module, "_run_real_pilot", _stub)

    import asyncio
    asyncio.run(run_orchestrator(
        project="x", wave="1a", mock=False,
        max_stories=4, max_spend_usd=7.5,
    ))
    assert captured["max_stories"] == 4
    assert captured["max_spend_usd"] == 7.5


# ── --story flag (manual story-filter mode, bypasses DAG planner) ────────────


def test_w1_cli_run_has_story_flag() -> None:
    sig = inspect.signature(cli_main.run)
    assert "story" in sig.parameters


def test_w1_cli_run_help_shows_story_flag() -> None:
    runner = CliRunner()
    result = runner.invoke(cli_main.app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--story" in result.stdout


def test_w1_run_orchestrator_forwards_stories_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``stories=(...)`` must flow into ``_run_real_pilot(story_filter=...)``."""
    captured: dict[str, Any] = {}

    async def _stub(bus: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(run_module, "_run_real_pilot", _stub)

    import asyncio
    asyncio.run(run_orchestrator(
        project="x", wave="1a", mock=False,
        stories=("3.2", "3.3"),
    ))
    assert captured["story_filter"] == ("3.2", "3.3")


def test_w1_run_orchestrator_no_stories_passes_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def _stub(bus: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(run_module, "_run_real_pilot", _stub)

    import asyncio
    asyncio.run(run_orchestrator(project="x", wave="1a", mock=False))
    assert captured["story_filter"] is None


@pytest.mark.asyncio
async def test_w1_story_filter_missing_id_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """``--story <unknown_id>`` must raise RuntimeError before any spawn."""
    from bmad_orchestrator.agent.safety.budget_guard import BudgetGuard
    from bmad_orchestrator.config import BudgetConfig, ModelConfig
    from bmad_orchestrator.runtime.event_loop import EventLoop

    # Empty stories dir → any --story id is missing.
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(tmp_path))
    monkeypatch.setenv("BMAD_SANDBOX", "none")
    monkeypatch.setenv("BMAD_SANDBOX_DISABLE_CONFIRMED", "yes-i-accept-risk")

    bus = EventLoop()
    budget = BudgetGuard(BudgetConfig(), event_loop=bus)
    try:
        with pytest.raises(RuntimeError, match=r"--story.*not found"):
            await _run_real_pilot(
                bus,
                project="proj", wave="w",
                max_parallel=1, max_stories=1, max_spend_usd=10.0,
                budget=budget, state_db=None, session_id=None,
                models=ModelConfig(), options={}, settings=load_settings(),
                story_filter=("does-not-exist",),
            )
    finally:
        await bus.stop()


# ── BMAD_REQUIRE_SANDBOX guard ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_w1_sandbox_guard_raises_when_require_set_and_no_bwrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``BMAD_REQUIRE_SANDBOX=1`` + NoSandbox path → RuntimeError at entry."""
    monkeypatch.setenv("BMAD_REQUIRE_SANDBOX", "1")
    monkeypatch.setenv("PATH", "/nonexistent-path-for-w1")  # force NoSandbox

    bus = EventLoop()
    budget = BudgetGuard(load_settings().budget, event_loop=bus)

    with pytest.raises(RuntimeError, match="BMAD_REQUIRE_SANDBOX"):
        await _run_real_pilot(
            bus,
            project="x",
            wave="w",
            max_parallel=1,
            max_stories=5,
            max_spend_usd=50.0,
            budget=budget,
            state_db=None,
            session_id=None,
            models=ModelConfig(),
            options={},
            settings=load_settings(),
        )


@pytest.mark.asyncio
async def test_w1_sandbox_guard_passes_when_require_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """No ``BMAD_REQUIRE_SANDBOX`` → NoSandbox tolerated; pilot proceeds."""
    monkeypatch.delenv("BMAD_REQUIRE_SANDBOX", raising=False)

    target = _make_target_with_stories(tmp_path)  # zero stories → empty DAG
    # PATH munged AFTER fixture init — fixture needs git on PATH (Patch Y).
    monkeypatch.setenv("PATH", "/nonexistent-path-for-w1")
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target))

    bus = EventLoop()
    budget = BudgetGuard(load_settings().budget, event_loop=bus)

    # Should not raise — empty DAG, immediate WAVE_BOUNDARY_REACHED.
    try:
        await _run_real_pilot(
            bus,
            project="proj",
            wave="w",
            max_parallel=1,
            max_stories=5,
            max_spend_usd=50.0,
            budget=budget,
            state_db=None,
            session_id=None,
            models=ModelConfig(),
            options={},
            settings=load_settings(),
        )
    finally:
        await bus.stop()


# ── Fake claude end-to-end ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_w1_real_pilot_spawns_worker_and_bridges_completed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Fake ``runtime_spawn_worker`` → JSONL pre-seeded → bus emits
    ``WORKER_COMPLETED`` for each spawned story."""
    monkeypatch.delenv("BMAD_REQUIRE_SANDBOX", raising=False)
    target = _make_target_with_stories(tmp_path, "s1", "s2")
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target))

    fake_spawn = _make_fake_spawn(tmp_path / "jsonl")
    monkeypatch.setattr(run_module, "runtime_spawn_worker", fake_spawn)

    bus = EventLoop()
    budget = BudgetGuard(load_settings().budget, event_loop=bus)

    events = _record_events(bus)
    await _run_real_pilot(
        bus,
        project="proj",
        wave="w",
        max_parallel=2,
        max_stories=10,
        max_spend_usd=100.0,
        budget=budget,
        state_db=None,
        session_id=None,
        models=ModelConfig(),
        options={},
        settings=load_settings(),
    )

    await _drain(bus)
    completed = [e for e in events if e.type == EventType.WORKER_COMPLETED]
    assert len(completed) >= 1, f"expected ≥1 WORKER_COMPLETED, got {events!r}"
    # WorkerHandle.mock must be False (real path).
    payloads = [e.payload for e in completed]
    assert all(p.get("mock") is False for p in payloads)


@pytest.mark.asyncio
async def test_w1_real_pilot_calls_spawn_with_mock_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """``runtime_spawn_worker`` invoked with ``mock=False, sandbox_network='full'``."""
    monkeypatch.delenv("BMAD_REQUIRE_SANDBOX", raising=False)
    target = _make_target_with_stories(tmp_path, "s1")
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target))

    captured_kwargs: dict[str, Any] = {}

    async def _fake_spawn(**kwargs: Any) -> WorkerHandle:
        captured_kwargs.update(kwargs)
        jsonl_path = tmp_path / f"{kwargs['story_id']}.jsonl"
        _seed_jsonl_completed(jsonl_path, kwargs["story_id"])
        return WorkerHandle(
            worktree=kwargs["worktree"],
            story_id=kwargs["story_id"],
            branch=kwargs["branch"],
            pid=1,
            jsonl_path=jsonl_path,
            process=None,
            mock=False,
            sandbox_kind="bwrap",
        )

    monkeypatch.setattr(run_module, "runtime_spawn_worker", _fake_spawn)

    bus = EventLoop()
    budget = BudgetGuard(load_settings().budget, event_loop=bus)
    await _run_real_pilot(
        bus, project="proj", wave="w", max_parallel=1, max_stories=1,
        max_spend_usd=50.0, budget=budget, state_db=None, session_id=None,
        models=ModelConfig(), options={}, settings=load_settings(),
    )
    await bus.stop()

    assert captured_kwargs.get("mock") is False
    assert captured_kwargs.get("sandbox_network") == "full"
    assert captured_kwargs.get("story_id") == "s1"


@pytest.mark.asyncio
async def test_w1_real_pilot_emits_wave_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.delenv("BMAD_REQUIRE_SANDBOX", raising=False)
    target = _make_target_with_stories(tmp_path, "s1")
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target))

    monkeypatch.setattr(
        run_module, "runtime_spawn_worker", _make_fake_spawn(tmp_path / "jsonl"),
    )

    bus = EventLoop()
    budget = BudgetGuard(load_settings().budget, event_loop=bus)
    events = _record_events(bus)
    await _run_real_pilot(
        bus, project="proj", wave="w", max_parallel=1, max_stories=1,
        max_spend_usd=50.0, budget=budget, state_db=None, session_id=None,
        models=ModelConfig(), options={}, settings=load_settings(),
    )

    await _drain(bus)
    boundaries = [e for e in events if e.type == EventType.WAVE_BOUNDARY_REACHED]
    assert len(boundaries) == 1
    assert boundaries[0].payload.get("wave") == "w"


@pytest.mark.asyncio
async def test_w1_real_pilot_starts_backstop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """``bus.start_backstop_task()`` must be invoked in real pilot."""
    monkeypatch.delenv("BMAD_REQUIRE_SANDBOX", raising=False)
    target = _make_target_with_stories(tmp_path)
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target))

    bus = EventLoop()
    called = {"backstop": False}

    original = bus.start_backstop_task

    def _spy() -> Any:
        called["backstop"] = True
        return original()

    bus.start_backstop_task = _spy  # type: ignore[method-assign]

    budget = BudgetGuard(load_settings().budget, event_loop=bus)
    await _run_real_pilot(
        bus, project="proj", wave="w", max_parallel=1, max_stories=1,
        max_spend_usd=50.0, budget=budget, state_db=None, session_id=None,
        models=ModelConfig(), options={}, settings=load_settings(),
    )
    await bus.stop()
    assert called["backstop"] is True


# ── max_stories cap ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_w1_max_stories_caps_at_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """5 ready stories, ``max_stories=1`` → only 1 spawn."""
    monkeypatch.delenv("BMAD_REQUIRE_SANDBOX", raising=False)
    target = _make_target_with_stories(tmp_path, "s1", "s2", "s3", "s4", "s5")
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target))

    spawn_count = {"n": 0}
    base_fake = _make_fake_spawn(tmp_path / "jsonl")

    async def _counting_fake(**kwargs: Any) -> WorkerHandle:
        spawn_count["n"] += 1
        return await base_fake(**kwargs)

    monkeypatch.setattr(run_module, "runtime_spawn_worker", _counting_fake)

    bus = EventLoop()
    budget = BudgetGuard(load_settings().budget, event_loop=bus)
    await _run_real_pilot(
        bus, project="proj", wave="w", max_parallel=3, max_stories=1,
        max_spend_usd=100.0, budget=budget, state_db=None, session_id=None,
        models=ModelConfig(), options={}, settings=load_settings(),
    )
    await bus.stop()
    assert spawn_count["n"] == 1


@pytest.mark.asyncio
async def test_w1_max_stories_two(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """5 ready stories, ``max_stories=2`` → exactly 2 spawns."""
    monkeypatch.delenv("BMAD_REQUIRE_SANDBOX", raising=False)
    target = _make_target_with_stories(tmp_path, "s1", "s2", "s3", "s4", "s5")
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target))

    spawn_count = {"n": 0}
    base_fake = _make_fake_spawn(tmp_path / "jsonl")

    async def _counting_fake(**kwargs: Any) -> WorkerHandle:
        spawn_count["n"] += 1
        return await base_fake(**kwargs)

    monkeypatch.setattr(run_module, "runtime_spawn_worker", _counting_fake)

    bus = EventLoop()
    budget = BudgetGuard(load_settings().budget, event_loop=bus)
    await _run_real_pilot(
        bus, project="proj", wave="w", max_parallel=3, max_stories=2,
        max_spend_usd=100.0, budget=budget, state_db=None, session_id=None,
        models=ModelConfig(), options={}, settings=load_settings(),
    )
    await bus.stop()
    assert spawn_count["n"] == 2


@pytest.mark.asyncio
async def test_w1_max_stories_zero_no_spawns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """``max_stories=0`` → 0 spawns even with ready stories."""
    monkeypatch.delenv("BMAD_REQUIRE_SANDBOX", raising=False)
    target = _make_target_with_stories(tmp_path, "s1", "s2")
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target))

    spawn_count = {"n": 0}

    async def _counting_fake(**kwargs: Any) -> WorkerHandle:
        spawn_count["n"] += 1
        return WorkerHandle(
            worktree=kwargs["worktree"], story_id=kwargs["story_id"],
            branch=kwargs["branch"], pid=1, jsonl_path=tmp_path / "x.jsonl",
            process=None, mock=False, sandbox_kind="bwrap",
        )

    monkeypatch.setattr(run_module, "runtime_spawn_worker", _counting_fake)

    bus = EventLoop()
    budget = BudgetGuard(load_settings().budget, event_loop=bus)
    await _run_real_pilot(
        bus, project="proj", wave="w", max_parallel=3, max_stories=0,
        max_spend_usd=100.0, budget=budget, state_db=None, session_id=None,
        models=ModelConfig(), options={}, settings=load_settings(),
    )
    await bus.stop()
    assert spawn_count["n"] == 0


# ── max_spend_usd cap ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_w1_max_spend_usd_halts_pilot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Tiny ``max_spend_usd`` → halt before first spawn (reserve > cap)."""
    monkeypatch.delenv("BMAD_REQUIRE_SANDBOX", raising=False)
    # S6 (#6 P2) — subscription auto-disable would otherwise skip $-gates.
    # The W1 cap test asserts on a $-gate firing, so pin auth=API-key.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-w1-cap")
    target = _make_target_with_stories(tmp_path, "s1", "s2", "s3")
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target))

    spawn_count = {"n": 0}

    async def _counting_fake(**kwargs: Any) -> WorkerHandle:
        spawn_count["n"] += 1
        jsonl = tmp_path / f"{kwargs['story_id']}.jsonl"
        _seed_jsonl_completed(jsonl, kwargs["story_id"])
        return WorkerHandle(
            worktree=kwargs["worktree"], story_id=kwargs["story_id"],
            branch=kwargs["branch"], pid=1, jsonl_path=jsonl,
            process=None, mock=False, sandbox_kind="bwrap",
        )

    monkeypatch.setattr(run_module, "runtime_spawn_worker", _counting_fake)

    bus = EventLoop()
    budget = BudgetGuard(load_settings().budget, event_loop=bus)
    # story_alarm_usd default 30 → reserve = 30/6 = $5/story.
    # max_spend_usd=1.0 → projected_daily (0+5=5) > 1 → halt immediately.
    await _run_real_pilot(
        bus, project="proj", wave="w", max_parallel=3, max_stories=10,
        max_spend_usd=1.0, budget=budget, state_db=None, session_id=None,
        models=ModelConfig(), options={}, settings=load_settings(),
    )
    await bus.stop()
    assert spawn_count["n"] == 0


@pytest.mark.asyncio
async def test_w1_max_spend_usd_emits_budget_threshold_hit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The halt path emits ``BUDGET_THRESHOLD_HIT`` with reason=max_spend_usd_cap."""
    monkeypatch.delenv("BMAD_REQUIRE_SANDBOX", raising=False)
    # S6 (#6 P2) — see sibling test for rationale.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-w1-emit")
    target = _make_target_with_stories(tmp_path, "s1")
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target))

    monkeypatch.setattr(
        run_module, "runtime_spawn_worker", _make_fake_spawn(tmp_path / "jsonl"),
    )

    bus = EventLoop()
    budget = BudgetGuard(load_settings().budget, event_loop=bus)
    events = _record_events(bus)
    await _run_real_pilot(
        bus, project="proj", wave="w", max_parallel=1, max_stories=10,
        max_spend_usd=1.0, budget=budget, state_db=None, session_id=None,
        models=ModelConfig(), options={}, settings=load_settings(),
    )

    await _drain(bus)
    halts = [e for e in events if e.type == EventType.BUDGET_THRESHOLD_HIT]
    assert any(
        e.payload.get("reason") == "max_spend_usd_cap" for e in halts
    ), f"expected max_spend_usd_cap halt event, got {[e.payload for e in halts]!r}"


@pytest.mark.asyncio
async def test_w1_max_spend_usd_default_50_allows_progress(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """``max_spend_usd=50`` (default) — small DAG completes without budget halt."""
    monkeypatch.delenv("BMAD_REQUIRE_SANDBOX", raising=False)
    target = _make_target_with_stories(tmp_path, "s1", "s2")
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target))

    monkeypatch.setattr(
        run_module, "runtime_spawn_worker", _make_fake_spawn(tmp_path / "jsonl"),
    )

    bus = EventLoop()
    budget = BudgetGuard(load_settings().budget, event_loop=bus)
    await _run_real_pilot(
        bus, project="proj", wave="w", max_parallel=2, max_stories=10,
        max_spend_usd=50.0, budget=budget, state_db=None, session_id=None,
        models=ModelConfig(), options={}, settings=load_settings(),
    )

    events = await _drain(bus)
    halts = [
        e for e in events
        if e.type == EventType.BUDGET_THRESHOLD_HIT
        and e.payload.get("reason") == "max_spend_usd_cap"
    ]
    assert halts == []


# ── Mock path regression ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_w1_mock_path_still_works(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Default ``mock=True`` path unchanged — no NotImplementedError, no real spawn."""
    target = _make_target_with_stories(tmp_path, "s1")
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target))

    bus = EventLoop()
    await run_orchestrator(
        project="proj", wave="w", max_parallel=1, mock=True, event_loop=bus,
    )
    # Smoke: bus has emitted at least one event.
    events = await _drain(bus)
    assert any(e.type == EventType.WAVE_BOUNDARY_REACHED for e in events)


def test_w1_run_orchestrator_default_mock_true_preserved() -> None:
    """N3 invariant: default ``mock=True`` survives W1."""
    sig = inspect.signature(run_orchestrator)
    assert sig.parameters["mock"].default is True


def test_w1_cli_default_mock_true_preserved() -> None:
    """N3 invariant: CLI default ``--mock`` survives W1."""
    sig = inspect.signature(cli_main.run)
    assert sig.parameters["mock"].default.default is True


# ── Source-grep validations ──────────────────────────────────────────────────


def test_w1_run_real_pilot_source_calls_detect_sandbox() -> None:
    """``_run_real_pilot`` body must invoke ``detect_sandbox()`` at entry."""
    src = inspect.getsource(_run_real_pilot)
    assert "detect_sandbox()" in src


def test_w1_run_real_pilot_source_references_caps() -> None:
    src = inspect.getsource(_run_real_pilot)
    assert "max_stories" in src
    assert "max_spend_usd" in src


def test_w1_tail_helper_bridges_to_bus() -> None:
    """``_tail_and_emit_completion`` must call ``tail_jsonl_events``."""
    src = inspect.getsource(_tail_and_emit_completion)
    assert "tail_jsonl_events" in src
    assert "WORKER_COMPLETED" in src


# ── CLI propagation ──────────────────────────────────────────────────────────


def test_w1_cli_daemon_args_include_caps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--daemon`` mode must propagate ``--max-stories`` / ``--max-spend-usd``."""
    captured: dict[str, Any] = {}

    class _FakePopen:
        pid = 999

        def __init__(self, args: list[str], **kwargs: Any) -> None:
            captured["args"] = args

    monkeypatch.setattr(cli_main.subprocess, "Popen", _FakePopen)

    # NEW-1 — `--project` is now resolved strictly through the registry; an
    # unregistered slug fails loud. Register a project so the daemon path runs.
    from bmad_orchestrator.runtime.project_registry import (
        REGISTRY_ENV_VAR,
        ProjectEntry,
        ProjectsRegistry,
        save_registry,
    )

    reg_file = tmp_path / "projects.yaml"
    save_registry(
        ProjectsRegistry(
            projects={"x": ProjectEntry(path=tmp_path / "x", bmad_layout="bmm-v6")}
        ),
        reg_file,
    )
    monkeypatch.setenv(REGISTRY_ENV_VAR, str(reg_file))

    runner = CliRunner()
    result = runner.invoke(cli_main.app, [
        "run", "--project", "x", "--wave", "1a",
        "--daemon", "--max-stories", "7", "--max-spend-usd", "2.5",
    ])
    assert result.exit_code == 0, result.stdout
    assert "--max-stories" in captured["args"]
    assert "7" in captured["args"]
    assert "--max-spend-usd" in captured["args"]
    assert "2.5" in captured["args"]
