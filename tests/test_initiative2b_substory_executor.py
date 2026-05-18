"""Initiative #2 Task 2.3 + 2.4 — sub-story execution + squash-merge.

Three groups:

* execute_sub_stories — sequential dispatch into a shared parent worktree,
  halt-on-failure, silent-failure detection, branch invariant check.
* squash_sub_stories — N→1 commit collapse with body listing every sub-id;
  edge cases for 0/1 commit fast paths.
* End-to-end — execute + squash chained in one test, mimicking the way
  Initiative #2's auto-split pipeline will use both helpers together.
"""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from bmad_orchestrator.runtime.sub_story_executor import (
    SquashResult,
    SubStoryExecutionError,
    SubStoryResult,
    execute_sub_stories,
    squash_sub_stories,
)
from bmad_orchestrator.runtime.worker_spawn import WorkerHandle


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def parent_worktree(tmp_path: Path) -> tuple[Path, str, str]:
    """Init a tmp git repo, branch off feature/parent-1, return (path, base_sha, branch)."""
    _git(["init", "-q", "-b", "main"], tmp_path)
    _git(["config", "user.email", "test@example.com"], tmp_path)
    _git(["config", "user.name", "Test Bot"], tmp_path)
    (tmp_path / "README.md").write_text("init\n", encoding="utf-8")
    _git(["add", "."], tmp_path)
    _git(["commit", "-q", "-m", "init"], tmp_path)
    branch = "feature/parent-1"
    _git(["checkout", "-q", "-b", branch], tmp_path)
    base_sha = _git(["rev-parse", "HEAD"], tmp_path)
    return tmp_path, base_sha, branch


SpawnFactory = Callable[..., Awaitable[WorkerHandle]]


def _make_committing_spawn(repo_path: Path) -> SpawnFactory:
    """A spawn_fn shim that commits a per-sub file on behalf of the synthetic worker.

    Models the contract real workers honour (one commit per sub-story under
    a self-descriptive message). Returns a mock-mode WorkerHandle so the
    default ``_default_wait`` short-circuits to exit code 0.
    """

    async def _spawn(
        *, worktree: str, story_id: str, branch: str, **_kwargs: Any
    ) -> WorkerHandle:
        wt = Path(worktree)
        target = wt / f"{story_id}.txt"
        target.write_text(f"work for {story_id}\n", encoding="utf-8")
        _git(["add", "."], repo_path)
        _git(["commit", "-q", "-m", f"sub: {story_id}"], repo_path)
        return WorkerHandle(
            worktree=worktree,
            story_id=story_id,
            branch=branch,
            pid=0,
            jsonl_path=wt / "events.jsonl",
            process=None,
            mock=True,
        )

    return _spawn


def _make_silent_spawn(repo_path: Path) -> SpawnFactory:
    """spawn_fn shim that succeeds but commits nothing — exercises Phase 0
    silent-failure detection inside execute_sub_stories."""

    async def _spawn(
        *, worktree: str, story_id: str, branch: str, **_kwargs: Any
    ) -> WorkerHandle:
        return WorkerHandle(
            worktree=worktree,
            story_id=story_id,
            branch=branch,
            pid=0,
            jsonl_path=Path(worktree) / "events.jsonl",
            process=None,
            mock=True,
        )

    return _spawn


# ── execute_sub_stories ───────────────────────────────────────────────────────


def test_execute_empty_sub_stories_is_noop(parent_worktree: tuple[Path, str, str]) -> None:
    path, base_sha, branch = parent_worktree
    results = asyncio.run(
        execute_sub_stories(
            parent_story_id="1.1",
            sub_stories=[],
            worktree=path,
            branch=branch,
            base_sha=base_sha,
            spawn_fn=_make_committing_spawn(path),
        )
    )
    assert results == []
    assert int(_git(["rev-list", "--count", f"{base_sha}..HEAD"], path)) == 0


