"""W4 acceptance tests — code-review gate + auto-merge to integration branch.

Spec: spec/spec_wave_1a_pilot_wiring.md §W4.

Coverage:

* ``EventType.CODE_REVIEW_VERDICT`` is registered (15th event type).
* Verdict parser handles explicit JSON key + ``verdict: <X>`` text patterns.
* :func:`configure_code_review_gate` sets / clears module-level config.
* :func:`code_review_subscriber` filters (WORKER_COMPLETED + success only),
  spawns ``/bmad-code-review`` and emits CODE_REVIEW_VERDICT with the parsed
  verdict; spawn failures degrade to a loud ``error`` verdict.
* :func:`cleanup_worktree` refuses to delete anything outside the configured
  ``.worktrees`` root (path-traversal, root itself, sibling escape).
* :func:`_ff_merge_to_integration` does a fast-forward merge in a real
  ephemeral repo and raises on non-ff or conflict.
* :func:`merge_to_integration_subscriber` happy path (approve → merge + cleanup),
  escalation path (request_changes / reject / error → HUMAN_QUERY) and
  conflict path (merge raises → HUMAN_QUERY with ``verdict=merge_conflict``).
* Grep validations from the spec DoD are asserted at the bottom.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from bmad_orchestrator.agent.run import (
    CODE_REVIEW_SKILL_INVOCATION,
    CODE_REVIEW_VERDICTS,
    _extract_verdict_from_event,
    _ff_merge_to_integration,
    _verdict_from_text,
    code_review_subscriber,
    configure_code_review_gate,
    merge_to_integration_subscriber,
)
from bmad_orchestrator.runtime.event_loop import ALL_EVENT_TYPES, Event, EventLoop, EventType
from bmad_orchestrator.runtime.worker_spawn import WorkerHandle
from bmad_orchestrator.runtime.worktree import cleanup_worktree

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_handle(worktree: str, story_id: str, jsonl_path: Path) -> WorkerHandle:
    return WorkerHandle(
        worktree=worktree,
        story_id=story_id,
        branch=f"feature/{story_id}",
        pid=0,
        jsonl_path=jsonl_path,
        process=None,
        mock=True,
        sandbox_kind="n/a-mock",
    )


def _write_jsonl(path: Path, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(ev) for ev in events) + "\n", encoding="utf-8"
    )


def _collect_emitted(bus: EventLoop) -> list[Event]:
    """Drain all events from the bus queue (non-blocking)."""
    out: list[Event] = []
    while True:
        try:
            out.append(bus.queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return out


@pytest.fixture
def reset_gate_config() -> Iterator[None]:
    """Clear and re-clear the module-level gate config around each test."""
    configure_code_review_gate(target_project=None, wave=None)
    yield
    configure_code_review_gate(target_project=None, wave=None)


# ── 1. Event type registered ────────────────────────────────────────────────


def test_w4_code_review_verdict_event_type_registered() -> None:
    assert EventType.CODE_REVIEW_VERDICT.value == "code_review_verdict"
    assert EventType.CODE_REVIEW_VERDICT in ALL_EVENT_TYPES


# ── 2. Verdict parser ───────────────────────────────────────────────────────


def test_w4_verdict_from_text_approve() -> None:
    assert _verdict_from_text("verdict: approve\n\nLGTM") == "approve"


def test_w4_verdict_from_text_request_changes() -> None:
    assert _verdict_from_text("VERDICT: request_changes — fix typos") == "request_changes"


def test_w4_verdict_from_text_reject() -> None:
    assert _verdict_from_text("Summary line\nverdict = reject") == "reject"


def test_w4_verdict_from_text_returns_none_when_absent() -> None:
    assert _verdict_from_text("nothing relevant here") is None
    assert _verdict_from_text("") is None


# ── 3. _extract_verdict_from_event ──────────────────────────────────────────


def test_w4_extract_verdict_explicit_json_key() -> None:
    res = _extract_verdict_from_event({
        "event_type": "claude_event",
        "verdict": "approve",
        "summary": "all green",
    })
    assert res == ("approve", "all green")


def test_w4_extract_verdict_text_pattern_in_text_field() -> None:
    res = _extract_verdict_from_event({
        "event_type": "claude_event",
        "text": "verdict: request_changes — rename foo",
    })
    assert res is not None
    assert res[0] == "request_changes"


def test_w4_extract_verdict_text_pattern_in_summary_field() -> None:
    res = _extract_verdict_from_event({
        "event_type": "claude_event",
        "summary": "verdict: reject",
    })
    assert res is not None
    assert res[0] == "reject"


def test_w4_extract_verdict_returns_none_when_no_match() -> None:
    assert _extract_verdict_from_event({"event_type": "stdout_line", "text": "hi"}) is None
    assert _extract_verdict_from_event({"event_type": "x"}) is None


def test_w4_extract_verdict_explicit_invalid_value_ignored() -> None:
    # ``verdict: maybe`` is not in the whitelist — must not be returned.
    res = _extract_verdict_from_event({"verdict": "maybe", "text": "verdict: approve"})
    # Falls through to text parsing → finds explicit text approve.
    assert res is not None
    assert res[0] == "approve"


def test_w4_verdict_whitelist_frozen() -> None:
    assert CODE_REVIEW_VERDICTS == frozenset({"approve", "request_changes", "reject"})


# ── 4. configure_code_review_gate ───────────────────────────────────────────


def test_w4_configure_sets_module_config(reset_gate_config: None, tmp_path: Path) -> None:
    from bmad_orchestrator.agent import run as run_mod

    configure_code_review_gate(target_project=tmp_path, wave="1a", escalation_chat_id=42)
    cfg = run_mod._CODE_REVIEW_GATE
    assert cfg is not None
    assert cfg.target_project == tmp_path
    assert cfg.wave == "1a"
    assert cfg.escalation_chat_id == 42


def test_w4_configure_clears_when_required_none(reset_gate_config: None, tmp_path: Path) -> None:
    from bmad_orchestrator.agent import run as run_mod

    configure_code_review_gate(target_project=tmp_path, wave="1a")
    assert run_mod._CODE_REVIEW_GATE is not None
    configure_code_review_gate(target_project=None, wave=None)
    assert run_mod._CODE_REVIEW_GATE is None


# ── 5. code_review_subscriber filtering ─────────────────────────────────────


@pytest.mark.asyncio
async def test_w4_code_review_skips_non_worker_completed(
    reset_gate_config: None, tmp_path: Path
) -> None:
    bus = EventLoop()
    configure_code_review_gate(target_project=tmp_path, wave="1a")
    ev = Event(type=EventType.HUMAN_QUERY, payload={"story_id": "x", "worktree": "/w"})
    await code_review_subscriber(ev, bus)
    await bus.stop()
    assert _collect_emitted(bus) == []


@pytest.mark.asyncio
async def test_w4_code_review_skips_failure_status(
    reset_gate_config: None, tmp_path: Path
) -> None:
    bus = EventLoop()
    configure_code_review_gate(target_project=tmp_path, wave="1a")
    ev = Event(
        type=EventType.WORKER_COMPLETED,
        payload={"story_id": "s1", "worktree": "/w", "status": "failure"},
    )
    await code_review_subscriber(ev, bus)
    await bus.stop()
    assert _collect_emitted(bus) == []


@pytest.mark.asyncio
async def test_w4_code_review_skips_missing_fields(
    reset_gate_config: None, tmp_path: Path
) -> None:
    bus = EventLoop()
    configure_code_review_gate(target_project=tmp_path, wave="1a")
    ev = Event(
        type=EventType.WORKER_COMPLETED,
        payload={"status": "success"},  # missing story_id + worktree
    )
    await code_review_subscriber(ev, bus)
    await bus.stop()
    assert _collect_emitted(bus) == []


# ── 6. code_review_subscriber happy path ────────────────────────────────────


@pytest.mark.asyncio
async def test_w4_code_review_emits_approve_verdict(
    reset_gate_config: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Subscriber spawns code-review worker, parses approve verdict, emits CODE_REVIEW_VERDICT."""
    review_jsonl = tmp_path / "review.jsonl"
    _write_jsonl(
        review_jsonl,
        [
            {"event_type": "claude_event", "text": "Reviewed 3 files…"},
            {"event_type": "claude_event", "verdict": "approve", "summary": "LGTM"},
            {"event_type": "worker_completed", "story_id": "s1", "exit_code": 0, "status": "success"},
        ],
    )

    captured: dict[str, Any] = {}

    async def fake_spawn(*, worktree: str, story_id: str, wave: str) -> WorkerHandle:
        captured["worktree"] = worktree
        captured["story_id"] = story_id
        captured["wave"] = wave
        return _make_handle(worktree, story_id, review_jsonl)

    monkeypatch.setattr("bmad_orchestrator.agent.run._spawn_code_review_worker", fake_spawn)

    configure_code_review_gate(target_project=tmp_path, wave="1a")
    bus = EventLoop()
    ev = Event(
        type=EventType.WORKER_COMPLETED,
        payload={"story_id": "s1", "worktree": str(tmp_path / "wt"), "status": "success"},
    )
    await code_review_subscriber(ev, bus)
    await bus.stop()

    assert captured["story_id"] == "s1"
    assert captured["wave"] == "1a"
    emitted = _collect_emitted(bus)
    assert len(emitted) == 1
    assert emitted[0].type == EventType.CODE_REVIEW_VERDICT
    assert emitted[0].payload["verdict"] == "approve"
    assert emitted[0].payload["summary"] == "LGTM"
    assert emitted[0].payload["story_id"] == "s1"


