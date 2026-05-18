"""Tests for review_iteration wiring (P5 Evaluator-Optimizer loop completion).

Worker → orchestrator surface:
    bmad-auto-dev runner.sh writes per-story retry counts in
    ``<worktree>/_bmad/auto-dev-state/current-batch.json``. The
    ``_read_review_iteration`` helper translates ``retries[story_id]`` to
    ``review_iteration = retry_count + 1`` and ``_wait_and_finalize`` surfaces
    the value into the ``worker_completed`` JSONL event. Bridge in
    ``agent/run.py`` forwards the field to the bus payload so
    ``code_review_subscriber`` and ``_gate_iteration_cap`` can act on it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bmad_orchestrator.runtime.worker_spawn import _read_review_iteration

# ── _read_review_iteration helper ──────────────────────────────────────────


def _write_state(worktree: Path, retries: dict[str, int]) -> None:
    state_dir = worktree / "_bmad" / "auto-dev-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "current-batch.json").write_text(
        json.dumps({"retries": retries}), encoding="utf-8"
    )


def test_read_review_iteration_missing_file_returns_1(tmp_path: Path):
    assert _read_review_iteration(str(tmp_path), "1.1") == 1


def test_read_review_iteration_retry_zero_returns_1(tmp_path: Path):
    _write_state(tmp_path, {"1.1": 0})
    assert _read_review_iteration(str(tmp_path), "1.1") == 1


def test_read_review_iteration_retry_one_returns_2(tmp_path: Path):
    _write_state(tmp_path, {"1.1": 1})
    assert _read_review_iteration(str(tmp_path), "1.1") == 2


def test_read_review_iteration_retry_three_returns_4(tmp_path: Path):
    _write_state(tmp_path, {"epic-3/story-7": 3})
    assert _read_review_iteration(str(tmp_path), "epic-3/story-7") == 4


def test_read_review_iteration_other_story_returns_1(tmp_path: Path):
    _write_state(tmp_path, {"1.1": 5})
    # Different story id → not found → default 1
    assert _read_review_iteration(str(tmp_path), "2.2") == 1


def test_read_review_iteration_malformed_json_returns_1(tmp_path: Path):
    state_dir = tmp_path / "_bmad" / "auto-dev-state"
    state_dir.mkdir(parents=True)
    (state_dir / "current-batch.json").write_text("{not valid json", encoding="utf-8")
    assert _read_review_iteration(str(tmp_path), "1.1") == 1


def test_read_review_iteration_root_not_object_returns_1(tmp_path: Path):
    state_dir = tmp_path / "_bmad" / "auto-dev-state"
    state_dir.mkdir(parents=True)
    (state_dir / "current-batch.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert _read_review_iteration(str(tmp_path), "1.1") == 1


def test_read_review_iteration_retries_not_object_returns_1(tmp_path: Path):
    state_dir = tmp_path / "_bmad" / "auto-dev-state"
    state_dir.mkdir(parents=True)
    (state_dir / "current-batch.json").write_text(
        '{"retries": "not-a-dict"}', encoding="utf-8"
    )
    assert _read_review_iteration(str(tmp_path), "1.1") == 1


def test_read_review_iteration_non_int_value_returns_1(tmp_path: Path):
    state_dir = tmp_path / "_bmad" / "auto-dev-state"
    state_dir.mkdir(parents=True)
    (state_dir / "current-batch.json").write_text(
        '{"retries": {"1.1": "two"}}', encoding="utf-8"
    )
    assert _read_review_iteration(str(tmp_path), "1.1") == 1


# ── worker_completed event surface ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_mock_worker_emits_review_iteration_default(tmp_path: Path, monkeypatch):
    """Mock-mode worker_completed JSONL row includes review_iteration=1 by default."""
    from bmad_orchestrator.runtime import worker_spawn

    monkeypatch.setattr(worker_spawn, "_resolve_claude_bin", lambda: None)

    worktree = tmp_path / "wt"
    worktree.mkdir()

    handle = await worker_spawn.spawn_worker(
        worktree=str(worktree),
        story_id="1.1",
        branch="feature/1.1",
        mock=True,
    )

    lines = [
        json.loads(line)
        for line in Path(handle.jsonl_path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    completed = next(r for r in lines if r.get("event_type") == "worker_completed")
    assert completed["review_iteration"] == 1


@pytest.mark.asyncio
async def test_mock_worker_emits_review_iteration_from_state(tmp_path: Path, monkeypatch):
    """When the worker's state file holds a retry count, it surfaces verbatim."""
    from bmad_orchestrator.runtime import worker_spawn

    monkeypatch.setattr(worker_spawn, "_resolve_claude_bin", lambda: None)

    worktree = tmp_path / "wt"
    worktree.mkdir()
    _write_state(worktree, {"1.1": 2})

    handle = await worker_spawn.spawn_worker(
        worktree=str(worktree),
        story_id="1.1",
        branch="feature/1.1",
        mock=True,
    )
    lines = [
        json.loads(line)
        for line in Path(handle.jsonl_path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    completed = next(r for r in lines if r.get("event_type") == "worker_completed")
    assert completed["review_iteration"] == 3