def test_execute_runs_sub_stories_sequentially(
    parent_worktree: tuple[Path, str, str]
) -> None:
    path, base_sha, branch = parent_worktree
    subs = [
        {"id": "1.1-a", "title": "first"},
        {"id": "1.1-b", "title": "second"},
        {"id": "1.1-c", "title": "third"},
    ]
    results = asyncio.run(
        execute_sub_stories(
            parent_story_id="1.1",
            sub_stories=subs,
            worktree=path,
            branch=branch,
            base_sha=base_sha,
            spawn_fn=_make_committing_spawn(path),
        )
    )
    assert [r.sub_id for r in results] == ["1.1-a", "1.1-b", "1.1-c"]
    assert all(r.succeeded for r in results)
    assert all(r.commits_added == 1 for r in results)
    assert int(_git(["rev-list", "--count", f"{base_sha}..HEAD"], path)) == 3


def test_execute_each_sub_sees_previous_commits(
    parent_worktree: tuple[Path, str, str]
) -> None:
    path, base_sha, branch = parent_worktree
    subs = [{"id": "1.1-a"}, {"id": "1.1-b"}]
    results = asyncio.run(
        execute_sub_stories(
            parent_story_id="1.1",
            sub_stories=subs,
            worktree=path,
            branch=branch,
            base_sha=base_sha,
            spawn_fn=_make_committing_spawn(path),
        )
    )
    # head sha after sub-a != head sha after sub-b — sequential advancement.
    assert results[0].head_sha_after != results[1].head_sha_after
    # And sub-b's worker saw sub-a's file (artefact of shared worktree).
    assert (path / "1.1-a.txt").exists()
    assert (path / "1.1-b.txt").exists()


def test_execute_emits_event_per_phase(parent_worktree: tuple[Path, str, str]) -> None:
    path, base_sha, branch = parent_worktree
    events: list[dict[str, Any]] = []
    subs = [{"id": "1.1-a"}, {"id": "1.1-b"}]
    asyncio.run(
        execute_sub_stories(
            parent_story_id="1.1",
            sub_stories=subs,
            worktree=path,
            branch=branch,
            base_sha=base_sha,
            spawn_fn=_make_committing_spawn(path),
            on_event=events.append,
        )
    )
    types = [e["event_type"] for e in events]
    assert types == [
        "sub_story_started",
        "sub_story_completed",
        "sub_story_started",
        "sub_story_completed",
    ]
    assert events[1]["succeeded"] is True
    assert events[1]["commits_added"] == 1


def test_execute_halts_on_failure_by_default(
    parent_worktree: tuple[Path, str, str]
) -> None:
    path, base_sha, branch = parent_worktree
    committing = _make_committing_spawn(path)
    silent = _make_silent_spawn(path)

    async def hybrid_spawn(
        *, worktree: str, story_id: str, branch: str, **kwargs: Any
    ) -> WorkerHandle:
        if story_id.endswith("-b"):
            return await silent(
                worktree=worktree, story_id=story_id, branch=branch, **kwargs
            )
        return await committing(
            worktree=worktree, story_id=story_id, branch=branch, **kwargs
        )

    subs = [{"id": "1.1-a"}, {"id": "1.1-b"}, {"id": "1.1-c"}]
    results = asyncio.run(
        execute_sub_stories(
            parent_story_id="1.1",
            sub_stories=subs,
            worktree=path,
            branch=branch,
            base_sha=base_sha,
            spawn_fn=hybrid_spawn,
        )
    )
    # sub-c never spawned — halt fired after sub-b's silent failure.
    assert [r.sub_id for r in results] == ["1.1-a", "1.1-b"]
    assert results[0].succeeded is True
    assert results[1].succeeded is False
    assert results[1].failure_reason == "silent_failure_zero_commits"


