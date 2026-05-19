"""NEW-32 — post-merge integration test in merge_to_integration_subscriber.

After a successful ff-merge, the subscriber runs build-check commands against
the integration branch. Two parallel stories can merge with no textual
conflict yet break the combined integration branch semantically. This catches
such regressions and emits INTEGRATION_TEST_FAILED + HUMAN_QUERY.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bmad_orchestrator.agent import run
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType


# ── helpers ──────────────────────────────────────────────────────────────────


def _approve_event(story_id: str = "1.5", worktree: str = "") -> Event:
    return Event(
        type=EventType.CODE_REVIEW_VERDICT,
        payload={"story_id": story_id, "verdict": "approve", "worktree": worktree},
    )


def _ok_result(name: str = "pytest") -> "BuildCheckResult":
    from bmad_orchestrator.runtime.build_check import BuildCheckResult
    return BuildCheckResult(name=name, run="pytest", exit_code=0, tail="")


def _fail_result(name: str = "pytest") -> "BuildCheckResult":
    from bmad_orchestrator.runtime.build_check import BuildCheckResult
    return BuildCheckResult(name=name, run="pytest", exit_code=1, tail="FAILED test")


def _skipped_result(name: str = "ruff") -> "BuildCheckResult":
    from bmad_orchestrator.runtime.build_check import BuildCheckResult
    return BuildCheckResult(
        name=name, run="ruff check", exit_code=0, tail="",
        skipped_no_ruff_config=True,
    )


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clean_integration_test_no_events(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Passing integration test → no INTEGRATION_TEST_FAILED, no HUMAN_QUERY for it."""
    project = tmp_path / "proj"
    project.mkdir()
    run.configure_code_review_gate(target_project=project, wave="1a")

    # Patch ff-merge to succeed
    monkeypatch.setattr(
        run,
        "_ff_merge_to_integration",
        AsyncMock(return_value="new-sha"),
    )
    # Patch pre-merge recovery to no-op
    monkeypatch.setattr(
        run,
        "recover_pre_merge",
        AsyncMock(return_value=MagicMock(recovered=False, error=None, out_of_scope_paths=[])),
    )
    # Patch cleanup_worktree to no-op
    monkeypatch.setattr(run, "cleanup_worktree", lambda path, root: None)

    from bmad_orchestrator.runtime.build_check import BuildCheckPolicy

    from bmad_orchestrator.runtime.build_check import BuildCheckCommand as _BCC
    from bmad_orchestrator.runtime.build_check import BuildCheckPolicy as _BCP
    _cmd = _BCC(name="pytest", run="pytest tests/ -q", required=True)
    _policy = _BCP(commands=[_cmd])

    with (
        patch(
            "bmad_orchestrator.runtime.build_check.load_build_check_policy",
            return_value=_policy,
        ),
        patch(
            "bmad_orchestrator.runtime.build_check._run_command",
            AsyncMock(return_value=_ok_result("pytest")),
        ),
    ):
        bus = EventLoop()
        await run.merge_to_integration_subscriber(_approve_event(), bus)

    emitted = list(bus.queue._queue)  # type: ignore[attr-defined]
    types = [e.type for e in emitted]
    assert EventType.INTEGRATION_TEST_FAILED not in types
    assert EventType.INTEGRATION_MERGE_COMPLETED in types
    # No HUMAN_QUERY for the merge itself
    hq = [e for e in emitted if e.type == EventType.HUMAN_QUERY]
    assert not hq, "no HUMAN_QUERY expected for a passing integration test"

    run.configure_code_review_gate(target_project=None, wave=None)


