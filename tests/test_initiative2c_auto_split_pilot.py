"""Initiative #2C — auto-split orchestration tests.

Three groups:

* ``auto_split_and_execute`` happy/fallback/error paths — driven without an
  EventLoop bus so the synchronous fallback branches stay testable.
* ``make_bus_bridge`` translation — drives a real ``EventLoop`` and asserts the
  4 typed events fire with the expected payloads.
* ``auto_split_enabled`` env-flag toggle.

The decomposer signature is async-callable; tests inject lambdas that return
canned JSON without ever touching a real ``claude -p`` subprocess.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from bmad_orchestrator.runtime.auto_split import (
    AUTO_SPLIT_ENV_VAR,
    AutoSplitOutcome,
    auto_split_and_execute,
    auto_split_enabled,
    make_bus_bridge,
)
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.runtime.worker_spawn import WorkerHandle


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


SpawnFactory = Callable[..., Awaitable[WorkerHandle]]


@pytest.fixture
def parent_worktree(tmp_path: Path) -> tuple[Path, str, str]:
    _git(["init", "-q", "-b", "main"], tmp_path)
    _git(["config", "user.email", "test@example.com"], tmp_path)
    _git(["config", "user.name", "Test Bot"], tmp_path)
    (tmp_path / "README.md").write_text("init\n", encoding="utf-8")
    _git(["add", "."], tmp_path)
    _git(["commit", "-q", "-m", "init"], tmp_path)
    branch = "feature/3.1"
    _git(["checkout", "-q", "-b", branch], tmp_path)
    base_sha = _git(["rev-parse", "HEAD"], tmp_path)
    return tmp_path, base_sha, branch


def _committing_spawn(repo_path: Path) -> SpawnFactory:
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


def _silent_spawn(repo_path: Path) -> SpawnFactory:
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


def _large_story(
    parent_id: str = "3.1",
    tokens: int = 12000,
    files: tuple[str, ...] = (
        "backend/x.py",
        "backend/y.py",
        "ui/a.tsx",
        "ui/b.tsx",
        "ui/c.tsx",
        "tests/test_x.py",
        "tests/test_y.py",
        "tests/test_a.py",
        "tests/test_b.py",
        "tests/test_c.py",
        "tests/test_extra.py",
    ),
) -> dict[str, Any]:
    return {
        "id": parent_id,
        "title": "Nextcloud Docker template",
        "estimated_minutes": 360,
        "estimated_tokens": tokens,
        "touches_files": list(files),
        "acceptance": [
            "AC1", "AC2", "AC3", "AC4", "AC5", "AC6", "AC7", "AC8",
        ],
    }


def _small_story(parent_id: str = "1.1") -> dict[str, Any]:
    return {
        "id": parent_id,
        "title": "Tiny",
        "estimated_minutes": 30,
        "estimated_tokens": 800,
        "touches_files": ["backend/x.py"],
        "acceptance": ["AC1"],
    }


def _canned_decomposer(
    sub_stories: list[dict[str, Any]],
) -> Callable[[str, dict[str, Any]], Awaitable[str]]:
    async def _fn(_prompt: str, _story: dict[str, Any]) -> str:
        return json.dumps(sub_stories)

    return _fn


def _fence_decomposer(
    sub_stories: list[dict[str, Any]],
) -> Callable[[str, dict[str, Any]], Awaitable[str]]:
    async def _fn(_prompt: str, _story: dict[str, Any]) -> str:
        return "```json\n" + json.dumps(sub_stories) + "\n```"

    return _fn


# ── happy path ────────────────────────────────────────────────────────────────


def test_auto_split_happy_path_three_sub_stories(
    parent_worktree: tuple[Path, str, str],
) -> None:
    path, base_sha, branch = parent_worktree
    sub_payload = [
        {"id": "3.1-a", "title": "Compose scaffold", "deps_on": []},
        {"id": "3.1-b", "title": "Service definitions", "deps_on": ["3.1-a"]},
        {"id": "3.1-c", "title": "Healthcheck wiring", "deps_on": ["3.1-b"]},
    ]
    outcome = asyncio.run(
        auto_split_and_execute(
            story=_large_story(),
            worktree=path,
            branch=branch,
            base_sha=base_sha,
            decompose_fn=_canned_decomposer(sub_payload),
            spawn_fn=_committing_spawn(path),
        )
    )
    assert outcome.attempted is True
    assert outcome.succeeded is True
    assert outcome.decision == "split"
    assert outcome.sub_ids == ("3.1-a", "3.1-b", "3.1-c")
    assert outcome.squash is not None
    assert outcome.squash.commits_squashed == 3
    # Squash collapses N → 1 commit on the parent branch.
    commits_after = int(
        _git(["rev-list", "--count", f"{base_sha}..HEAD"], path)
    )
    assert commits_after == 1
    head_msg = _git(["log", "-1", "--pretty=%B", "HEAD"], path)
    assert "squash 3 sub-stories" in head_msg
    assert "3.1-a" in head_msg and "3.1-c" in head_msg


def test_auto_split_keep_decision_returns_attempted_false(
    parent_worktree: tuple[Path, str, str],
) -> None:
    path, base_sha, branch = parent_worktree
    outcome = asyncio.run(
        auto_split_and_execute(
            story=_small_story(),
            worktree=path,
            branch=branch,
            base_sha=base_sha,
            decompose_fn=_canned_decomposer([]),
            spawn_fn=_committing_spawn(path),
        )
    )
    assert outcome.attempted is False
    assert outcome.decision == "keep"
    assert outcome.squash is None
    # No commits added.
    assert int(_git(["rev-list", "--count", f"{base_sha}..HEAD"], path)) == 0


def test_auto_split_accepts_markdown_fenced_decomposer_output(
    parent_worktree: tuple[Path, str, str],
) -> None:
    path, base_sha, branch = parent_worktree
    sub_payload = [
        {"id": "3.1-a", "title": "A", "deps_on": []},
        {"id": "3.1-b", "title": "B", "deps_on": []},
    ]
    outcome = asyncio.run(
        auto_split_and_execute(
            story=_large_story(),
            worktree=path,
            branch=branch,
            base_sha=base_sha,
            decompose_fn=_fence_decomposer(sub_payload),
            spawn_fn=_committing_spawn(path),
        )
    )
    assert outcome.attempted is True and outcome.succeeded is True
    assert outcome.sub_ids == ("3.1-a", "3.1-b")


# ── fallback paths ────────────────────────────────────────────────────────────


def test_auto_split_decomposer_error_falls_back_keep(
    parent_worktree: tuple[Path, str, str],
) -> None:
    path, base_sha, branch = parent_worktree

    async def _crash(_prompt: str, _story: dict[str, Any]) -> str:
        raise RuntimeError("simulated network failure")

    outcome = asyncio.run(
        auto_split_and_execute(
            story=_large_story(),
            worktree=path,
            branch=branch,
            base_sha=base_sha,
            decompose_fn=_crash,
            spawn_fn=_committing_spawn(path),
        )
    )
    assert outcome.attempted is False
    assert outcome.fallback_reason == "decomposer_error"
    assert "simulated network failure" in (outcome.error or "")


def test_auto_split_invalid_json_falls_back_keep(
    parent_worktree: tuple[Path, str, str],
) -> None:
    path, base_sha, branch = parent_worktree

    async def _garbage(_prompt: str, _story: dict[str, Any]) -> str:
        return "not json {{{"

    outcome = asyncio.run(
        auto_split_and_execute(
            story=_large_story(),
            worktree=path,
            branch=branch,
            base_sha=base_sha,
            decompose_fn=_garbage,
            spawn_fn=_committing_spawn(path),
        )
    )
    assert outcome.attempted is False
    assert outcome.fallback_reason == "invalid_decomposition"


def test_auto_split_single_sub_story_falls_back_invalid(
    parent_worktree: tuple[Path, str, str],
) -> None:
    path, base_sha, branch = parent_worktree
    # MIN_SUBS = 2 → 1 sub-story payload fails validate_decomposition.
    outcome = asyncio.run(
        auto_split_and_execute(
            story=_large_story(),
            worktree=path,
            branch=branch,
            base_sha=base_sha,
            decompose_fn=_canned_decomposer(
                [{"id": "3.1-a", "title": "only one", "deps_on": []}]
            ),
            spawn_fn=_committing_spawn(path),
        )
    )
    assert outcome.attempted is False
    assert outcome.fallback_reason == "invalid_decomposition"


def test_auto_split_silent_failure_marks_outcome_not_succeeded(
    parent_worktree: tuple[Path, str, str],
) -> None:
    path, base_sha, branch = parent_worktree
    sub_payload = [
        {"id": "3.1-a", "title": "A", "deps_on": []},
        {"id": "3.1-b", "title": "B", "deps_on": []},
    ]
    outcome = asyncio.run(
        auto_split_and_execute(
            story=_large_story(),
            worktree=path,
            branch=branch,
            base_sha=base_sha,
            decompose_fn=_canned_decomposer(sub_payload),
            spawn_fn=_silent_spawn(path),  # exit 0 but no commits
        )
    )
    assert outcome.attempted is True
    assert outcome.succeeded is False
    assert outcome.squash is None
    assert outcome.fallback_reason == "silent_failure_zero_commits"


def test_auto_split_missing_parent_id_returns_immediately(
    parent_worktree: tuple[Path, str, str],
) -> None:
    path, base_sha, branch = parent_worktree
    outcome = asyncio.run(
        auto_split_and_execute(
            story={"title": "no id here"},
            worktree=path,
            branch=branch,
            base_sha=base_sha,
            decompose_fn=_canned_decomposer([]),
            spawn_fn=_committing_spawn(path),
        )
    )
    assert outcome.attempted is False
    assert outcome.fallback_reason == "missing_parent_id"


# ── bus bridge ────────────────────────────────────────────────────────────────


def test_make_bus_bridge_translates_executor_dicts_to_typed_events(
    parent_worktree: tuple[Path, str, str],
) -> None:
    path, base_sha, branch = parent_worktree

    async def _run() -> tuple[AutoSplitOutcome, list[Event]]:
        bus = EventLoop()
        seen: list[Event] = []

        async def _collect(event: Event) -> None:
            seen.append(event)

        bus.on(_collect)
        sub_payload = [
            {"id": "3.1-a", "title": "A", "deps_on": []},
            {"id": "3.1-b", "title": "B", "deps_on": []},
        ]
        outcome = await auto_split_and_execute(
            story=_large_story(),
            worktree=path,
            branch=branch,
            base_sha=base_sha,
            decompose_fn=_canned_decomposer(sub_payload),
            bus=bus,
            spawn_fn=_committing_spawn(path),
        )
        # Drain everything emitted during the execute path.
        for _ in range(20):
            ev = await bus.dispatch_one(timeout=0.05)
            if ev is None:
                break
        return outcome, seen

    outcome, events = asyncio.run(_run())
    assert outcome.succeeded is True
    types = [e.type for e in events]
    assert EventType.STORY_SPLIT_TRIGGERED in types
    assert types.count(EventType.SUB_STORY_STARTED) == 2
    assert types.count(EventType.SUB_STORY_COMPLETED) == 2
    assert EventType.SUB_STORY_SQUASH_DONE in types
    # Squash event carries sub_ids + squashed_sha.
    squash_ev = next(e for e in events if e.type == EventType.SUB_STORY_SQUASH_DONE)
    assert squash_ev.payload.get("sub_ids") == ["3.1-a", "3.1-b"]
    assert squash_ev.payload.get("squashed_sha")


def test_bus_bridge_emits_squash_skipped_when_single_commit_squash(
    parent_worktree: tuple[Path, str, str],
) -> None:
    # parent_worktree fixture asserts git plumbing works; the bridge itself
    # does not touch a worktree, so unpacked fields go unused on purpose.
    _ = parent_worktree

    async def _run() -> list[Event]:
        bus = EventLoop()
        seen: list[Event] = []

        async def _collect(event: Event) -> None:
            seen.append(event)

        bus.on(_collect)
        # Single sub-story would be rejected by validate_decomposition (MIN_SUBS=2),
        # so use the bridge directly to verify the skipped translation.
        bridge = make_bus_bridge(bus, parent_story_id="3.1")
        bridge({
            "event_type": "sub_story_squash_skipped",
            "parent_story_id": "3.1",
            "reason": "single_commit",
        })
        for _ in range(5):
            ev = await bus.dispatch_one(timeout=0.05)
            if ev is None:
                break
        return seen

    events = asyncio.run(_run())
    assert any(e.type == EventType.SUB_STORY_SQUASH_SKIPPED for e in events)


def test_bus_bridge_ignores_unknown_event_type() -> None:
    async def _run() -> list[Event]:
        bus = EventLoop()
        seen: list[Event] = []

        async def _collect(event: Event) -> None:
            seen.append(event)

        bus.on(_collect)
        bridge = make_bus_bridge(bus, parent_story_id="3.1")
        bridge({"event_type": "some_unknown_thing"})
        bridge({})  # no event_type at all
        await bus.dispatch_one(timeout=0.05)
        return seen

    events = asyncio.run(_run())
    assert events == []


# ── env-flag toggle ───────────────────────────────────────────────────────────


def test_auto_split_enabled_respects_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default = ON (2026-05-20 — user request «должно быть всегда включена сплит»).

    Явное отключение через ``BMAD_AUTO_SPLIT=0|off|false|no``.
    """
    monkeypatch.delenv(AUTO_SPLIT_ENV_VAR, raising=False)
    assert auto_split_enabled() is True  # default ON
    monkeypatch.setenv(AUTO_SPLIT_ENV_VAR, "1")
    assert auto_split_enabled() is True
    monkeypatch.setenv(AUTO_SPLIT_ENV_VAR, "true")
    assert auto_split_enabled() is True
    monkeypatch.setenv(AUTO_SPLIT_ENV_VAR, "yes")
    assert auto_split_enabled() is True
    # Явные off-значения.
    monkeypatch.setenv(AUTO_SPLIT_ENV_VAR, "0")
    assert auto_split_enabled() is False
    monkeypatch.setenv(AUTO_SPLIT_ENV_VAR, "off")
    assert auto_split_enabled() is False
    monkeypatch.setenv(AUTO_SPLIT_ENV_VAR, "false")
    assert auto_split_enabled() is False
    monkeypatch.setenv(AUTO_SPLIT_ENV_VAR, "no")
    assert auto_split_enabled() is False


def test_auto_split_env_var_unset_returns_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default ON: без env var — auto-split активен."""
    monkeypatch.delenv(AUTO_SPLIT_ENV_VAR, raising=False)
    assert os.environ.get(AUTO_SPLIT_ENV_VAR) is None
    assert auto_split_enabled() is True


# ── agent/run.py decomposer setter ────────────────────────────────────────────


def test_set_decomposer_round_trips() -> None:
    from bmad_orchestrator.agent import run as run_module

    original = run_module.get_decomposer()
    try:
        async def _stub(_p: str, _s: dict[str, Any]) -> str:
            return "[]"

        run_module.set_decomposer(_stub)
        assert run_module.get_decomposer() is _stub
        run_module.set_decomposer(None)
        assert run_module.get_decomposer() is None
    finally:
        run_module.set_decomposer(original)