@pytest.mark.asyncio
async def test_w4_code_review_emits_request_changes_verdict(
    reset_gate_config: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review_jsonl = tmp_path / "review.jsonl"
    _write_jsonl(
        review_jsonl,
        [
            {"event_type": "claude_event", "text": "verdict: request_changes — rename foo"},
            {"event_type": "worker_completed", "story_id": "s2", "exit_code": 0, "status": "success"},
        ],
    )

    async def fake_spawn(*, worktree: str, story_id: str, wave: str) -> WorkerHandle:
        return _make_handle(worktree, story_id, review_jsonl)

    monkeypatch.setattr("bmad_orchestrator.agent.run._spawn_code_review_worker", fake_spawn)

    configure_code_review_gate(target_project=tmp_path, wave="1a")
    bus = EventLoop()
    ev = Event(
        type=EventType.WORKER_COMPLETED,
        payload={"story_id": "s2", "worktree": str(tmp_path / "wt"), "status": "success"},
    )
    await code_review_subscriber(ev, bus)
    await bus.stop()

    emitted = _collect_emitted(bus)
    assert emitted[0].payload["verdict"] == "request_changes"


@pytest.mark.asyncio
async def test_w4_code_review_emits_reject_verdict(
    reset_gate_config: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review_jsonl = tmp_path / "review.jsonl"
    _write_jsonl(
        review_jsonl,
        [
            {"event_type": "claude_event", "verdict": "reject", "summary": "security issue"},
            {"event_type": "worker_completed", "story_id": "s3", "exit_code": 0, "status": "success"},
        ],
    )

    async def fake_spawn(*, worktree: str, story_id: str, wave: str) -> WorkerHandle:
        return _make_handle(worktree, story_id, review_jsonl)

    monkeypatch.setattr("bmad_orchestrator.agent.run._spawn_code_review_worker", fake_spawn)

    configure_code_review_gate(target_project=tmp_path, wave="1a")
    bus = EventLoop()
    ev = Event(
        type=EventType.WORKER_COMPLETED,
        payload={"story_id": "s3", "worktree": str(tmp_path / "wt"), "status": "success"},
    )
    await code_review_subscriber(ev, bus)
    await bus.stop()

    emitted = _collect_emitted(bus)
    assert emitted[0].payload["verdict"] == "reject"
    assert emitted[0].payload["summary"] == "security issue"


@pytest.mark.asyncio
async def test_w4_code_review_spawn_failure_emits_error_verdict(
    reset_gate_config: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spawn raising → subscriber emits ``verdict=error`` payload (no crash)."""

    async def boom(*, worktree: str, story_id: str, wave: str) -> WorkerHandle:
        raise RuntimeError("sandbox unavailable")

    monkeypatch.setattr("bmad_orchestrator.agent.run._spawn_code_review_worker", boom)

    configure_code_review_gate(target_project=tmp_path, wave="1a")
    bus = EventLoop()
    ev = Event(
        type=EventType.WORKER_COMPLETED,
        payload={"story_id": "s4", "worktree": str(tmp_path / "wt"), "status": "success"},
    )
    await code_review_subscriber(ev, bus)
    await bus.stop()

    emitted = _collect_emitted(bus)
    assert len(emitted) == 1
    assert emitted[0].payload["verdict"] == "error"
    assert "sandbox unavailable" in emitted[0].payload["summary"]


# ── 7. cleanup_worktree safety ──────────────────────────────────────────────


def test_w4_cleanup_worktree_under_root_succeeds(tmp_path: Path) -> None:
    root = tmp_path / ".worktrees"
    wt = root / "wt-s1"
    wt.mkdir(parents=True)
    (wt / "marker.txt").write_text("data", encoding="utf-8")

    cleanup_worktree(wt, root=root)
    assert not wt.exists()
    assert root.exists()  # root not destroyed


def test_w4_cleanup_worktree_outside_root_raises(tmp_path: Path) -> None:
    root = tmp_path / ".worktrees"
    root.mkdir()
    outside = tmp_path / "somewhere-else"
    outside.mkdir()
    with pytest.raises(ValueError, match="refusing to cleanup worktree outside"):
        cleanup_worktree(outside, root=root)
    assert outside.exists()  # not deleted


def test_w4_cleanup_worktree_refuses_root_itself(tmp_path: Path) -> None:
    root = tmp_path / ".worktrees"
    root.mkdir()
    with pytest.raises(ValueError, match="refusing to cleanup worktree root itself"):
        cleanup_worktree(root, root=root)
    assert root.exists()


def test_w4_cleanup_worktree_traversal_attempt_raises(tmp_path: Path) -> None:
    root = tmp_path / ".worktrees"
    root.mkdir()
    # path traversal — even constructed as a string with ``..``, the resolve()
    # collapses it to ``tmp_path/etc-mirror`` which is outside root → raises.
    target = root / ".." / "etc-mirror"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError, match="refusing to cleanup worktree outside"):
        cleanup_worktree(target, root=root)
    assert target.exists()


def test_w4_cleanup_worktree_nonexistent_path_is_noop(tmp_path: Path) -> None:
    root = tmp_path / ".worktrees"
    root.mkdir()
    # Non-existent path under root → noop (idempotent).
    cleanup_worktree(root / "wt-missing", root=root)


def test_w4_cleanup_worktree_refuses_absolute_etc(tmp_path: Path) -> None:
    """A path under /etc must raise — defends against payload tampering."""
    root = tmp_path / ".worktrees"
    root.mkdir()
    with pytest.raises(ValueError, match="refusing to cleanup worktree outside"):
        cleanup_worktree(Path("/etc"), root=root)


# ── 8. _ff_merge_to_integration with real git ───────────────────────────────


def _git(repo_path: Path, *args: str) -> str:
    """Run a git command inside ``repo_path``; raise on non-zero with output."""
    cp = subprocess.run(
        ["git", *args],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
    )
    return cp.stdout.strip()


def _init_repo_with_feature(tmp_path: Path, story_id: str = "s1") -> Path:
    """Build a tmp git repo: main commit, feature/<story> branch with one extra commit."""
    repo = tmp_path / "target"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@test.local")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "commit", "--allow-empty", "-m", "initial")
    _git(repo, "checkout", "-b", f"feature/{story_id}")
    (repo / "code.py").write_text("print('hi')\n", encoding="utf-8")
    _git(repo, "add", "code.py")
    _git(repo, "commit", "-m", f"feat: {story_id}")
    _git(repo, "checkout", "main")
    return repo


@pytest.mark.asyncio
async def test_w4_ff_merge_creates_integration_branch_when_missing(tmp_path: Path) -> None:
    repo = _init_repo_with_feature(tmp_path)
    sha = await _ff_merge_to_integration(
        target_project=repo,
        integration_branch="integration/1a",
        feature_branch="feature/s1",
    )
    # After merge integration HEAD == feature HEAD.
    expected = _git(repo, "rev-parse", "feature/s1")
    assert sha == expected
    # Branch list now includes integration/1a.
    branches = _git(repo, "branch").splitlines()
    assert any("integration/1a" in b for b in branches)


@pytest.mark.asyncio
async def test_w4_ff_merge_advances_existing_integration_branch(tmp_path: Path) -> None:
    repo = _init_repo_with_feature(tmp_path)
    _git(repo, "branch", "integration/1a", "main")  # pre-create at main
    sha = await _ff_merge_to_integration(
        target_project=repo,
        integration_branch="integration/1a",
        feature_branch="feature/s1",
    )
    assert sha == _git(repo, "rev-parse", "feature/s1")


@pytest.mark.asyncio
async def test_w4_ff_merge_raises_on_non_fast_forward(tmp_path: Path) -> None:
    """Integration branch has diverged from feature → ff-only refuses."""
    repo = _init_repo_with_feature(tmp_path)
    # Create integration with an independent commit so ff is impossible.
    _git(repo, "checkout", "-b", "integration/1a", "main")
    (repo / "other.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "other.py")
    _git(repo, "commit", "-m", "divergent")
    _git(repo, "checkout", "main")

    with pytest.raises(Exception):  # GitCommandError — must surface
        await _ff_merge_to_integration(
            target_project=repo,
            integration_branch="integration/1a",
            feature_branch="feature/s1",
        )
    # Integration HEAD must remain at the divergent commit, not feature.
    integration_sha = _git(repo, "rev-parse", "integration/1a")
    feature_sha = _git(repo, "rev-parse", "feature/s1")
    assert integration_sha != feature_sha


@pytest.mark.asyncio
async def test_w4_ff_merge_raises_on_missing_feature_branch(tmp_path: Path) -> None:
    repo = _init_repo_with_feature(tmp_path)
    with pytest.raises(Exception):
        await _ff_merge_to_integration(
            target_project=repo,
            integration_branch="integration/1a",
            feature_branch="feature/does-not-exist",
        )


# ── 9. merge_to_integration_subscriber ──────────────────────────────────────


@pytest.mark.asyncio
async def test_w4_merge_subscriber_filters_non_verdict_event(
    reset_gate_config: None, tmp_path: Path
) -> None:
    bus = EventLoop()
    configure_code_review_gate(target_project=tmp_path, wave="1a")
    ev = Event(type=EventType.WORKER_COMPLETED, payload={"story_id": "s1"})
    await merge_to_integration_subscriber(ev, bus)
    await bus.stop()
    assert _collect_emitted(bus) == []


@pytest.mark.asyncio
async def test_w4_merge_subscriber_unconfigured_noop(reset_gate_config: None) -> None:
    bus = EventLoop()
    ev = Event(
        type=EventType.CODE_REVIEW_VERDICT,
        payload={"story_id": "s1", "verdict": "approve", "worktree": "/w"},
    )
    await merge_to_integration_subscriber(ev, bus)
    await bus.stop()
    assert _collect_emitted(bus) == []


@pytest.mark.asyncio
async def test_w4_merge_subscriber_request_changes_emits_human_query(
    reset_gate_config: None, tmp_path: Path
) -> None:
    bus = EventLoop()
    configure_code_review_gate(target_project=tmp_path, wave="1a", escalation_chat_id=99)
    ev = Event(
        type=EventType.CODE_REVIEW_VERDICT,
        payload={
            "story_id": "s1",
            "verdict": "request_changes",
            "summary": "fix naming",
            "worktree": "/w",
        },
    )
    await merge_to_integration_subscriber(ev, bus)
    await bus.stop()

    emitted = _collect_emitted(bus)
    assert len(emitted) == 1
    payload = emitted[0].payload
    assert emitted[0].type == EventType.HUMAN_QUERY
    assert payload["verdict"] == "request_changes"
    assert payload["story_id"] == "s1"
    assert payload["chat_id"] == 99
    assert "approve_override" in payload["actions"]
    assert "abandon" in payload["actions"]
    assert "edit_in_human_loop" in payload["actions"]


@pytest.mark.asyncio
async def test_w4_merge_subscriber_reject_emits_human_query(
    reset_gate_config: None, tmp_path: Path
) -> None:
    bus = EventLoop()
    configure_code_review_gate(target_project=tmp_path, wave="1a", escalation_chat_id=99)
    ev = Event(
        type=EventType.CODE_REVIEW_VERDICT,
        payload={"story_id": "s2", "verdict": "reject", "summary": "security", "worktree": "/w"},
    )
    await merge_to_integration_subscriber(ev, bus)
    await bus.stop()
    emitted = _collect_emitted(bus)
    assert len(emitted) == 1
    assert emitted[0].type == EventType.HUMAN_QUERY
    assert emitted[0].payload["verdict"] == "reject"


@pytest.mark.asyncio
async def test_w4_merge_subscriber_error_verdict_emits_human_query(
    reset_gate_config: None, tmp_path: Path
) -> None:
    bus = EventLoop()
    configure_code_review_gate(target_project=tmp_path, wave="1a")
    ev = Event(
        type=EventType.CODE_REVIEW_VERDICT,
        payload={"story_id": "s3", "verdict": "error", "summary": "spawn failed", "worktree": "/w"},
    )
    await merge_to_integration_subscriber(ev, bus)
    await bus.stop()
    emitted = _collect_emitted(bus)
    assert emitted[0].type == EventType.HUMAN_QUERY
    assert emitted[0].payload["verdict"] == "error"


@pytest.mark.asyncio
async def test_w4_merge_subscriber_approve_ff_merge_and_cleanup(
    reset_gate_config: None, tmp_path: Path
) -> None:
    """Happy path — verdict=approve → ff-merge succeeds → worktree dir is removed."""
    repo = _init_repo_with_feature(tmp_path, story_id="s1")
    worktrees_root = repo / ".worktrees"
    worktrees_root.mkdir()
    wt = worktrees_root / "wt-s1"
    wt.mkdir()
    (wt / "marker.txt").write_text("present", encoding="utf-8")

    configure_code_review_gate(target_project=repo, wave="1a")
    bus = EventLoop()
    ev = Event(
        type=EventType.CODE_REVIEW_VERDICT,
        payload={"story_id": "s1", "verdict": "approve", "summary": "LGTM", "worktree": str(wt)},
    )
    await merge_to_integration_subscriber(ev, bus)
    await bus.stop()

    # Integration branch advanced to feature HEAD.
    integration_sha = _git(repo, "rev-parse", "integration/1a")
    feature_sha = _git(repo, "rev-parse", "feature/s1")
    assert integration_sha == feature_sha

    # Worktree directory was cleaned up.
    assert not wt.exists()
    # Worktrees root preserved.
    assert worktrees_root.exists()

    # No HUMAN_QUERY emitted on the happy path.
    emitted = _collect_emitted(bus)
    assert emitted == []


@pytest.mark.asyncio
async def test_w4_merge_subscriber_conflict_emits_human_query(
    reset_gate_config: None, tmp_path: Path
) -> None:
    """Non-ff scenario → merge raises → escalation HUMAN_QUERY with verdict=merge_conflict."""
    repo = _init_repo_with_feature(tmp_path, story_id="s1")
    _git(repo, "checkout", "-b", "integration/1a", "main")
    (repo / "other.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "other.py")
    _git(repo, "commit", "-m", "divergent")
    _git(repo, "checkout", "main")

    worktrees_root = repo / ".worktrees"
    worktrees_root.mkdir()
    wt = worktrees_root / "wt-s1"
    wt.mkdir()

    configure_code_review_gate(target_project=repo, wave="1a", escalation_chat_id=7)
    bus = EventLoop()
    ev = Event(
        type=EventType.CODE_REVIEW_VERDICT,
        payload={"story_id": "s1", "verdict": "approve", "summary": "", "worktree": str(wt)},
    )
    await merge_to_integration_subscriber(ev, bus)
    await bus.stop()

    emitted = _collect_emitted(bus)
    assert len(emitted) == 1
    assert emitted[0].type == EventType.HUMAN_QUERY
    assert emitted[0].payload["verdict"] == "merge_conflict"
    assert emitted[0].payload["chat_id"] == 7
    assert "manual_resolve" in emitted[0].payload["actions"]
    # Worktree must NOT be cleaned up when merge failed.
    assert wt.exists()
    # Integration branch must not have advanced.
    integration_sha = _git(repo, "rev-parse", "integration/1a")
    feature_sha = _git(repo, "rev-parse", "feature/s1")
    assert integration_sha != feature_sha


@pytest.mark.asyncio
async def test_w4_merge_subscriber_missing_story_id_noop(
    reset_gate_config: None, tmp_path: Path
) -> None:
    bus = EventLoop()
    configure_code_review_gate(target_project=tmp_path, wave="1a")
    ev = Event(
        type=EventType.CODE_REVIEW_VERDICT,
        payload={"verdict": "approve", "worktree": "/w"},  # no story_id
    )
    await merge_to_integration_subscriber(ev, bus)
    await bus.stop()
    assert _collect_emitted(bus) == []


@pytest.mark.asyncio
async def test_w4_merge_subscriber_cleanup_failure_does_not_block_merge(
    reset_gate_config: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If cleanup raises, the merge still counts as success (no HUMAN_QUERY)."""
    repo = _init_repo_with_feature(tmp_path, story_id="s1")
    worktrees_root = repo / ".worktrees"
    worktrees_root.mkdir()
    wt = worktrees_root / "wt-s1"
    wt.mkdir()

    def fail_cleanup(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("bmad_orchestrator.agent.run.cleanup_worktree", fail_cleanup)

    configure_code_review_gate(target_project=repo, wave="1a")
    bus = EventLoop()
    ev = Event(
        type=EventType.CODE_REVIEW_VERDICT,
        payload={"story_id": "s1", "verdict": "approve", "summary": "", "worktree": str(wt)},
    )
    await merge_to_integration_subscriber(ev, bus)
    await bus.stop()

    # Merge must have succeeded (integration advanced).
    assert _git(repo, "rev-parse", "integration/1a") == _git(repo, "rev-parse", "feature/s1")
    # No HUMAN_QUERY emitted — cleanup failure is downgraded to a warn log.
    assert _collect_emitted(bus) == []


# ── 10. Skill invocation literal ─────────────────────────────────────────────


def test_w4_code_review_skill_invocation_literal() -> None:
    assert CODE_REVIEW_SKILL_INVOCATION == "/bmad-code-review"


# ── 11. Grep validation (W4 DoD) ────────────────────────────────────────────


def test_w4_subscribers_defined_and_wired() -> None:
    """W4 + F1-P0-1: both subscribers must (a) be defined as async functions
    and (b) be wired into the EventLoop via ``partial(...)`` inside
    ``_run_real_pilot``. The earlier assertion that they appeared on
    exactly 2 lines silently sanctioned the P0 bug where the subscribers
    were defined but never registered with ``bus.on(...)``."""
    src = (
        Path(__file__).parent.parent
        / "src"
        / "bmad_orchestrator"
        / "agent"
        / "run.py"
    ).read_text(encoding="utf-8")
    assert "async def code_review_subscriber" in src
    assert "async def merge_to_integration_subscriber" in src
    assert "partial(code_review_subscriber, bus=bus)" in src
    assert "partial(merge_to_integration_subscriber, bus=bus)" in src


def test_w4_grep_event_type_in_event_loop() -> None:
    src = (
        Path(__file__).parent.parent
        / "src"
        / "bmad_orchestrator"
        / "runtime"
        / "event_loop.py"
    ).read_text(encoding="utf-8")
    assert "CODE_REVIEW_VERDICT" in src


def test_w4_grep_ff_only_present() -> None:
    src = (
        Path(__file__).parent.parent
        / "src"
        / "bmad_orchestrator"
        / "agent"
        / "run.py"
    ).read_text(encoding="utf-8")
    assert '"--ff-only"' in src


def test_w4_grep_no_destructive_flags() -> None:
    """W4 DoD — agent/run.py must contain ZERO references to no-verify, --force, reset --hard."""
    src = (
        Path(__file__).parent.parent
        / "src"
        / "bmad_orchestrator"
        / "agent"
        / "run.py"
    ).read_text(encoding="utf-8")
    hits = sum(
        1
        for line in src.splitlines()
        if "no-verify" in line or "--force" in line or "reset --hard" in line
    )
    assert hits == 0, f"expected 0, got {hits} (security regression)"
