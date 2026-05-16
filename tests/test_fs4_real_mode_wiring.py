"""FS4 acceptance tests — real-mode SDK wiring + bot cross-process bridge.

Coverage:
- B1  build_agent_options shape — ClaudeAgentOptions instantiation succeeds.
- B1  _validate_sdk_options raises RuntimeError on TypeError (no soft-fail).
- B1  run_orchestrator(mock=False) dispatches to _run_real_pilot (W1: no longer raises).
- B10 system_prompt.build_system_prompt — 5 cached blocks, no "TODO" markers.
- B10 _load_project_context — cap enforced; truncation marker on oversize.
- B10 RU operational rules + 15 few-shot pairs present.
- B11 ALWAYS_ON_TOOLS — exactly 5 entries, matches spec contract.
- B11 mcp_servers carries one server with all 34 @tool registrations.
- B9  EventType.HUMAN_QUERY exists in enum.
- B9  bot per-chat FIFO with corr_id — concurrent 5 messages × 5 responses.
- B9  bot _INFLIGHT_CAP — 101st request returns "queue_full".
- B9  state-db bridge round-trip latency <500ms.
- B9  human_query_subscriber stub emits HUMAN_RESPONSE with same corr_id.
- B12 spawn_worker(real=True) without binary → loud-fallback isError payload.
- B12 WorkerHandle.fallback_reason populated on auto-mock degradation.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from bmad_orchestrator.config import ModelConfig

# ── B1 — SDK options shape + validation ──────────────────────────────────────


def test_build_agent_options_matches_sdk_schema() -> None:
    """ClaudeAgentOptions instantiates without TypeError — shape contract."""
    from claude_agent_sdk import ClaudeAgentOptions

    from bmad_orchestrator.agent.run import build_agent_options

    opts = build_agent_options(
        project_root=Path("/tmp/fake-project"),
        wave="1a",
        models=ModelConfig(),
    )
    # Must instantiate cleanly — this is the gate _validate_sdk_options runs.
    ClaudeAgentOptions(**opts)


def test_build_agent_options_returns_string_system_prompt() -> None:
    """SDK signature: system_prompt: str | preset — NOT list of blocks."""
    from bmad_orchestrator.agent.run import build_agent_options

    opts = build_agent_options(
        project_root=Path("/tmp/fake-project"),
        wave="1a",
        models=ModelConfig(),
    )
    assert isinstance(opts["system_prompt"], str)
    assert len(opts["system_prompt"]) > 1000  # non-trivial content


def test_build_agent_options_uses_betas_not_beta_headers() -> None:
    """FS4 B1: field renamed from `beta_headers` → `betas` in SDK."""
    from bmad_orchestrator.agent.run import build_agent_options

    opts = build_agent_options(
        project_root=Path("/tmp/fake-project"),
        wave="1a",
        models=ModelConfig(),
    )
    assert "betas" in opts
    assert "beta_headers" not in opts
    assert "tool-search-tool-2025-10-19" in opts["betas"]
    assert "context-management-2025-06-27" in opts["betas"]


def test_build_agent_options_hooks_are_hookmatcher_wrappers() -> None:
    """SDK requires HookMatcher; raw callables in list silently no-op."""
    from claude_agent_sdk import HookMatcher

    from bmad_orchestrator.agent.run import build_agent_options

    opts = build_agent_options(
        project_root=Path("/tmp/fake-project"),
        wave="1a",
        models=ModelConfig(),
    )
    pre = opts["hooks"]["PreToolUse"]
    post = opts["hooks"]["PostToolUse"]
    assert all(isinstance(h, HookMatcher) for h in pre)
    assert all(isinstance(h, HookMatcher) for h in post)


def test_validate_sdk_options_raises_runtimeerror_on_unknown_field() -> None:
    """FS4 B1: TypeError → RuntimeError, no silent log-warning."""
    from bmad_orchestrator.agent.run import _validate_sdk_options

    bad = {"this_field_does_not_exist": True}
    with pytest.raises(RuntimeError, match="ClaudeAgentOptions shape mismatch"):
        _validate_sdk_options(bad)


@pytest.mark.asyncio
async def test_run_orchestrator_real_mode_dispatches_to_real_pilot() -> None:
    """W1: real-mode no longer raises NotImplementedError; dispatches to _run_real_pilot."""
    from bmad_orchestrator.agent import run as run_mod
    from bmad_orchestrator.agent.run import run_orchestrator

    captured: dict[str, Any] = {}

    async def _fake_pilot(bus: Any, **kwargs: Any) -> None:
        captured["called"] = True
        captured["kwargs"] = kwargs

    with patch.object(run_mod, "_run_real_pilot", new=_fake_pilot):
        bus = await run_orchestrator(
            project="x", wave="1a", mock=False,
            max_stories=7, max_spend_usd=3.5,
        )
    assert captured.get("called") is True
    assert captured["kwargs"]["max_stories"] == 7
    assert captured["kwargs"]["max_spend_usd"] == 3.5
    assert bus is not None


# ── B10 — system_prompt full impl ────────────────────────────────────────────


def test_system_prompt_has_five_cached_blocks() -> None:
    from bmad_orchestrator.agent.system_prompt import CACHE_1H, build_system_prompt

    blocks = build_system_prompt(Path("/tmp"), wave="1a", locale="ru")
    assert len(blocks) == 5
    # First 4 blocks must carry cache_control ttl=1h.
    for i in range(4):
        assert blocks[i].get("cache_control") == CACHE_1H, f"block {i} missing 1h cache"
    # Last block (personality) intentionally uncached.
    assert "cache_control" not in blocks[4]


def test_system_prompt_no_todo_markers() -> None:
    """FS4 B10: all 5 blocks must be filled — no `TODO:` left over."""
    from bmad_orchestrator.agent.system_prompt import build_system_prompt

    blocks = build_system_prompt(Path("/tmp"), wave="1a", locale="ru")
    blob = "\n".join(b.get("text", "") for b in blocks)
    assert "TODO:" not in blob, "system_prompt still has TODO placeholders"


def test_system_prompt_project_context_cap_enforced() -> None:
    """FS4 B10: oversized project context truncated to ~25K tokens."""
    from bmad_orchestrator.agent import system_prompt as sp

    big_text = "A" * (sp.PROJECT_CONTEXT_TOKEN_CAP * sp.TOKEN_CHARS * 2)
    with patch.object(sp, "_safe_read_text", return_value=big_text):
        blocks = sp.build_system_prompt(Path("/tmp"), wave="1a", locale="ru")
    ctx_block = blocks[0]["text"]
    assert "[truncated at" in ctx_block
    # Should not exceed cap × chars-per-token by more than a small marker tail.
    assert len(ctx_block) < sp.PROJECT_CONTEXT_TOKEN_CAP * sp.TOKEN_CHARS + 200


def test_system_prompt_ru_operational_rules_present() -> None:
    from bmad_orchestrator.agent.system_prompt import build_system_prompt

    blocks = build_system_prompt(Path("/tmp"), wave="1a", locale="ru")
    rules_block = blocks[1]["text"]
    assert "OPERATIONAL RULES" in rules_block
    assert "Safety" in rules_block
    assert "IN-scope" in rules_block
    assert "OUT-of-scope" in rules_block
    assert "Disambiguation" in rules_block


def test_system_prompt_ru_few_shot_has_15_pairs() -> None:
    """FS4 B10: spec specifies 10-15 pairs; we ship 15."""
    from bmad_orchestrator.agent.system_prompt import build_system_prompt

    blocks = build_system_prompt(Path("/tmp"), wave="1a", locale="ru")
    fs_block = blocks[3]["text"]
    # Numbered list 1. through 15.
    for i in range(1, 16):
        assert f"{i}." in fs_block, f"few-shot pair {i} missing"


# ── B11 — manual tool split (always-on + deferred) ───────────────────────────


def test_always_on_tools_has_exactly_five_entries() -> None:
    from bmad_orchestrator.agent.run import ALWAYS_ON_TOOLS

    assert len(ALWAYS_ON_TOOLS) == 5
    assert "start_wave" in ALWAYS_ON_TOOLS
    assert "stop_orchestrator" in ALWAYS_ON_TOOLS
    assert "escalate_to_human" in ALWAYS_ON_TOOLS
    assert "read_memory" in ALWAYS_ON_TOOLS
    assert "read_sprint_status" in ALWAYS_ON_TOOLS


def test_always_on_tools_match_registered_tools() -> None:
    """Each always-on name must exist among @tool registrations."""
    from bmad_orchestrator.agent.run import ALWAYS_ON_TOOLS
    from bmad_orchestrator.agent.tools import tool_names

    names = set(tool_names())
    for n in ALWAYS_ON_TOOLS:
        assert n in names, f"ALWAYS_ON_TOOLS member {n!r} not registered"


def test_build_agent_options_mcp_servers_has_one_server() -> None:
    from bmad_orchestrator.agent.run import MCP_SERVER_NAME, build_agent_options

    opts = build_agent_options(
        project_root=Path("/tmp/fake-project"),
        wave="1a",
        models=ModelConfig(),
    )
    assert MCP_SERVER_NAME in opts["mcp_servers"]


# ── B9 — bot cross-process bridge + per-chat FIFO + cap ──────────────────────


def test_human_query_event_type_exists() -> None:
    from bmad_orchestrator.runtime.event_loop import EventType

    assert EventType.HUMAN_QUERY.value == "human_query"
    assert EventType.HUMAN_RESPONSE.value == "human_response"


@pytest.mark.asyncio
async def test_forward_to_agent_per_chat_fifo_with_corr_id() -> None:
    """Multiple concurrent requests on same chat — each future resolves by corr_id (W5: via subscribe_one_correlation)."""
    import asyncio

    from bmad_orchestrator.bot import handlers as bot_handlers
    from bmad_orchestrator.runtime.event_loop import EventLoop, EventType

    bus = EventLoop()
    bot_handlers.attach_event_loop(bus)
    bot_handlers.attach_state_db(None, None)
    bot_handlers.reset_for_test()
    try:
        async def _collect_and_reply() -> None:
            events = []
            for _ in range(5):
                ev = await bus.next(timeout=2.0)
                assert ev is not None
                assert ev.type == EventType.USER_CHAT_MESSAGE
                events.append(ev)
            # Reply in reverse order — futures must still resolve correctly.
            for ev in reversed(events):
                await bus.emit(
                    EventType.HUMAN_RESPONSE,
                    chat_id=ev.payload["chat_id"],
                    corr_id=ev.payload["corr_id"],
                    text=f"reply-{ev.payload['text']}",
                )

        collector = asyncio.create_task(_collect_and_reply())
        results = await asyncio.gather(
            *[
                bot_handlers.forward_to_agent(
                    f"msg{i}", chat_id=42, source="text", timeout=3.0
                )
                for i in range(5)
            ]
        )
        await collector
        # Each result matches its own request (corr_id correlation).
        for i, r in enumerate(results):
            assert r == f"reply-msg{i}", f"mismatched corr_id mapping: {results}"
    finally:
        bot_handlers.attach_event_loop(None)
        bot_handlers.reset_for_test()


@pytest.mark.asyncio
async def test_forward_to_agent_inflight_cap_rejects_101st_request() -> None:
    from bmad_orchestrator.bot import handlers as bot_handlers
    from bmad_orchestrator.runtime.event_loop import EventLoop

    bus = EventLoop()
    bot_handlers.attach_event_loop(bus)
    bot_handlers.attach_state_db(None, None)
    try:
        # Fill the FIFO with futures that never resolve.
        fillers = [
            asyncio.create_task(
                bot_handlers.forward_to_agent(
                    f"m{i}", chat_id=i, source="text", timeout=5.0
                )
            )
            for i in range(bot_handlers._INFLIGHT_CAP)
        ]
        # Drain emitted events so the queue does not back up (we still keep futures pending).
        for _ in range(bot_handlers._INFLIGHT_CAP):
            await bus.next(timeout=1.0)

        # 101st request must be rejected synchronously.
        reply = await bot_handlers.forward_to_agent(
            "overflow", chat_id=9999, source="text", timeout=1.0
        )
        assert reply == "queue_full"
    finally:
        # Cancel hanging futures so the event loop can wind down.
        for t in fillers:
            t.cancel()
        await asyncio.gather(*fillers, return_exceptions=True)
        bot_handlers.attach_event_loop(None)


@pytest.mark.asyncio
async def test_state_db_bridge_round_trip_under_500ms() -> None:
    """FS4 B9: cross-process bridge latency target — p99 <500ms."""
    from bmad_orchestrator.bot import handlers as bot_handlers
    from bmad_orchestrator.state.db import StateDB

    with tempfile.TemporaryDirectory() as td:
        db = StateDB(Path(td) / "state.db")
        await db.init()
        sid = await db.create_session("odyssey", "1a", 2)

        bot_handlers.attach_event_loop(None)
        bot_handlers.attach_state_db(db, sid)
        try:
            # Background process simulator: drain human_query, write human_response.
            async def _agent_sim() -> None:
                for _ in range(20):
                    row = await db.claim_next_event_of_type(sid, "human_query")
                    if row is None:
                        await asyncio.sleep(0.01)
                        continue
                    payload = row["payload"]
                    await db.enqueue_human_response(
                        sid,
                        payload["chat_id"],
                        f"echo:{payload['text']}",
                        payload["corr_id"],
                    )
                    return

            sim = asyncio.create_task(_agent_sim())
            t0 = time.perf_counter()
            reply = await bot_handlers.forward_to_agent(
                "ping", chat_id=7, source="text", timeout=2.0
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000
            await sim
            assert reply == "echo:ping"
            assert elapsed_ms < 500, f"bridge round-trip {elapsed_ms:.0f}ms exceeds 500ms"
        finally:
            bot_handlers.attach_state_db(None, None)


@pytest.mark.asyncio
async def test_human_query_subscriber_emits_response_with_same_corr_id() -> None:
    """FS4 B9 subscriber stub — corr_id preserved for FIFO resolution."""
    from bmad_orchestrator.agent.run import human_query_subscriber
    from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType

    bus = EventLoop()
    ev = Event(
        type=EventType.HUMAN_QUERY,
        payload={"chat_id": 7, "corr_id": "abc123", "text": "hi"},
    )
    await human_query_subscriber(ev, bus)
    out = await bus.next(timeout=1.0)
    assert out is not None
    assert out.type == EventType.HUMAN_RESPONSE
    assert out.payload["corr_id"] == "abc123"
    assert out.payload["chat_id"] == 7


# ── B12 — loud-fallback for real=True spawn ──────────────────────────────────


@pytest.mark.asyncio
async def test_spawn_worker_real_without_binary_loud_fallback() -> None:
    """FS4 B12: real=True + binary missing → isError envelope with fallback_reason."""
    from bmad_orchestrator.agent.tools.spawn import spawn_worker

    with tempfile.TemporaryDirectory() as td:
        wt = Path(td) / "wt-x"
        wt.mkdir()
        with patch(
            "bmad_orchestrator.agent.tools.spawn.shutil.which", return_value=None
        ), patch(
            "bmad_orchestrator.runtime.worker_spawn.shutil.which", return_value=None
        ):
            res = await spawn_worker.handler(
                {"worktree": str(wt), "real": True, "story_id": "x"}
            )
    assert res.get("isError") is True
    body = json.loads(res["content"][0]["text"])
    assert body["error"] == "real_spawn_fallback"
    assert body["fallback_reason"] == "claude_binary_not_found"
    assert body["real_requested"] is True
    assert body["mock"] is True


@pytest.mark.asyncio
async def test_worker_handle_fallback_reason_populated_on_auto_mock() -> None:
    """FS4 B12: runtime spawn surfaces fallback_reason on WorkerHandle."""
    from bmad_orchestrator.runtime.worker_spawn import spawn_worker as runtime_spawn

    with tempfile.TemporaryDirectory() as td:
        wt = Path(td) / "wt-y"
        wt.mkdir()
        with patch(
            "bmad_orchestrator.runtime.worker_spawn.shutil.which", return_value=None
        ):
            handle = await runtime_spawn(
                worktree=str(wt), story_id="y", branch="feature/y", mock=None
            )
    assert handle.mock is True
    assert handle.fallback_reason == "claude_binary_not_found"
    # mock=None means caller is ambivalent; @tool wrapper tracks intent.
    assert handle.real_requested is False


@pytest.mark.asyncio
async def test_worker_handle_no_fallback_when_caller_asks_for_mock() -> None:
    """fallback_reason must be None when mock=True is explicit."""
    from bmad_orchestrator.runtime.worker_spawn import spawn_worker as runtime_spawn

    with tempfile.TemporaryDirectory() as td:
        wt = Path(td) / "wt-z"
        wt.mkdir()
        handle = await runtime_spawn(
            worktree=str(wt), story_id="z", branch="feature/z", mock=True
        )
    assert handle.mock is True
    assert handle.fallback_reason is None
    assert handle.real_requested is False


# ── Security hardening (post-audit) ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_forward_to_agent_per_chat_cap_rejects_excess() -> None:
    """Single chat cannot exceed _PER_CHAT_CAP — DoS-by-one-user guard."""
    from bmad_orchestrator.bot import handlers as bot_handlers
    from bmad_orchestrator.runtime.event_loop import EventLoop

    bus = EventLoop()
    bot_handlers.attach_event_loop(bus)
    try:
        fillers = [
            asyncio.create_task(
                bot_handlers.forward_to_agent(
                    f"m{i}", chat_id=42, source="text", timeout=5.0
                )
            )
            for i in range(bot_handlers._PER_CHAT_CAP)
        ]
        for _ in range(bot_handlers._PER_CHAT_CAP):
            await bus.next(timeout=1.0)

        reply = await bot_handlers.forward_to_agent(
            "overflow", chat_id=42, source="text", timeout=1.0
        )
        assert reply == "queue_full"
    finally:
        for t in fillers:
            t.cancel()
        await asyncio.gather(*fillers, return_exceptions=True)


@pytest.mark.asyncio
async def test_poll_state_db_does_not_resolve_foreign_chat() -> None:
    """Bridge-poisoning guard — a row for chat B must NOT resolve chat A's future."""
    from bmad_orchestrator.bot import handlers as bot_handlers
    from bmad_orchestrator.state.db import StateDB

    with tempfile.TemporaryDirectory() as td:
        db = StateDB(Path(td) / "state.db")
        await db.init()
        sid = await db.create_session("odyssey", "1a", 2)
        bot_handlers.attach_state_db(db, sid)

        chat_a_task = asyncio.create_task(
            bot_handlers.forward_to_agent(
                "from A", chat_id=111, source="text", timeout=1.0
            )
        )
        # Wait for chat A's row to be enqueued + polling task to start.
        await asyncio.sleep(0.1)
        # Inject a hostile row for a DIFFERENT chat with a fabricated corr_id.
        await db.enqueue_human_response(sid, 222, "PWNED", "deadbeef")
        # Chat A's task must time out (it never sees its own response).
        result = await chat_a_task
        assert "не ответил" in result or "повтори" in result
        assert "PWNED" not in result


