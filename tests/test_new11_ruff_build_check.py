"""NEW-11 (pilot_findings_closure_v5 S1) — ruff build_check graceful path.

Spec: spec/spec_pilot_findings_closure_v5.md §1.

A target BMad project without a ruff configuration makes ruff fall back to
its built-in defaults — usually stricter than the project intends — so
``ruff check`` exits 1 and ``build_check_subscriber`` halts the story before
merge. The fix: a ``skip_if_no_ruff_config`` flag on the ruff build-check
command. When set and the worktree has no ruff config, ruff is skipped
gracefully (exit 0 + audit log) instead of halting. When a config IS
present, ruff runs normally and real lint violations still halt.

Coverage (5 tests):
  * 3 unit  — config detection (toml files + pyproject table), ruff-command
              recognition.
  * 2 integration — subscriber does NOT halt on a config-less worktree;
              subscriber DOES halt on a real lint violation when config
              is present.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bmad_orchestrator.runtime.build_check import (
    BuildCheckCommand,
    _is_ruff_command,
    _worktree_has_ruff_config,
    build_check_subscriber,
)
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType

# ─── unit — config detection ──────────────────────────────────────────────────


def test_detects_ruff_toml_files(tmp_path: Path) -> None:
    assert not _worktree_has_ruff_config(tmp_path)
    (tmp_path / "ruff.toml").write_text("line-length = 100\n", encoding="utf-8")
    assert _worktree_has_ruff_config(tmp_path)

    other = tmp_path / "dotted"
    other.mkdir()
    (other / ".ruff.toml").write_text("line-length = 88\n", encoding="utf-8")
    assert _worktree_has_ruff_config(other)


def test_detects_ruff_table_in_pyproject(tmp_path: Path) -> None:
    no_ruff = tmp_path / "no_ruff"
    no_ruff.mkdir()
    (no_ruff / "pyproject.toml").write_text(
        "[tool.black]\nline-length = 88\n", encoding="utf-8"
    )
    assert not _worktree_has_ruff_config(no_ruff)

    with_ruff = tmp_path / "with_ruff"
    with_ruff.mkdir()
    (with_ruff / "pyproject.toml").write_text(
        "[tool.ruff]\nline-length = 100\n", encoding="utf-8"
    )
    assert _worktree_has_ruff_config(with_ruff)

    sub_table = tmp_path / "sub"
    sub_table.mkdir()
    (sub_table / "pyproject.toml").write_text(
        "[tool.ruff.lint]\nselect = [\"E\"]\n", encoding="utf-8"
    )
    assert _worktree_has_ruff_config(sub_table)


def test_is_ruff_command_recognises_executable() -> None:
    ruff_cmd = BuildCheckCommand(name="ruff", run="ruff check src tests")
    pytest_cmd = BuildCheckCommand(name="pytest", run="pytest tests/ -q")
    assert _is_ruff_command(ruff_cmd)
    assert not _is_ruff_command(pytest_cmd)


# ─── integration — subscriber halt behaviour ──────────────────────────────────


def _write_policy(path: Path, *, run: str, skip_flag: bool) -> Path:
    flag = "true" if skip_flag else "false"
    path.write_text(
        "commands:\n"
        f"  - {{name: ruff, run: \"{run}\", required: true, "
        f"skip_if_no_ruff_config: {flag}}}\n"
        "timeout_sec: 60\n"
        "tail_lines: 20\n"
        "skip_if_missing_executable: true\n"
        "escalation_text: test\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.asyncio
async def test_no_ruff_config_worktree_does_not_halt(tmp_path: Path) -> None:
    """Worktree without any ruff config → ruff command skipped, status stays
    ``success``, no HUMAN_QUERY emitted."""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    # A file ruff WOULD flag (unused import) — proves the skip, not a clean
    # tree, is what kept the build green.
    (worktree / "bad.py").write_text("import os\n", encoding="utf-8")

    policy = _write_policy(
        tmp_path / "policy.yaml", run="ruff check .", skip_flag=True
    )
    bus = EventLoop()
    event = Event(
        type=EventType.WORKER_COMPLETED,
        payload={"status": "success", "worktree": str(worktree), "story_id": "s1"},
    )
    await build_check_subscriber(event, bus, policy_path=policy)

    assert event.payload["status"] == "success"
    assert "halt_reason" not in event.payload
    assert await bus.dispatch_one(timeout=0.05) is None


@pytest.mark.asyncio
async def test_ruff_config_present_real_violation_halts(tmp_path: Path) -> None:
    """Worktree WITH a ruff config → ruff runs; a real F401 violation halts
    the story (skip flag does not suppress a legitimate failure)."""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / "ruff.toml").write_text("line-length = 100\n", encoding="utf-8")
    (worktree / "bad.py").write_text("import os\n", encoding="utf-8")

    policy = _write_policy(
        tmp_path / "policy.yaml", run="ruff check bad.py", skip_flag=True
    )
    bus = EventLoop()
    event = Event(
        type=EventType.WORKER_COMPLETED,
        payload={"status": "success", "worktree": str(worktree), "story_id": "s2"},
    )
    await build_check_subscriber(event, bus, policy_path=policy)

    assert event.payload["status"] == "halted_build_check_failed"
    assert event.payload["halt_reason"] == "build_check"
    human = await bus.dispatch_one(timeout=0.05)
    assert human is not None
    assert human.type == EventType.HUMAN_QUERY
