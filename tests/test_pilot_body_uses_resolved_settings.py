"""NEW-1-completion — pilot bodies must use the resolved ``Settings`` passed
down from ``run_orchestrator`` instead of re-reading ``load_settings()``.

Regression source: ``spec/spec_pilot_findings_closure_v3.md`` §1 #2. The v2
fix made ``run_orchestrator`` accept a pre-resolved ``Settings`` (registry
``--project`` beats ``ORCHESTRATOR_TARGET_PROJECT``), but ``_run_mock_pilot``,
``_run_real_pilot`` and ``_run_real_pilot_body`` still called
``load_settings()`` unconditionally — so worktrees landed in the env-named
project instead of the ``--project`` one.

Coverage:
- ``_run_mock_pilot`` derives ``worktree_root`` from the passed ``settings``,
  not from ``ORCHESTRATOR_TARGET_PROJECT`` in env.
- ``_run_real_pilot`` / ``_run_real_pilot_body`` — same, behaviourally.
- ``settings`` is a required keyword-only arg (no default → TypeError) and the
  pilot-call-chain no longer references ``load_settings()`` in its bodies.
- Integration: ``run_orchestrator(settings=...)`` end-to-end mock honours the
  passed project for worktree paths.
"""

from __future__ import annotations

import inspect
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from bmad_orchestrator.agent import run as run_module
from bmad_orchestrator.agent.run import (
    _run_mock_pilot,
    _run_real_pilot,
    _run_real_pilot_body,
    run_orchestrator,
)
from bmad_orchestrator.agent.safety.budget_guard import BudgetGuard
from bmad_orchestrator.config import ModelConfig, Settings, load_settings
from bmad_orchestrator.runtime.event_loop import EventLoop
from bmad_orchestrator.runtime.worker_spawn import WorkerHandle

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_target(tmp_path: Path, name: str, *story_ids: str) -> Path:
    """Minimal git-backed target project with N ready-for-dev stories."""
    target = tmp_path / name
    target.mkdir(exist_ok=True)
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
        "wave: w\nepics:\n  e1:\n    stories:\n" + (stories_block + "\n" if stories_block else ""),
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


def _capturing_spawn(captured: list[str]) -> Any:
    """Fake ``runtime_spawn_worker`` that records the ``worktree`` it is given."""

    async def _fake_spawn(
        *, worktree: str, story_id: str, branch: str, **_: Any
    ) -> WorkerHandle:
        captured.append(worktree)
        jsonl_path = Path(worktree) / f"{story_id}.jsonl"
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        jsonl_path.write_text(
            json.dumps(
                {
                    "event_type": "worker_completed",
                    "story_id": story_id,
                    "exit_code": 0,
                    "status": "success",
                    "ts": "2026-05-19T00:00:00+00:00",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return WorkerHandle(
            worktree=worktree,
            story_id=story_id,
            branch=branch,
            pid=4242,
            jsonl_path=jsonl_path,
            process=None,
            mock=False,
            sandbox_kind="bwrap",
        )

    return _fake_spawn


# ── 1. _run_mock_pilot honours passed settings ───────────────────────────────


@pytest.mark.asyncio
async def test_mock_pilot_uses_passed_settings_not_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """``_run_mock_pilot`` worktree path follows ``settings.target_project``
    even when ``ORCHESTRATOR_TARGET_PROJECT`` env names a different project."""
    target_resolved = _make_target(tmp_path, "resolved")  # the --project answer
    target_env = _make_target(tmp_path, "envproj", "s1")  # the env decoy + DAG
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target_env))

    captured: list[str] = []
    monkeypatch.setattr(run_module, "runtime_spawn_worker", _capturing_spawn(captured))

    bus = EventLoop()
    budget = BudgetGuard(load_settings().budget, event_loop=bus)
    settings = Settings(target_project=target_resolved)
    try:
        await _run_mock_pilot(
            bus, wave="w", max_parallel=1, budget=budget, settings=settings
        )
    finally:
        await bus.stop()

    assert captured, "expected at least one spawn"
    assert all(wt.startswith(str(target_resolved)) for wt in captured), (
        f"worktrees must sit under the resolved project {target_resolved}, "
        f"got {captured!r}"
    )
    assert not (target_env / ".worktrees").exists(), (
        "env-named project must not receive worktrees"
    )


# ── 2. _run_real_pilot / _run_real_pilot_body honour passed settings ──────────


@pytest.mark.asyncio
async def test_real_pilot_body_uses_passed_settings_not_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """``_run_real_pilot`` → ``_run_real_pilot_body`` derives ``worktree_root``
    from the passed ``settings``, not from env."""
    monkeypatch.delenv("BMAD_REQUIRE_SANDBOX", raising=False)
    target_resolved = _make_target(tmp_path, "resolved")
    target_env = _make_target(tmp_path, "envproj", "s1")
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target_env))

    captured: list[str] = []
    monkeypatch.setattr(run_module, "runtime_spawn_worker", _capturing_spawn(captured))

    bus = EventLoop()
    budget = BudgetGuard(load_settings().budget, event_loop=bus)
    settings = Settings(target_project=target_resolved)
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
            settings=settings,
        )
    finally:
        await bus.stop()

    assert captured, "expected at least one spawn"
    assert all(wt.startswith(str(target_resolved)) for wt in captured), (
        f"real-pilot worktrees must sit under {target_resolved}, got {captured!r}"
    )