def test_execute_continues_when_halt_disabled(
    parent_worktree: tuple[Path, str, str]
) -> None:
    path, base_sha, branch = parent_worktree
    committing = _make_committing_spawn(path)
    silent = _make_silent_spawn(path)

    async def hybrid_spawn(
        *, worktree: str, story_id: str, branch: str, **kwargs: Any
    ) -> WorkerHandle:
        if story_id.endswith("-b"):
            return await silent(
                worktree=worktree, story_id=story_id, branch=branch, **kwargs
            )
        return await committing(
            worktree=worktree, story_id=story_id, branch=branch, **kwargs
        )

    subs = [{"id": "1.1-a"}, {"id": "1.1-b"}, {"id": "1.1-c"}]
    results = asyncio.run(
        execute_sub_stories(
            parent_story_id="1.1",
            sub_stories=subs,
            worktree=path,
            branch=branch,
            base_sha=base_sha,
            spawn_fn=hybrid_spawn,
            halt_on_failure=False,
        )
    )
    assert [r.sub_id for r in results] == ["1.1-a", "1.1-b", "1.1-c"]
    assert [r.succeeded for r in results] == [True, False, True]


def test_execute_uses_custom_wait_fn(parent_worktree: tuple[Path, str, str]) -> None:
    path, base_sha, branch = parent_worktree

    async def fake_wait(_handle: WorkerHandle) -> int:
        return 7  # non-zero — worker "crashed"

    subs = [{"id": "1.1-a"}, {"id": "1.1-b"}]
    results = asyncio.run(
        execute_sub_stories(
            parent_story_id="1.1",
            sub_stories=subs,
            worktree=path,
            branch=branch,
            base_sha=base_sha,
            spawn_fn=_make_committing_spawn(path),
            wait_fn=fake_wait,
        )
    )
    # Halt after first crash; never tries sub-b.
    assert [r.sub_id for r in results] == ["1.1-a"]
    assert results[0].exit_code == 7
    assert results[0].failure_reason == "worker_exit_code=7"
    assert results[0].succeeded is False


def test_execute_missing_id_raises(parent_worktree: tuple[Path, str, str]) -> None:
    path, base_sha, branch = parent_worktree
    with pytest.raises(SubStoryExecutionError, match="missing 'id'"):
        asyncio.run(
            execute_sub_stories(
                parent_story_id="1.1",
                sub_stories=[{"title": "no id"}],
                worktree=path,
                branch=branch,
                base_sha=base_sha,
                spawn_fn=_make_committing_spawn(path),
            )
        )


def test_execute_branch_mismatch_raises(
    parent_worktree: tuple[Path, str, str]
) -> None:
    path, base_sha, _branch = parent_worktree
    with pytest.raises(SubStoryExecutionError, match="expected 'feature/other'"):
        asyncio.run(
            execute_sub_stories(
                parent_story_id="1.1",
                sub_stories=[{"id": "1.1-a"}],
                worktree=path,
                branch="feature/other",
                base_sha=base_sha,
                spawn_fn=_make_committing_spawn(path),
            )
        )


def test_execute_not_a_git_repo_raises(tmp_path: Path) -> None:
    with pytest.raises(SubStoryExecutionError, match="not a git repo"):
        asyncio.run(
            execute_sub_stories(
                parent_story_id="1.1",
                sub_stories=[{"id": "1.1-a"}],
                worktree=tmp_path,
                branch="feature/parent-1",
                base_sha="0" * 40,
            )
        )


def test_execute_passes_spawn_kwargs(parent_worktree: tuple[Path, str, str]) -> None:
    path, base_sha, branch = parent_worktree
    captured: dict[str, Any] = {}

    async def spy_spawn(
        *, worktree: str, story_id: str, branch: str, **kwargs: Any
    ) -> WorkerHandle:
        captured.update(kwargs)
        return await _make_committing_spawn(path)(
            worktree=worktree, story_id=story_id, branch=branch
        )

    asyncio.run(
        execute_sub_stories(
            parent_story_id="1.1",
            sub_stories=[{"id": "1.1-a"}],
            worktree=path,
            branch=branch,
            base_sha=base_sha,
            spawn_fn=spy_spawn,
            spawn_kwargs={"mock": True, "sandbox_network": "none"},
        )
    )
    assert captured == {"mock": True, "sandbox_network": "none"}


