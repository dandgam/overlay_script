"""P2 regression tests — canonical_patches_port (Patch N — build check guard).

Spec: spec/spec_canonical_patches_port.md §P2.
Tracker: .claude/initiative-tracker-canonical_patches_port.md.

Coverage:

* **Patch N policy** (5) — ``load_build_check_policy`` happy path, missing
  file, malformed YAML, non-mapping top-level, schema violation.
* **Patch N command runner** (6) — clean exit, non-zero exit, timeout,
  missing executable skip, missing executable required-failure (no skip),
  stderr captured into tail.
* **Patch N subscriber** (6) — wired before deletion_safety in
  ``_run_real_pilot`` (order asserted); halts WORKER_COMPLETED on required
  failure (status mutated to ``halted_build_check_failed``, HUMAN_QUERY
  emitted); no-op on success with clean commands; no-op on non-success
  status; no-op on non-WORKER_COMPLETED event types; optional-command
  failure does NOT halt; empty policy is a no-op.
* **Patch N bundled policy** (1) — on-disk YAML carries pytest + ruff
  baseline, validates clean.
"""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

import pytest

from bmad_orchestrator.agent.run import _run_real_pilot
from bmad_orchestrator.agent.safety.budget_guard import BudgetGuard
from bmad_orchestrator.config import ModelConfig, load_settings
from bmad_orchestrator.runtime.build_check import (
    BUILD_CHECK_POLICY_PATH_DEFAULT,
    BuildCheckCommand,
    BuildCheckPolicy,
    BuildCheckResult,
    _run_command,
    build_check_subscriber,
    load_build_check_policy,
)
from bmad_orchestrator.runtime.deletion_safety import deletion_safety_subscriber
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.skills_repo import PolicyInvalidError, PolicyNotFoundError

# ───────────────────── Patch N — policy load tests ───────────────────────────


def _write_policy(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_load_build_check_policy_happy_path(tmp_path: Path) -> None:
    p = _write_policy(
        tmp_path / "bc.yaml",
        "commands:\n"
        "  - {name: a, run: 'true', required: true}\n"
        "  - {name: b, run: 'false', required: false}\n"
        "timeout_sec: 30\n"
        "tail_lines: 5\n"
        "escalation_text: hi\n",
    )
    policy = load_build_check_policy(p)
    assert [c.name for c in policy.commands] == ["a", "b"]
    assert policy.commands[0].required is True
    assert policy.commands[1].required is False
    assert policy.timeout_sec == 30
    assert policy.tail_lines == 5
    assert policy.escalation_text == "hi"


def test_load_build_check_policy_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(PolicyNotFoundError):
        load_build_check_policy(tmp_path / "absent.yaml")


def test_load_build_check_policy_malformed_yaml(tmp_path: Path) -> None:
    p = _write_policy(tmp_path / "bad.yaml", "commands: [unclosed\n")
    with pytest.raises(PolicyInvalidError):
        load_build_check_policy(p)


def test_load_build_check_policy_non_mapping_top(tmp_path: Path) -> None:
    p = _write_policy(tmp_path / "list.yaml", "- a\n- b\n")
    with pytest.raises(PolicyInvalidError):
        load_build_check_policy(p)


def test_load_build_check_policy_schema_violation(tmp_path: Path) -> None:
    # ``timeout_sec`` declared as int — string is rejected by pydantic strict.
    p = _write_policy(
        tmp_path / "schema.yaml",
        "commands:\n  - {name: x, run: 'true'}\ntimeout_sec: not-a-number\n",
    )
    with pytest.raises(PolicyInvalidError):
        load_build_check_policy(p)


# ─────────────────── Patch N — _run_command behaviour ────────────────────────


@pytest.mark.asyncio
async def test_run_command_clean_exit(tmp_path: Path) -> None:
    res = await _run_command(
        BuildCheckCommand(name="echo", run="echo hello"),
        worktree=tmp_path,
        timeout_sec=10,
        tail_lines=10,
        skip_if_missing_executable=False,
    )
    assert res.exit_code == 0
    assert "hello" in res.tail
    assert res.timed_out is False
    assert res.skipped_missing_executable is False


@pytest.mark.asyncio
async def test_run_command_non_zero_exit_captured(tmp_path: Path) -> None:
    res = await _run_command(
        BuildCheckCommand(name="false", run="false"),
        worktree=tmp_path,
        timeout_sec=10,
        tail_lines=10,
        skip_if_missing_executable=False,
    )
    assert res.exit_code != 0
    assert res.timed_out is False


@pytest.mark.asyncio
async def test_run_command_timeout(tmp_path: Path) -> None:
    res = await _run_command(
        BuildCheckCommand(name="sleep", run="sleep 5"),
        worktree=tmp_path,
        timeout_sec=1,
        tail_lines=10,
        skip_if_missing_executable=False,
    )
    assert res.timed_out is True
    assert res.exit_code == 124
    assert "timed out" in res.tail


@pytest.mark.asyncio
async def test_run_command_skip_if_missing_executable(tmp_path: Path) -> None:
    res = await _run_command(
        BuildCheckCommand(
            name="cargo", run="cargo-totally-absent-binary-xyz --workspace"
        ),
        worktree=tmp_path,
        timeout_sec=10,
        tail_lines=10,
        skip_if_missing_executable=True,
    )
    assert res.skipped_missing_executable is True
    assert res.exit_code == 0


@pytest.mark.asyncio
async def test_run_command_missing_executable_without_skip(tmp_path: Path) -> None:
    # ``skip_if_missing_executable=False`` → /bin/sh handles the missing
    # binary itself; shells return 127 for not-found.
    res = await _run_command(
        BuildCheckCommand(
            name="ghost", run="this-binary-does-not-exist-12345 arg"
        ),
        worktree=tmp_path,
        timeout_sec=10,
        tail_lines=10,
        skip_if_missing_executable=False,
    )
    assert res.skipped_missing_executable is False
    assert res.exit_code != 0


@pytest.mark.asyncio
async def test_run_command_captures_stderr_into_tail(tmp_path: Path) -> None:
    res = await _run_command(
        BuildCheckCommand(name="stderr", run="echo 'failmsg' >&2; exit 1"),
        worktree=tmp_path,
        timeout_sec=10,
        tail_lines=10,
        skip_if_missing_executable=False,
    )
    assert res.exit_code == 1
    assert "failmsg" in res.tail


# ───────────────── Patch N — subscriber wiring + behaviour ───────────────────


def _make_target_with_stories(tmp_path: Path) -> Path:
    target = tmp_path / "proj"
    target.mkdir(exist_ok=True)
    artifacts = target / "_bmad-output" / "planning-artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "sprint-status.yaml").write_text(
        "wave: w\nepics: {}\n", encoding="utf-8"
    )
    (artifacts / "stories").mkdir(exist_ok=True)
    return target