# ── 3. settings is required + no load_settings() in pilot bodies ──────────────


def test_settings_is_required_keyword_arg() -> None:
    """``settings`` must be a required (no-default) param on all three pilot
    entrypoints — a caller that forgets it gets a TypeError, not a silent
    env re-read."""
    for fn in (_run_mock_pilot, _run_real_pilot, _run_real_pilot_body):
        param = inspect.signature(fn).parameters.get("settings")
        assert param is not None, f"{fn.__name__} missing 'settings' param"
        assert param.default is inspect.Parameter.empty, (
            f"{fn.__name__}.settings must have no default (required)"
        )


def test_pilot_bodies_do_not_call_load_settings() -> None:
    """The pilot-call-chain bodies must not call ``load_settings()`` — that is
    exactly the env-override bug NEW-1-completion closes."""
    for fn in (_run_mock_pilot, _run_real_pilot, _run_real_pilot_body):
        src = inspect.getsource(fn)
        assert "load_settings()" not in src, (
            f"{fn.__name__} still calls load_settings() — env can override "
            f"the resolved --project"
        )


# ── 4. integration — run_orchestrator threads settings end-to-end ─────────────


@pytest.mark.asyncio
async def test_run_orchestrator_threads_settings_to_mock_pilot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """``run_orchestrator(settings=...)`` mock run → worktrees land under the
    passed project, not under ``ORCHESTRATOR_TARGET_PROJECT``."""
    target_resolved = _make_target(tmp_path, "resolved")
    target_env = _make_target(tmp_path, "envproj", "s1")
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target_env))

    captured: list[str] = []
    monkeypatch.setattr(run_module, "runtime_spawn_worker", _capturing_spawn(captured))

    async def _no_session(**_: Any) -> tuple[None, None]:
        return None, None

    monkeypatch.setattr(run_module, "_resolve_session", _no_session)

    settings = Settings(
        target_project=target_resolved, orchestrator_home=tmp_path
    )
    bus = await run_orchestrator(
        project="proj", wave="w", max_parallel=1, mock=True, settings=settings
    )
    await bus.stop()

    assert captured, "expected at least one spawn"
    assert all(wt.startswith(str(target_resolved)) for wt in captured), (
        f"run_orchestrator must thread settings to the pilot body; "
        f"worktrees got {captured!r}"
    )