# ── squash_sub_stories ────────────────────────────────────────────────────────


def test_squash_skipped_when_no_commits(
    parent_worktree: tuple[Path, str, str]
) -> None:
    path, base_sha, _branch = parent_worktree
    result = squash_sub_stories(
        parent_story_id="1.1",
        worktree=path,
        base_sha=base_sha,
        sub_ids=["1.1-a"],
    )
    assert result.skipped is True
    assert result.commits_squashed == 0
    assert result.squashed_sha == result.pre_squash_head


def test_squash_skipped_when_single_commit(
    parent_worktree: tuple[Path, str, str]
) -> None:
    path, base_sha, branch = parent_worktree
    asyncio.run(
        execute_sub_stories(
            parent_story_id="1.1",
            sub_stories=[{"id": "1.1-only"}],
            worktree=path,
            branch=branch,
            base_sha=base_sha,
            spawn_fn=_make_committing_spawn(path),
        )
    )
    pre_head = _git(["rev-parse", "HEAD"], path)
    result = squash_sub_stories(
        parent_story_id="1.1",
        worktree=path,
        base_sha=base_sha,
        sub_ids=["1.1-only"],
    )
    assert result.skipped is True
    assert result.commits_squashed == 1
    assert result.squashed_sha == pre_head
    assert "sub: 1.1-only" in result.message


def test_squash_collapses_n_commits_to_one(
    parent_worktree: tuple[Path, str, str]
) -> None:
    path, base_sha, branch = parent_worktree
    sub_ids = ["1.1-a", "1.1-b", "1.1-c"]
    asyncio.run(
        execute_sub_stories(
            parent_story_id="1.1",
            sub_stories=[{"id": sid} for sid in sub_ids],
            worktree=path,
            branch=branch,
            base_sha=base_sha,
            spawn_fn=_make_committing_spawn(path),
        )
    )
    assert int(_git(["rev-list", "--count", f"{base_sha}..HEAD"], path)) == 3

    result = squash_sub_stories(
        parent_story_id="1.1",
        worktree=path,
        base_sha=base_sha,
        sub_ids=sub_ids,
    )
    assert result.skipped is False
    assert result.commits_squashed == 3
    # exactly one commit between base and HEAD now
    assert int(_git(["rev-list", "--count", f"{base_sha}..HEAD"], path)) == 1
    # all sub artefacts retained in the tree
    for sid in sub_ids:
        assert (path / f"{sid}.txt").exists()
    msg = _git(["log", "-1", "--pretty=%B", "HEAD"], path)
    assert "feat(1.1)" in msg
    for sid in sub_ids:
        assert sid in msg


def test_squash_emits_event(parent_worktree: tuple[Path, str, str]) -> None:
    path, base_sha, branch = parent_worktree
    sub_ids = ["1.1-a", "1.1-b"]
    asyncio.run(
        execute_sub_stories(
            parent_story_id="1.1",
            sub_stories=[{"id": sid} for sid in sub_ids],
            worktree=path,
            branch=branch,
            base_sha=base_sha,
            spawn_fn=_make_committing_spawn(path),
        )
    )
    events: list[dict[str, Any]] = []
    squash_sub_stories(
        parent_story_id="1.1",
        worktree=path,
        base_sha=base_sha,
        sub_ids=sub_ids,
        on_event=events.append,
    )
    assert len(events) == 1
    assert events[0]["event_type"] == "sub_story_squash_done"
    assert events[0]["commits_squashed"] == 2
    assert events[0]["sub_ids"] == sub_ids


