"""Regression tests for eval/runner.py — sandbox_network propagation.

Background: Step B attempt #2 2026-05-20 failed 0/10 because eval/runner.py
called spawn_worker() without sandbox_network. Default "none" triggers bwrap
--unshare-net which blocks api.anthropic.com → workers got ConnectionRefused
without ever making an LLM call. Wave 1a worked because agent/run.py callsites
explicitly set sandbox_network="full". These tests guard against re-regression.

Q-26140-a1b2 fix: real-mode → "full"; mock-mode stays "none".
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from bmad_orchestrator.eval import runner as runner_mod


class _FakeHandle:
    """Minimal stand-in for WorkerHandle — enough for _run_one_case to error
    out cleanly after spawn_worker capture. We don't exercise the JSONL tail
    path here (covered by other tests); we only need the spawn call kwargs."""

    def __init__(self, jsonl_path: Path) -> None:
        self.jsonl_path = jsonl_path


async def _empty_event_stream(_path: Path):
    """Async generator stub that yields nothing — terminates the JSONL tail loop."""
    if False:
        yield {}
    return


@pytest.fixture
def _capture_spawn(monkeypatch, tmp_path):
    """Replace spawn_worker with a capture stub; return the captured-kwargs
    dict so each test can assert on it."""
    captured: dict[str, Any] = {}

    async def fake_spawn(**kwargs):
        captured.update(kwargs)
        jsonl = tmp_path / "fake.events.jsonl"
        jsonl.write_text("", encoding="utf-8")
        return _FakeHandle(jsonl)

    monkeypatch.setattr(runner_mod, "spawn_worker", fake_spawn)
    monkeypatch.setattr(runner_mod, "tail_jsonl_events", _empty_event_stream)
    return captured


def _case_fixture() -> dict[str, Any]:
    return {
        "id": "T-REG-001",
        "level": "easy",
        "story_id": "regression-1",
        "story_path": "regression-1.md",
        "expected": {"final_verdict": "approve"},
    }


@pytest.mark.asyncio
async def test_real_mode_propagates_sandbox_network_full(
    tmp_path: Path, _capture_spawn: dict[str, Any]
) -> None:
    """Real-mode workers MUST get sandbox_network='full' or bwrap --unshare-net
    blocks api.anthropic.com (Step B 2026-05-20 incident)."""
    worktree_root = tmp_path / ".worktrees-eval"
    worktree_root.mkdir()

    await runner_mod._run_one_case(
        _case_fixture(),
        evals_root=tmp_path,
        worktree_root=worktree_root,
        mode="real",
    )

    assert _capture_spawn.get("sandbox_network") == "full", (
        f"real-mode must pass sandbox_network='full' to spawn_worker; got "
        f"{_capture_spawn.get('sandbox_network')!r}. Without this bwrap "
        f"--unshare-net blocks LLM API calls."
    )


@pytest.mark.asyncio
async def test_mock_mode_keeps_sandbox_network_none(
    tmp_path: Path, _capture_spawn: dict[str, Any]
) -> None:
    """Mock-mode does NOT call real Claude API → keep the secure default
    'none' (bwrap --unshare-net) to preserve isolation invariant."""
    worktree_root = tmp_path / ".worktrees-eval"
    worktree_root.mkdir()

    await runner_mod._run_one_case(
        _case_fixture(),
        evals_root=tmp_path,
        worktree_root=worktree_root,
        mode="mock",
    )

    assert _capture_spawn.get("sandbox_network") == "none", (
        f"mock-mode must keep sandbox_network='none'; got "
        f"{_capture_spawn.get('sandbox_network')!r}"
    )