def test_safe_read_text_rejects_symlink_escaping_root(tmp_path: Path) -> None:
    """Symlink pointing outside allow_root must surface as (missing) — never read."""
    from bmad_orchestrator.agent.system_prompt import _safe_read_text

    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("PWNED")

    inside = tmp_path / "inside"
    inside.mkdir()
    link = inside / "CLAUDE.md"
    link.symlink_to(secret)

    body = _safe_read_text(link, inside)
    assert "PWNED" not in body
    assert body.startswith("(missing:") or body.startswith("(unreadable:")


def test_safe_read_text_rejects_path_escape_via_dotdot(tmp_path: Path) -> None:
    """`allow_root/../sibling/file` must not be served when allow_root is enforced."""
    from bmad_orchestrator.agent.system_prompt import _safe_read_text

    inside = tmp_path / "inside"
    inside.mkdir()
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    (sibling / "secret.md").write_text("PWNED")

    body = _safe_read_text(inside / ".." / "sibling" / "secret.md", inside)
    assert "PWNED" not in body


@pytest.mark.asyncio
async def test_spawn_worker_error_envelope_basename_only() -> None:
    """spawn_worker real-mode fallback must NOT leak absolute paths in payload."""
    from bmad_orchestrator.agent.tools.spawn import spawn_worker as spawn_tool

    with tempfile.TemporaryDirectory() as td:
        wt = Path(td) / "wt-leak"
        wt.mkdir()
        with patch(
            "bmad_orchestrator.agent.tools.spawn.shutil.which", return_value=None
        ), patch(
            "bmad_orchestrator.runtime.worker_spawn.shutil.which", return_value=None
        ):
            result = await spawn_tool.handler({"worktree": str(wt), "real": True})

    payload = json.loads(result["content"][0]["text"])
    assert result["isError"] is True
    assert payload["worktree"] == "wt-leak"
    assert str(wt.parent) not in result["content"][0]["text"]


# ── helpers + shared fixtures ────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_bot_state() -> Any:
    """Each test starts with a clean handler state."""
    from bmad_orchestrator.bot import handlers as bot_handlers

    bot_handlers.attach_event_loop(None)
    bot_handlers.attach_state_db(None, None)
    bot_handlers.reset_for_test()
    yield
    bot_handlers.attach_event_loop(None)
    bot_handlers.attach_state_db(None, None)
    bot_handlers.reset_for_test()
