"""W5 acceptance tests — bot real-mode + e2e smoke + production launcher docs.

Spec: spec/spec_wave_1a_pilot_wiring.md §W5.

Coverage:

* :meth:`EventLoop.subscribe_one_correlation` — single-shot future resolved on
  the first :data:`EventType.HUMAN_RESPONSE` whose payload's ``corr_id``
  matches. Non-matching events, mismatched corr_ids, and non-string corr_ids
  are ignored. Re-subscribing for the same corr_id while the original future is
  pending returns the same future; once done the slot is replaced. The
  resolution fires from both ``emit()`` (producer-side) and ``dispatch_one``
  (consumer-side).
* :meth:`EventLoop.unsubscribe_correlation` — idempotent cleanup.
* :func:`forward_to_agent` bus-bridge path — emits ``USER_CHAT_MESSAGE``
  (not the legacy ``HUMAN_QUERY``) and awaits ``HUMAN_RESPONSE`` via
  :meth:`subscribe_one_correlation`. Timeout falls back to a friendly string.
  Per-chat and global DoS caps still apply. Future is unsubscribed on exit
  (no leak under timeout / success / cancellation).
* End-to-end synthetic Odyssey wave — fake target project + fake spawn →
  ``WORKER_COMPLETED`` → :func:`code_review_subscriber` (mocked spawn → approve
  verdict) → :func:`merge_to_integration_subscriber` → fast-forward merge
  on a real git repo → ``story_merged`` audit log line, integration branch
  carries the feature commit.
* :file:`docs/production-launcher.md` exists.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from bmad_orchestrator.agent.run import (
    code_review_subscriber,
    configure_code_review_gate,
    merge_to_integration_subscriber,
)
from bmad_orchestrator.bot import handlers as bot_handlers
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.runtime.worker_spawn import WorkerHandle

# ── Helpers ──────────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> str:
    cp = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return cp.stdout.strip()


def _init_repo_with_feature(tmp_path: Path, story_id: str = "wt-test-1.1") -> Path:
    """Minimal git repo with main + feature/<story> branches."""
    repo = tmp_path / "odyssey"
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


def _drain(bus: EventLoop) -> list[Event]:
    out: list[Event] = []
    while True:
        try:
            out.append(bus.queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return out


# ── 1. subscribe_one_correlation — basic resolution ─────────────────────────


@pytest.mark.asyncio
async def test_w5_subscribe_one_correlation_returns_future() -> None:
    bus = EventLoop()
    fut = bus.subscribe_one_correlation("abc")
    assert isinstance(fut, asyncio.Future)
    assert not fut.done()
    bus.unsubscribe_correlation("abc")


@pytest.mark.asyncio
async def test_w5_subscribe_one_correlation_resolves_on_matching_human_response() -> None:
    bus = EventLoop()
    fut = bus.subscribe_one_correlation("abc123")
    await bus.emit(
        EventType.HUMAN_RESPONSE,
        chat_id=42,
        corr_id="abc123",
        text="hello",
    )
    event = await asyncio.wait_for(fut, timeout=1.0)
    assert event.type is EventType.HUMAN_RESPONSE
    assert event.payload["text"] == "hello"


@pytest.mark.asyncio
async def test_w5_subscribe_one_correlation_ignored_for_non_human_response() -> None:
    """Same corr_id on a non-HUMAN_RESPONSE event MUST NOT resolve the future."""
    bus = EventLoop()
    fut = bus.subscribe_one_correlation("abc")
    await bus.emit(
        EventType.USER_CHAT_MESSAGE,
        chat_id=1,
        corr_id="abc",
        text="ping",
    )
    await asyncio.sleep(0)
    assert not fut.done()
    bus.unsubscribe_correlation("abc")


@pytest.mark.asyncio
async def test_w5_subscribe_one_correlation_ignored_for_mismatched_corr_id() -> None:
    bus = EventLoop()
    fut = bus.subscribe_one_correlation("abc")
    await bus.emit(
        EventType.HUMAN_RESPONSE,
        chat_id=1,
        corr_id="xyz",
        text="not for you",
    )
    await asyncio.sleep(0)
    assert not fut.done()
    bus.unsubscribe_correlation("abc")


@pytest.mark.asyncio
async def test_w5_subscribe_one_correlation_returns_same_future_for_pending_corr_id() -> None:
    bus = EventLoop()
    fut1 = bus.subscribe_one_correlation("abc")
    fut2 = bus.subscribe_one_correlation("abc")
    assert fut1 is fut2
    bus.unsubscribe_correlation("abc")


@pytest.mark.asyncio
async def test_w5_subscribe_one_correlation_replaces_done_future() -> None:
    """Stale (done) future for a corr_id is replaced by a fresh one."""
    bus = EventLoop()
    fut1 = bus.subscribe_one_correlation("abc")
    await bus.emit(EventType.HUMAN_RESPONSE, chat_id=1, corr_id="abc", text="first")
    await asyncio.wait_for(fut1, timeout=1.0)
    fut2 = bus.subscribe_one_correlation("abc")
    assert fut1 is not fut2
    assert not fut2.done()
    bus.unsubscribe_correlation("abc")


@pytest.mark.asyncio
async def test_w5_unsubscribe_correlation_removes_future() -> None:
    bus = EventLoop()
    bus.subscribe_one_correlation("abc")
    bus.unsubscribe_correlation("abc")
    await bus.emit(EventType.HUMAN_RESPONSE, chat_id=1, corr_id="abc", text="late")
    # Nothing crashed; corr_id slot is empty so emit is a no-op resolver.
    assert "abc" not in bus._corr_futures


def test_w5_unsubscribe_correlation_idempotent() -> None:
    bus = EventLoop()
    bus.unsubscribe_correlation("never-registered")
    bus.unsubscribe_correlation("never-registered")  # still no error


@pytest.mark.asyncio
async def test_w5_subscribe_one_correlation_resolves_via_dispatch_one() -> None:
    """If emit somehow misses (corr registered after emit), dispatch_one resolves."""
    bus = EventLoop()
    # Pre-queue an event (bypasses _resolve_correlation invoked in emit).
    bus.queue.put_nowait(
        Event(
            type=EventType.HUMAN_RESPONSE,
            payload={"chat_id": 1, "corr_id": "late-corr", "text": "deferred"},
        )
    )
    fut = bus.subscribe_one_correlation("late-corr")
    out = await bus.dispatch_one(timeout=1.0)
    assert out is not None
    event = await asyncio.wait_for(fut, timeout=1.0)
    assert event.payload["text"] == "deferred"


@pytest.mark.asyncio
async def test_w5_subscribe_one_correlation_ignores_non_string_corr_id() -> None:
    bus = EventLoop()
    fut = bus.subscribe_one_correlation("abc")
    # corr_id is an int — not a string. Resolver must skip.
    await bus.emit(EventType.HUMAN_RESPONSE, chat_id=1, corr_id=123, text="bad")
    await asyncio.sleep(0)
    assert not fut.done()
    bus.unsubscribe_correlation("abc")


# ── 2. Bot flow — USER_CHAT_MESSAGE + subscribe_one_correlation ────────────


@pytest.mark.asyncio
async def test_w5_forward_to_agent_bus_emits_user_chat_message() -> None:
    """Bot bus-bridge emits USER_CHAT_MESSAGE (W5 contract), not HUMAN_QUERY."""
    bus = EventLoop()
    bot_handlers.attach_event_loop(bus)
    bot_handlers.attach_state_db(None, None)
    bot_handlers.reset_for_test()
    try:
        async def _responder() -> None:
            ev = await bus.next(timeout=2.0)
            assert ev is not None
            assert ev.type is EventType.USER_CHAT_MESSAGE
            assert ev.payload["chat_id"] == 999
            assert ev.payload["text"] == "привет"
            await bus.emit(
                EventType.HUMAN_RESPONSE,
                chat_id=999,
                corr_id=ev.payload["corr_id"],
                text="response_text",
            )

        responder = asyncio.create_task(_responder())
        reply = await bot_handlers.forward_to_agent(
            "привет", chat_id=999, source="text", timeout=3.0
        )
        await responder
        assert reply == "response_text"
    finally:
        bot_handlers.attach_event_loop(None)
        bot_handlers.reset_for_test()


@pytest.mark.asyncio
async def test_w5_forward_to_agent_bus_timeout_returns_fallback() -> None:
    bus = EventLoop()
    bot_handlers.attach_event_loop(bus)
    bot_handlers.attach_state_db(None, None)
    bot_handlers.reset_for_test()
    try:
        reply = await bot_handlers.forward_to_agent(
            "no-one-replies", chat_id=42, source="text", timeout=0.05
        )
        assert "не ответил" in reply
        # Future must be cleaned up — no leak.
        assert not bus._corr_futures
    finally:
        bot_handlers.attach_event_loop(None)
        bot_handlers.reset_for_test()


@pytest.mark.asyncio
async def test_w5_forward_to_agent_bus_per_chat_cap_rejects_excess() -> None:
    """Single chat may not exceed ``_PER_CHAT_CAP`` simultaneous in-flight queries."""
    bus = EventLoop()
    bot_handlers.attach_event_loop(bus)
    bot_handlers.attach_state_db(None, None)
    bot_handlers.reset_for_test()
    try:
        fillers = [
            asyncio.create_task(
                bot_handlers.forward_to_agent(
                    f"m{i}", chat_id=42, source="text", timeout=5.0
                )
            )
            for i in range(bot_handlers._PER_CHAT_CAP)
        ]
        # Drain emitted USER_CHAT_MESSAGE events so the queue does not back up.
        for _ in range(bot_handlers._PER_CHAT_CAP):
            ev = await bus.next(timeout=1.0)
            assert ev is not None
            assert ev.type is EventType.USER_CHAT_MESSAGE

        reply = await bot_handlers.forward_to_agent(
            "overflow", chat_id=42, source="text", timeout=1.0
        )
        assert reply == "queue_full"
    finally:
        for t in fillers:
            t.cancel()
        await asyncio.gather(*fillers, return_exceptions=True)
        bot_handlers.attach_event_loop(None)
        bot_handlers.reset_for_test()


@pytest.mark.asyncio
async def test_w5_forward_to_agent_bus_unsubscribes_after_response() -> None:
    """After a successful response, the bot must drop its corr future from the bus."""
    bus = EventLoop()
    bot_handlers.attach_event_loop(bus)
    bot_handlers.attach_state_db(None, None)
    bot_handlers.reset_for_test()
    try:
        async def _responder() -> None:
            ev = await bus.next(timeout=2.0)
            assert ev is not None
            await bus.emit(
                EventType.HUMAN_RESPONSE,
                chat_id=7,
                corr_id=ev.payload["corr_id"],
                text="done",
            )

        responder = asyncio.create_task(_responder())
        await bot_handlers.forward_to_agent(
            "ping", chat_id=7, source="text", timeout=3.0
        )
        await responder
        # All corr futures dropped — the single-shot subscription has been
        # cleaned up by either resolution-pop OR the bot's `finally`.
        assert not bus._corr_futures
        # In-flight counter for chat 7 cleared.
        assert bot_handlers._BUS_INFLIGHT.get(7, 0) == 0
    finally:
        bot_handlers.attach_event_loop(None)
        bot_handlers.reset_for_test()


# ── 3. End-to-end synthetic Odyssey wave ────────────────────────────────────


@pytest.mark.asyncio
async def test_w5_e2e_synthetic_wave(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Synthetic Odyssey wave: WORKER_COMPLETED → code-review approve → ff merge.

    Asserts the full subscriber chain wires the contract pieces (W2 / W4 / W5):
    1. ``code_review_subscriber`` consumes WORKER_COMPLETED + status=success.
    2. Spawns a (mocked) code-review worker whose JSONL emits ``approve``.
    3. Emits ``CODE_REVIEW_VERDICT(approve)``.
    4. ``merge_to_integration_subscriber`` consumes the verdict.
    5. Calls ``_ff_merge_to_integration`` on the real git repo — feature
       branch's commit lands on ``integration/<wave>``.

    No real ``claude`` subprocess is spawned (the code-review spawn is
    monkey-patched). The real git binary is the only external dependency.
    """
    repo = _init_repo_with_feature(tmp_path, story_id="wt-test-1.1")
    # Place a fake worktree dir under the project's .worktrees/ root so the
    # merge subscriber's cleanup path validation succeeds (path must be under
    # `target_project/.worktrees/`).
    worktree_root = repo / ".worktrees"
    worktree_root.mkdir()
    worktree = worktree_root / "wt-test-1.1"
    worktree.mkdir()

    review_jsonl = tmp_path / "review.jsonl"
    _write_jsonl(
        review_jsonl,
        [
            {"event_type": "claude_event", "text": "code review pass 1"},
            {
                "event_type": "claude_event",
                "verdict": "approve",
                "summary": "LGTM — ships W5 pilot wiring",
            },
            {
                "event_type": "worker_completed",
                "story_id": "wt-test-1.1",
                "exit_code": 0,
                "status": "success",
            },
        ],
    )

    async def fake_spawn(*, worktree: str, story_id: str, wave: str) -> WorkerHandle:
        return _make_handle(worktree, story_id, review_jsonl)

    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_code_review_worker", fake_spawn
    )

    configure_code_review_gate(target_project=repo, wave="1a")
    bus = EventLoop()

    # 1. Worker (dev) completes → emit synthetic WORKER_COMPLETED.
    worker_completed = Event(
        type=EventType.WORKER_COMPLETED,
        payload={
            "story_id": "wt-test-1.1",
            "worktree": str(worktree),
            "status": "success",
        },
    )
    await code_review_subscriber(worker_completed, bus)

    # 2. Pull verdict off bus, run merge subscriber.
    verdict_event = await bus.next(timeout=1.0)
    assert verdict_event is not None
    assert verdict_event.type is EventType.CODE_REVIEW_VERDICT
    assert verdict_event.payload["verdict"] == "approve"
    assert verdict_event.payload["story_id"] == "wt-test-1.1"

    await merge_to_integration_subscriber(verdict_event, bus)
    await bus.stop()

    # 3. integration/1a branch carries the feature commit.
    feature_sha = _git(repo, "rev-parse", "feature/wt-test-1.1")
    integration_sha = _git(repo, "rev-parse", "integration/1a")
    assert integration_sha == feature_sha, (
        f"integration/1a HEAD {integration_sha} != feature/wt-test-1.1 HEAD {feature_sha}"
    )

    # 4. Worktree cleaned up post-merge.
    assert not worktree.exists(), "merge subscriber should have removed the worktree"