def test_squash_appends_extra_message_lines(
    parent_worktree: tuple[Path, str, str]
) -> None:
    path, base_sha, branch = parent_worktree
    sub_ids = ["1.1-a", "1.1-b"]
    asyncio.run(
        execute_sub_stories(
            parent_story_id="1.1",
            sub_stories=[{"id": sid} for sid in sub_ids],
            worktree=path,
            branch=branch,
            base_sha=base_sha,
            spawn_fn=_make_committing_spawn(path),
        )
    )
    result = squash_sub_stories(
        parent_story_id="1.1",
        worktree=path,
        base_sha=base_sha,
        sub_ids=sub_ids,
        extra_message_lines=[
            "Original split decision: AC>=7",
            "Decomposer: opus-4.7@2026-05-18",
        ],
    )
    msg = _git(["log", "-1", "--pretty=%B", "HEAD"], path)
    assert "Original split decision: AC>=7" in msg
    assert "Decomposer: opus-4.7@2026-05-18" in msg
    assert "feat(1.1)" in result.message


# ── End-to-end ────────────────────────────────────────────────────────────────


def test_execute_then_squash_full_pipeline(
    parent_worktree: tuple[Path, str, str]
) -> None:
    """Models how Initiative #2's auto-split pipeline will chain both helpers:
    decomposer emits sub-stories → executor runs them sequentially in the
    parent worktree → squash collapses to a single parent commit ready for
    Stage 6 code-review."""
    path, base_sha, branch = parent_worktree
    sub_ids = ["3.1-a", "3.1-b", "3.1-c", "3.1-d"]
    subs = [
        {"id": "3.1-a", "title": "Dockerfile"},
        {"id": "3.1-b", "title": "docker-compose"},
        {"id": "3.1-c", "title": "healthcheck"},
        {"id": "3.1-d", "title": "smoke tests"},
    ]
    events: list[dict[str, Any]] = []

    exec_results = asyncio.run(
        execute_sub_stories(
            parent_story_id="3.1",
            sub_stories=subs,
            worktree=path,
            branch=branch,
            base_sha=base_sha,
            spawn_fn=_make_committing_spawn(path),
            on_event=events.append,
        )
    )
    assert all(r.succeeded for r in exec_results)

    squash = squash_sub_stories(
        parent_story_id="3.1",
        worktree=path,
        base_sha=base_sha,
        sub_ids=sub_ids,
        extra_message_lines=["Decomposer source: story-splitter@v1"],
        on_event=events.append,
    )
    assert squash.commits_squashed == 4
    assert squash.skipped is False
    assert int(_git(["rev-list", "--count", f"{base_sha}..HEAD"], path)) == 1

    # Audit-trail assertion — every phase emitted an event in order.
    types = [e["event_type"] for e in events]
    assert types == [
        "sub_story_started", "sub_story_completed",
        "sub_story_started", "sub_story_completed",
        "sub_story_started", "sub_story_completed",
        "sub_story_started", "sub_story_completed",
        "sub_story_squash_done",
    ]
    # Final commit message exposes all four sub-ids — Stage 6 reviewer scope.
    final_msg = _git(["log", "-1", "--pretty=%B", "HEAD"], path)
    for sid in sub_ids:
        assert sid in final_msg


def test_result_dataclasses_are_frozen() -> None:
    """SubStoryResult / SquashResult are frozen — sub-stories are append-only history."""
    sr = SubStoryResult(
        sub_id="1.1-a",
        exit_code=0,
        commits_added=1,
        head_sha_after="abc",
        handle=None,
    )
    with pytest.raises(Exception):
        sr.exit_code = 1  # type: ignore[misc]

    sq = SquashResult(
        parent_story_id="1.1",
        base_sha="0" * 40,
        pre_squash_head="abc",
        squashed_sha="def",
        commits_squashed=3,
        message="x",
    )
    with pytest.raises(Exception):
        sq.commits_squashed = 0  # type: ignore[misc]