@pytest.mark.asyncio
async def test_build_check_wired_first_before_deletion_safety(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Patch N must register BEFORE Patch C so the cheap build guard halts
    before the deletion scan; Patch C still precedes code_review.

    After P3 (Patch S, 2026-05-18) stage5_completeness registers at index 0,
    so build_check is at index 1 and deletion_safety at index 2.
    """
    monkeypatch.delenv("BMAD_REQUIRE_SANDBOX", raising=False)
    target = _make_target_with_stories(tmp_path)
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target))

    bus = EventLoop()
    budget = BudgetGuard(load_settings().budget, event_loop=bus)
    try:
        await _run_real_pilot(
            bus, project="proj", wave="w", max_parallel=1, max_stories=1,
            max_spend_usd=10.0, budget=budget, state_db=None, session_id=None,
            models=ModelConfig(), options={},
        )
    finally:
        await bus.stop()

    funcs = [getattr(s, "func", s) for s in bus._subs]
    assert funcs[1] is build_check_subscriber
    assert funcs[2] is deletion_safety_subscriber


def _policy_with(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "build-check.yaml"
    p.write_text(body, encoding="utf-8")
    return p


@pytest.mark.asyncio
async def test_build_check_halts_on_required_failure(tmp_path: Path) -> None:
    """Required command non-zero exit → status mutated, HUMAN_QUERY emitted."""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    policy = _policy_with(
        tmp_path,
        "commands:\n"
        "  - {name: must-fail, run: 'echo bad; exit 7', required: true}\n"
        "timeout_sec: 10\n"
        "escalation_text: 'Fix build.'\n",
    )
    bus = EventLoop()
    ev = Event(
        type=EventType.WORKER_COMPLETED,
        payload={"story_id": "s2", "worktree": str(worktree), "status": "success"},
    )
    await build_check_subscriber(ev, bus, policy_path=policy)

    assert ev.payload["status"] == "halted_build_check_failed"
    assert ev.payload["halt_reason"] == "build_check"
    assert ev.payload["failed_command"] == "must-fail"
    assert ev.payload["failed_command_exit"] == 7

    drained: list[Event] = []
    while True:
        nxt = await bus.next(timeout=0.01)
        if nxt is None:
            break
        drained.append(nxt)
    queries = [e for e in drained if e.type == EventType.HUMAN_QUERY]
    assert len(queries) == 1
    text = queries[0].payload["text"]
    assert "must-fail" in text
    assert "Fix build." in text
    assert "bad" in text  # tail captured from stdout


@pytest.mark.asyncio
async def test_build_check_noop_on_clean_run(tmp_path: Path) -> None:
    """All required commands exit 0 → payload untouched, no bus emit."""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    policy = _policy_with(
        tmp_path,
        "commands:\n"
        "  - {name: ok, run: 'true', required: true}\n"
        "timeout_sec: 10\n",
    )
    bus = EventLoop()
    ev = Event(
        type=EventType.WORKER_COMPLETED,
        payload={"story_id": "s2", "worktree": str(worktree), "status": "success"},
    )
    await build_check_subscriber(ev, bus, policy_path=policy)
    assert ev.payload["status"] == "success"
    assert "halt_reason" not in ev.payload
    assert await bus.next(timeout=0.01) is None


@pytest.mark.asyncio
async def test_build_check_noop_on_non_success_status(tmp_path: Path) -> None:
    """Pre-failed worker → subscriber must not even attempt the build."""
    policy = _policy_with(
        tmp_path,
        "commands:\n"
        "  - {name: should-not-run, run: 'echo NO; exit 1', required: true}\n",
    )
    bus = EventLoop()
    ev = Event(
        type=EventType.WORKER_COMPLETED,
        payload={"story_id": "s2", "worktree": "/anywhere", "status": "failed"},
    )
    await build_check_subscriber(ev, bus, policy_path=policy)
    assert ev.payload["status"] == "failed"
    assert "halt_reason" not in ev.payload
    assert await bus.next(timeout=0.01) is None


@pytest.mark.asyncio
async def test_build_check_noop_on_unrelated_event_type(tmp_path: Path) -> None:
    """Any event other than WORKER_COMPLETED → fast no-op."""
    policy = _policy_with(
        tmp_path,
        "commands:\n  - {name: x, run: 'exit 1', required: true}\n",
    )
    bus = EventLoop()
    ev = Event(
        type=EventType.WAVE_BOUNDARY_REACHED,
        payload={"completed_stories": 10},
    )
    await build_check_subscriber(ev, bus, policy_path=policy)
    assert ev.payload == {"completed_stories": 10}
    assert await bus.next(timeout=0.01) is None


@pytest.mark.asyncio
async def test_build_check_optional_failure_does_not_halt(tmp_path: Path) -> None:
    """A non-required command's non-zero exit logs but does NOT halt the
    event chain — the operator wants build guard to fire on hard breakers only."""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    policy = _policy_with(
        tmp_path,
        "commands:\n"
        "  - {name: advisory, run: 'false', required: false}\n"
        "  - {name: ok,       run: 'true',  required: true}\n",
    )
    bus = EventLoop()
    ev = Event(
        type=EventType.WORKER_COMPLETED,
        payload={"story_id": "s2", "worktree": str(worktree), "status": "success"},
    )
    await build_check_subscriber(ev, bus, policy_path=policy)
    assert ev.payload["status"] == "success"
    assert "halt_reason" not in ev.payload
    assert await bus.next(timeout=0.01) is None


@pytest.mark.asyncio
async def test_build_check_empty_policy_is_noop(tmp_path: Path) -> None:
    """An empty ``commands`` list → no-op (no shell invocation, no emit)."""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    policy = _policy_with(tmp_path, "commands: []\n")
    bus = EventLoop()
    ev = Event(
        type=EventType.WORKER_COMPLETED,
        payload={"story_id": "s2", "worktree": str(worktree), "status": "success"},
    )
    await build_check_subscriber(ev, bus, policy_path=policy)
    assert ev.payload["status"] == "success"
    assert await bus.next(timeout=0.01) is None


# ─────────────────── Bundled policy file sanity check ────────────────────────


def test_default_build_check_policy_loads_with_baseline_commands() -> None:
    """On-disk skills/policy/build-check.yaml validates and carries the
    pytest + ruff baseline (per spec §P2)."""
    policy = load_build_check_policy(BUILD_CHECK_POLICY_PATH_DEFAULT)
    names = [c.name for c in policy.commands]
    assert "pytest" in names
    assert "ruff" in names
    assert policy.timeout_sec >= 60
    assert policy.escalation_text.strip() != ""


# Module-import smoke: re-import build_check to assert no top-level side
# effects (the subscriber must be safe to register without an event bus).
def test_build_check_module_reimport_clean() -> None:
    importlib.reload(
        importlib.import_module("bmad_orchestrator.runtime.build_check")
    )


# Asyncio-loop reach: subscriber may be awaited from a non-running asyncio
# context inside a test (uses asyncio.create_subprocess_exec). This guards the
# happy-path coroutine signature against accidental sync conversion.
def test_subscriber_is_async_coroutine_function() -> None:
    assert asyncio.iscoroutinefunction(build_check_subscriber)


def test_build_check_result_dataclass_immutable() -> None:
    """``BuildCheckResult`` is a frozen dataclass — runtime tampering with
    captured exit codes / tails after collection must raise."""
    r = BuildCheckResult(name="x", run="true", exit_code=0, tail="")
    with pytest.raises((AttributeError, TypeError)):
        r.exit_code = 99  # type: ignore[misc]


def test_build_check_policy_defaults() -> None:
    """Defaults are documented in the schema docstring — verify them so a
    future refactor that changes them surfaces a failing test."""
    pol = BuildCheckPolicy()
    assert pol.timeout_sec == 600
    assert pol.tail_lines == 40
    assert pol.skip_if_missing_executable is True
    assert pol.commands == []