# ── 4. Grep DoD assertions ───────────────────────────────────────────────────


def test_w5_grep_subscribe_one_correlation_in_event_loop() -> None:
    """DoD: ``grep -c subscribe_one_correlation runtime/event_loop.py`` ≥ 1."""
    repo_root = Path(__file__).parent.parent
    body = (repo_root / "src/bmad_orchestrator/runtime/event_loop.py").read_text(
        encoding="utf-8"
    )
    assert body.count("subscribe_one_correlation") >= 1


def test_w5_grep_user_chat_message_or_wait_for_in_bot_handlers() -> None:
    """DoD: ``grep -c 'USER_CHAT_MESSAGE\\|asyncio.wait_for' bot/handlers.py`` ≥ 1."""
    repo_root = Path(__file__).parent.parent
    body = (repo_root / "src/bmad_orchestrator/bot/handlers.py").read_text(
        encoding="utf-8"
    )
    assert (
        body.count("USER_CHAT_MESSAGE") + body.count("asyncio.wait_for") >= 1
    )


def test_w5_production_launcher_doc_exists() -> None:
    """DoD: ``test -f docs/production-launcher.md``."""
    repo_root = Path(__file__).parent.parent
    assert (repo_root / "docs" / "production-launcher.md").is_file()


# ── 5. Configuration sanity ─────────────────────────────────────────────────


def test_w5_user_chat_message_event_type_registered() -> None:
    """``USER_CHAT_MESSAGE`` is a defined event type (no enum regression)."""
    assert EventType.USER_CHAT_MESSAGE.value == "user_chat_message"


def test_w5_human_response_event_type_registered() -> None:
    """``HUMAN_RESPONSE`` is a defined event type (no enum regression)."""
    assert EventType.HUMAN_RESPONSE.value == "human_response"