@pytest.mark.asyncio
async def test_failing_integration_test_emits_failed_and_human_query(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Failing test command → INTEGRATION_TEST_FAILED + HUMAN_QUERY with pre-merge SHA."""
    project = tmp_path / "proj"
    project.mkdir()
    run.configure_code_review_gate(target_project=project, wave="1a")

    monkeypatch.setattr(
        run,
        "_ff_merge_to_integration",
        AsyncMock(return_value="new-sha"),
    )
    monkeypatch.setattr(
        run,
        "recover_pre_merge",
        AsyncMock(return_value=MagicMock(recovered=False, error=None, out_of_scope_paths=[])),
    )
    monkeypatch.setattr(run, "cleanup_worktree", lambda path, root: None)

    # Simulate a pre-merge integration SHA being available
    monkeypatch.setattr(
        run,
        "_CODE_REVIEW_GATE",
        run._CODE_REVIEW_GATE,  # keep current config
    )

    from bmad_orchestrator.runtime.build_check import BuildCheckCommand as _BCC
    from bmad_orchestrator.runtime.build_check import BuildCheckPolicy as _BCP
    _cmd = _BCC(name="pytest", run="pytest tests/ -q", required=True)
    _policy = _BCP(commands=[_cmd])

    with (
        patch(
            "bmad_orchestrator.runtime.build_check.load_build_check_policy",
            return_value=_policy,
        ),
        patch(
            "bmad_orchestrator.runtime.build_check._run_command",
            AsyncMock(return_value=_fail_result("pytest")),
        ),
        patch("git.Repo") as mock_repo_cls,
    ):
        # Simulate pre-merge SHA
        mock_repo = MagicMock()
        mock_repo.branches = [MagicMock(name="integration/1a")]
        mock_repo.commit.return_value.hexsha = "pre-merge-sha-abc"
        mock_repo_cls.return_value = mock_repo

        bus = EventLoop()
        await run.merge_to_integration_subscriber(_approve_event(), bus)

    emitted = list(bus.queue._queue)  # type: ignore[attr-defined]
    types = [e.type for e in emitted]

    assert EventType.INTEGRATION_TEST_FAILED in types
    assert EventType.HUMAN_QUERY in types

    failed = next(e for e in emitted if e.type == EventType.INTEGRATION_TEST_FAILED)
    assert failed.payload["story_id"] == "1.5"
    assert failed.payload["failed_command"] == "pytest"
    assert failed.payload["exit_code"] == 1
    assert "integration_branch" in failed.payload

    hq = [e for e in emitted if e.type == EventType.HUMAN_QUERY]
    # One HQ for the integration test failure
    it_hq = [e for e in hq if e.payload.get("verdict") == "integration_test_failed"]
    assert len(it_hq) == 1
    assert "pre_merge_sha" in it_hq[0].payload

    run.configure_code_review_gate(target_project=None, wave=None)


@pytest.mark.asyncio
async def test_no_test_command_configured_skips_silently(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Empty policy.commands → post-merge test silently skipped."""
    project = tmp_path / "proj"
    project.mkdir()
    run.configure_code_review_gate(target_project=project, wave="1a")

    monkeypatch.setattr(
        run,
        "_ff_merge_to_integration",
        AsyncMock(return_value="new-sha"),
    )
    monkeypatch.setattr(
        run,
        "recover_pre_merge",
        AsyncMock(return_value=MagicMock(recovered=False, error=None, out_of_scope_paths=[])),
    )
    monkeypatch.setattr(run, "cleanup_worktree", lambda path, root: None)

    from bmad_orchestrator.runtime.build_check import BuildCheckPolicy
    from bmad_orchestrator.skills_repo import PolicyNotFoundError

    with patch(
        "bmad_orchestrator.runtime.build_check.load_build_check_policy",
        side_effect=PolicyNotFoundError("no policy"),
    ):
        bus = EventLoop()
        await run.merge_to_integration_subscriber(_approve_event(), bus)

    emitted = list(bus.queue._queue)  # type: ignore[attr-defined]
    types = [e.type for e in emitted]
    assert EventType.INTEGRATION_TEST_FAILED not in types
    # Merge still completes
    assert EventType.INTEGRATION_MERGE_COMPLETED in types

    run.configure_code_review_gate(target_project=None, wave=None)


@pytest.mark.asyncio
async def test_exception_inside_post_merge_block_does_not_break_merge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unexpected exception in the post-merge block must not abort the merge."""
    project = tmp_path / "proj"
    project.mkdir()
    run.configure_code_review_gate(target_project=project, wave="1a")

    monkeypatch.setattr(
        run,
        "_ff_merge_to_integration",
        AsyncMock(return_value="new-sha"),
    )
    monkeypatch.setattr(
        run,
        "recover_pre_merge",
        AsyncMock(return_value=MagicMock(recovered=False, error=None, out_of_scope_paths=[])),
    )
    monkeypatch.setattr(run, "cleanup_worktree", lambda path, root: None)

    # Make load_build_check_policy explode unexpectedly
    with patch(
        "bmad_orchestrator.runtime.build_check.load_build_check_policy",
        side_effect=RuntimeError("unexpected explosion"),
    ):
        bus = EventLoop()
        # Must NOT raise — exception is swallowed by the defensive wrapper
        await run.merge_to_integration_subscriber(_approve_event(), bus)

    emitted = list(bus.queue._queue)  # type: ignore[attr-defined]
    types = [e.type for e in emitted]
    # Merge completed despite the exception in the post-merge block
    assert EventType.INTEGRATION_MERGE_COMPLETED in types
    assert EventType.INTEGRATION_TEST_FAILED not in types

    run.configure_code_review_gate(target_project=None, wave=None)


@pytest.mark.asyncio
async def test_skipped_command_not_treated_as_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A skipped command (missing executable or no ruff config) is NOT a failure."""
    project = tmp_path / "proj"
    project.mkdir()
    run.configure_code_review_gate(target_project=project, wave="1a")

    monkeypatch.setattr(
        run,
        "_ff_merge_to_integration",
        AsyncMock(return_value="new-sha"),
    )
    monkeypatch.setattr(
        run,
        "recover_pre_merge",
        AsyncMock(return_value=MagicMock(recovered=False, error=None, out_of_scope_paths=[])),
    )
    monkeypatch.setattr(run, "cleanup_worktree", lambda path, root: None)

    from bmad_orchestrator.runtime.build_check import BuildCheckCommand as _BCC
    from bmad_orchestrator.runtime.build_check import BuildCheckPolicy as _BCP
    _cmd = _BCC(
        name="ruff", run="ruff check src", required=True,
        skip_if_no_ruff_config=True,
    )
    _policy = _BCP(commands=[_cmd])

    # ruff returns exit_code=1 but skipped_no_ruff_config=True → NOT a failure
    from bmad_orchestrator.runtime.build_check import BuildCheckResult as _BCR
    _skipped = _BCR(
        name="ruff", run="ruff check src", exit_code=1, tail="",
        skipped_no_ruff_config=True,
    )

    with (
        patch(
            "bmad_orchestrator.runtime.build_check.load_build_check_policy",
            return_value=_policy,
        ),
        patch(
            "bmad_orchestrator.runtime.build_check._run_command",
            AsyncMock(return_value=_skipped),
        ),
    ):
        bus = EventLoop()
        await run.merge_to_integration_subscriber(_approve_event(), bus)

    emitted = list(bus.queue._queue)  # type: ignore[attr-defined]
    types = [e.type for e in emitted]
    assert EventType.INTEGRATION_TEST_FAILED not in types
    assert EventType.INTEGRATION_MERGE_COMPLETED in types

    run.configure_code_review_gate(target_project=None, wave=None)
