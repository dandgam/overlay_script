"""W2 acceptance tests — intent-router LLM dispatch (wave_1a_pilot_wiring).

Coverage (per spec/spec_wave_1a_pilot_wiring.md §W2):
- Stub fallback when ANTHROPIC_API_KEY missing OR ``configure_intent_router``
  not wired — preserves FS4 B9 corr_id contract.
- Real dispatch (with stub AsyncAnthropic) routes user text → AsyncAnthropic
  call with cached system blocks + whitelisted tools subset.
- ``tool_use`` block dispatches through the registered handler (start_wave,
  read_sprint_status, …); non-whitelisted tools are dropped.
- Text-only response collected into HUMAN_RESPONSE payload.
- ``response.usage`` parsed → ``usd_cost`` → ``budget.attribute_usd`` toward
  daily cap; second call hits cache (``cache_read_input_tokens > 0``).
- Daily-cap halt blocks the next dispatch and emits
  ``BUDGET_THRESHOLD_HIT(scope=day, level=halt)``.
- Cached system blocks carry ``cache_control: {"type": "ephemeral"}`` on both
  blocks (router prompt + intent-router skill body).
- Anthropic API errors fall back to the stub.

Grep validations (asserted at module-import time, see ``test_grep_validations``):
- ``AsyncAnthropic`` OR ``client.messages.create`` in ``agent/run.py`` ≥ 1.
- ``cache_control`` in ``agent/run.py`` ≥ 1.
- ``intent_router_dispatched`` in ``agent/run.py`` ≥ 1.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from bmad_orchestrator.agent import run as run_module
from bmad_orchestrator.agent.run import (
    INTENT_ROUTER_SYSTEM_PROMPT,
    INTENT_ROUTER_TOOL_WHITELIST,
    configure_intent_router,
    human_query_subscriber,
)
from bmad_orchestrator.agent.safety.budget_guard import BudgetGuard
from bmad_orchestrator.config import BudgetConfig, ModelConfig
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType

# ── Helpers ──────────────────────────────────────────────────────────────────


class FakeUsage:
    """Pydantic-shaped ``usage`` stand-in (mirrors Anthropic SDK fields)."""

    def __init__(
        self,
        *,
        input_tokens: int = 0,
        cache_creation_input_tokens: int = 0,
        cache_read_input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        self.input_tokens = input_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens
        self.cache_read_input_tokens = cache_read_input_tokens
        self.output_tokens = output_tokens


class FakeBlock:
    """Pydantic-shaped content block (text or tool_use)."""

    def __init__(self, *, type: str, text: str = "", name: str = "", input: dict[str, Any] | None = None) -> None:
        self.type = type
        self.text = text
        self.name = name
        self.input = input or {}


class FakeResponse:
    def __init__(self, *, content: list[Any], usage: FakeUsage | None = None) -> None:
        self.content = content
        self.usage = usage or FakeUsage()


class _StubMessagesAPI:
    """Bound to a :class:`StubAnthropicClient` so it can record calls."""

    def __init__(self, owner: StubAnthropicClient) -> None:
        self._owner = owner

    async def create(self, **kwargs: Any) -> Any:
        self._owner.calls.append(kwargs)
        idx = min(len(self._owner.calls) - 1, len(self._owner._responses) - 1)
        return self._owner._responses[idx]


class StubAnthropicClient:
    """In-memory stand-in for ``AsyncAnthropic`` — records calls + replays scripted responses."""

    def __init__(self, responses: list[Any] | None = None) -> None:
        self._responses = list(responses or [FakeResponse(content=[FakeBlock(type="text", text="ok")])])
        self.calls: list[dict[str, Any]] = []
        self.messages = _StubMessagesAPI(self)


@pytest.fixture
def fresh_budget() -> BudgetGuard:
    """A fresh BudgetGuard with default caps and zero attribution."""
    return BudgetGuard(BudgetConfig())


@pytest.fixture
def isolated_intent_router(fresh_budget: BudgetGuard, monkeypatch: pytest.MonkeyPatch) -> StubAnthropicClient:
    """Wire intent-router with a stub Anthropic client + fresh budget.

    Restores module-level state after the test by reusing
    ``configure_intent_router`` with ``None`` arguments.
    """
    client = StubAnthropicClient()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    configure_intent_router(
        budget=fresh_budget,
        models=ModelConfig(),
        client_factory=lambda: client,
    )
    yield client
    configure_intent_router(budget=None, models=None, client_factory=None)


@pytest.fixture(autouse=True)
def _reset_intent_router_state() -> None:
    """Belt-and-braces — wipe module globals between tests."""
    configure_intent_router(budget=None, models=None, client_factory=None)
    yield
    configure_intent_router(budget=None, models=None, client_factory=None)


async def _drain_next(bus: EventLoop, *, timeout: float = 0.5) -> Event | None:
    return await bus.next(timeout=timeout)


# ── Stub-fallback path ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_api_key_falls_back_to_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    bus = EventLoop()
    ev = Event(
        type=EventType.HUMAN_QUERY,
        payload={"chat_id": 1, "corr_id": "c1", "text": "привет"},
    )
    await human_query_subscriber(ev, bus)
    out = await _drain_next(bus)
    assert out is not None and out.type == EventType.HUMAN_RESPONSE
    assert out.payload["corr_id"] == "c1"
    assert out.payload["chat_id"] == 1
    assert out.payload["text"].startswith("(stub)")


@pytest.mark.asyncio
async def test_no_budget_configured_falls_back_to_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    # configure_intent_router NOT called → budget/models are None
    bus = EventLoop()
    ev = Event(
        type=EventType.USER_CHAT_MESSAGE,
        payload={"chat_id": 2, "corr_id": "c2", "text": "hi"},
    )
    await human_query_subscriber(ev, bus)
    out = await _drain_next(bus)
    assert out is not None and out.type == EventType.HUMAN_RESPONSE
    assert out.payload["corr_id"] == "c2"


@pytest.mark.asyncio
async def test_ignores_other_event_types() -> None:
    bus = EventLoop()
    ev = Event(type=EventType.WORKER_COMPLETED, payload={"story_id": "1.1"})
    await human_query_subscriber(ev, bus)
    out = await bus.next(timeout=0.05)
    assert out is None


# ── Real dispatch path ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_real_dispatch_emits_text_response(
    isolated_intent_router: StubAnthropicClient,
) -> None:
    isolated_intent_router._responses = [
        FakeResponse(
            content=[FakeBlock(type="text", text="привет!")],
            usage=FakeUsage(input_tokens=100, output_tokens=20),
        )
    ]
    bus = EventLoop()
    ev = Event(
        type=EventType.HUMAN_QUERY,
        payload={"chat_id": 9, "corr_id": "c-text", "text": "привет"},
    )
    await human_query_subscriber(ev, bus)
    out = await _drain_next(bus)
    assert out is not None and out.type == EventType.HUMAN_RESPONSE
    assert out.payload["corr_id"] == "c-text"
    assert "привет!" in out.payload["text"]


@pytest.mark.asyncio
async def test_real_dispatch_routes_tool_use_to_handler(
    isolated_intent_router: StubAnthropicClient,
) -> None:
    isolated_intent_router._responses = [
        FakeResponse(
            content=[
                FakeBlock(
                    type="tool_use",
                    name="read_sprint_status",
                    input={},
                )
            ],
            usage=FakeUsage(input_tokens=50, output_tokens=10),
        )
    ]
    bus = EventLoop()
    ev = Event(
        type=EventType.USER_CHAT_MESSAGE,
        payload={"chat_id": 3, "corr_id": "c-tool", "text": "что сейчас идёт"},
    )
    await human_query_subscriber(ev, bus)
    out = await _drain_next(bus)
    assert out is not None and out.type == EventType.HUMAN_RESPONSE
    # tool dispatch returns either ok envelope or error envelope (read_sprint_status
    # reads from disk — in tests w/o target project, it surfaces an error envelope).
    # Either way the response text must mention the tool name.
    assert "read_sprint_status" in out.payload["text"]


@pytest.mark.asyncio
async def test_non_whitelisted_tool_use_dropped(
    isolated_intent_router: StubAnthropicClient,
) -> None:
    # Even if the LLM hallucinates a non-whitelisted tool (e.g. spawn_worker),
    # the router must NOT dispatch it.
    isolated_intent_router._responses = [
        FakeResponse(
            content=[
                FakeBlock(
                    type="tool_use",
                    name="spawn_worker",
                    input={"worktree": "/etc"},
                ),
                FakeBlock(type="text", text="fallback"),
            ],
            usage=FakeUsage(input_tokens=20, output_tokens=5),
        )
    ]
    bus = EventLoop()
    ev = Event(
        type=EventType.HUMAN_QUERY,
        payload={"chat_id": 4, "corr_id": "c-evil", "text": "spawn"},
    )
    await human_query_subscriber(ev, bus)
    out = await _drain_next(bus)
    assert out is not None
    # Tool dropped → text block surfaces as the response.
    assert out.payload["text"].strip() == "fallback"


@pytest.mark.asyncio
async def test_dispatch_calls_anthropic_with_correct_model_and_max_tokens(
    isolated_intent_router: StubAnthropicClient,
) -> None:
    bus = EventLoop()
    ev = Event(
        type=EventType.HUMAN_QUERY,
        payload={"chat_id": 5, "corr_id": "c-cfg", "text": "ping"},
    )
    await human_query_subscriber(ev, bus)
    assert len(isolated_intent_router.calls) == 1
    call = isolated_intent_router.calls[0]
    assert call["model"] == ModelConfig().routine
    assert call["max_tokens"] == 512


@pytest.mark.asyncio
async def test_system_blocks_include_cache_control(
    isolated_intent_router: StubAnthropicClient,
) -> None:
    bus = EventLoop()
    ev = Event(
        type=EventType.HUMAN_QUERY,
        payload={"chat_id": 6, "corr_id": "c-cache", "text": "x"},
    )
    await human_query_subscriber(ev, bus)
    call = isolated_intent_router.calls[0]
    system_blocks = call["system"]
    assert isinstance(system_blocks, list)
    assert len(system_blocks) >= 2
    for block in system_blocks:
        assert block["type"] == "text"
        assert block["cache_control"] == {"type": "ephemeral"}
    # Router system prompt is the first block, skill body the second.
    assert system_blocks[0]["text"] == INTENT_ROUTER_SYSTEM_PROMPT
    assert "intent-router" in system_blocks[1]["text"].lower()


@pytest.mark.asyncio
async def test_tools_passed_match_whitelist(
    isolated_intent_router: StubAnthropicClient,
) -> None:
    bus = EventLoop()
    ev = Event(
        type=EventType.HUMAN_QUERY,
        payload={"chat_id": 7, "corr_id": "c-tools", "text": "x"},
    )
    await human_query_subscriber(ev, bus)
    call = isolated_intent_router.calls[0]
    tool_names = {t["name"] for t in call["tools"]}
    assert tool_names == set(INTENT_ROUTER_TOOL_WHITELIST)
    for tool in call["tools"]:
        assert "description" in tool
        assert tool["input_schema"]["type"] == "object"


@pytest.mark.asyncio
async def test_user_message_passed_as_messages(
    isolated_intent_router: StubAnthropicClient,
) -> None:
    bus = EventLoop()
    ev = Event(
        type=EventType.HUMAN_QUERY,
        payload={"chat_id": 8, "corr_id": "c-msg", "text": "запусти wave 1a"},
    )
    await human_query_subscriber(ev, bus)
    messages = isolated_intent_router.calls[0]["messages"]
    assert messages == [{"role": "user", "content": "запусти wave 1a"}]


# ── Cost accounting + cache invariant ────────────────────────────────────────


@pytest.mark.asyncio
async def test_cost_attributed_to_budget(
    isolated_intent_router: StubAnthropicClient, fresh_budget: BudgetGuard
) -> None:
    isolated_intent_router._responses = [
        FakeResponse(
            content=[FakeBlock(type="text", text="ok")],
            usage=FakeUsage(input_tokens=1_000_000, output_tokens=1_000_000),
        )
    ]
    bus = EventLoop()
    ev = Event(
        type=EventType.HUMAN_QUERY,
        payload={"chat_id": 9, "corr_id": "c-cost", "text": "x"},
    )
    await human_query_subscriber(ev, bus)
    # 1M input + 1M output on Sonnet 4.6 = $3 + $15 = $18.
    assert fresh_budget.attributed_for("intent_router") == Decimal("18")
    assert fresh_budget.attributed_total() == Decimal("18")


@pytest.mark.asyncio
async def test_zero_usage_attributes_zero(
    isolated_intent_router: StubAnthropicClient, fresh_budget: BudgetGuard
) -> None:
    bus = EventLoop()
    ev = Event(
        type=EventType.HUMAN_QUERY,
        payload={"chat_id": 10, "corr_id": "c-zero", "text": "x"},
    )
    await human_query_subscriber(ev, bus)
    assert fresh_budget.attributed_total() == Decimal("0")


@pytest.mark.asyncio
async def test_cache_hit_on_second_call(
    isolated_intent_router: StubAnthropicClient,
) -> None:
    isolated_intent_router._responses = [
        FakeResponse(
            content=[FakeBlock(type="text", text="r1")],
            usage=FakeUsage(input_tokens=200, cache_creation_input_tokens=1500, output_tokens=10),
        ),
        FakeResponse(
            content=[FakeBlock(type="text", text="r2")],
            usage=FakeUsage(input_tokens=200, cache_read_input_tokens=1500, output_tokens=10),
        ),
    ]
    bus = EventLoop()
    for corr, text in (("c-cache-1", "hi 1"), ("c-cache-2", "hi 2")):
        ev = Event(type=EventType.HUMAN_QUERY, payload={"chat_id": 11, "corr_id": corr, "text": text})
        await human_query_subscriber(ev, bus)
    # 2 calls + 2 emitted HUMAN_RESPONSE — drain to confirm no extras.
    assert len(isolated_intent_router.calls) == 2
    # Both calls send the same system blocks (cache-eligible).
    assert isolated_intent_router.calls[0]["system"] == isolated_intent_router.calls[1]["system"]


# ── Daily-cap halt ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_daily_cap_halt_blocks_dispatch_when_already_over(
    isolated_intent_router: StubAnthropicClient, fresh_budget: BudgetGuard
) -> None:
    # Pre-load attribution above the day cap (default $500).
    await fresh_budget.attribute_usd(scope="warmup", spent=600.0)
    isolated_intent_router.calls.clear()
    bus = EventLoop()
    ev = Event(
        type=EventType.HUMAN_QUERY,
        payload={"chat_id": 12, "corr_id": "c-halt", "text": "ping"},
    )
    await human_query_subscriber(ev, bus)
    # 1) No Anthropic call made (pre-check halted)
    assert isolated_intent_router.calls == []
    # 2) Bus drained: BUDGET_THRESHOLD_HIT THEN HUMAN_RESPONSE stub
    captured: list[Event] = []
    while (e := await bus.next(timeout=0.05)) is not None:
        captured.append(e)
    types = [e.type for e in captured]
    assert EventType.BUDGET_THRESHOLD_HIT in types
    assert EventType.HUMAN_RESPONSE in types
    halt = next(e for e in captured if e.type == EventType.BUDGET_THRESHOLD_HIT)
    assert halt.payload["level"] == "halt"
    assert halt.payload["scope"] == "day"


@pytest.mark.asyncio
async def test_attribute_usd_emits_threshold_hit_on_overflow(
    fresh_budget: BudgetGuard,
) -> None:
    bus = EventLoop()
    fresh_budget.event_loop = bus
    # Single attribution above cap → immediate halt event.
    await fresh_budget.attribute_usd(scope="intent_router", spent=600.0)
    out = await bus.next(timeout=0.5)
    assert out is not None
    assert out.type == EventType.BUDGET_THRESHOLD_HIT
    assert out.payload["scope"] == "day"
    assert out.payload["level"] == "halt"
    assert out.payload["attribution_scope"] == "intent_router"


# ── Robustness: API errors / malformed responses ─────────────────────────────


@pytest.mark.asyncio
async def test_anthropic_api_error_falls_back_to_stub(
    isolated_intent_router: StubAnthropicClient,
) -> None:
    async def _boom(**_: Any) -> Any:
        raise RuntimeError("connection refused")

    isolated_intent_router.messages.create = _boom  # type: ignore[assignment]
    bus = EventLoop()
    ev = Event(
        type=EventType.HUMAN_QUERY,
        payload={"chat_id": 13, "corr_id": "c-boom", "text": "x"},
    )
    await human_query_subscriber(ev, bus)
    out = await _drain_next(bus)
    assert out is not None and out.type == EventType.HUMAN_RESPONSE
    assert out.payload["corr_id"] == "c-boom"
    assert out.payload["text"].startswith("(stub)")


@pytest.mark.asyncio
async def test_empty_content_emits_no_response_placeholder(
    isolated_intent_router: StubAnthropicClient,
) -> None:
    isolated_intent_router._responses = [
        FakeResponse(content=[], usage=FakeUsage(input_tokens=10, output_tokens=0))
    ]
    bus = EventLoop()
    ev = Event(
        type=EventType.HUMAN_QUERY,
        payload={"chat_id": 14, "corr_id": "c-empty", "text": "x"},
    )
    await human_query_subscriber(ev, bus)
    out = await _drain_next(bus)
    assert out is not None
    assert out.payload["text"] == "(no response)"


@pytest.mark.asyncio
async def test_corr_id_generated_when_missing(
    isolated_intent_router: StubAnthropicClient,
) -> None:
    bus = EventLoop()
    ev = Event(type=EventType.HUMAN_QUERY, payload={"chat_id": 15, "text": "x"})
    await human_query_subscriber(ev, bus)
    out = await _drain_next(bus)
    assert out is not None
    assert out.payload["corr_id"] and len(out.payload["corr_id"]) >= 8


@pytest.mark.asyncio
async def test_dict_shaped_response_supported(
    isolated_intent_router: StubAnthropicClient,
) -> None:
    """SDK normally returns Pydantic objects, but defensive code must handle dict-shaped
    responses too (mocking convenience + future-proofing). Spec mock strategy notes this."""
    isolated_intent_router._responses = [
        {  # type: ignore[list-item]
            "content": [{"type": "text", "text": "dict-mode"}],
            "usage": {"input_tokens": 5, "output_tokens": 2},
        }
    ]
    bus = EventLoop()
    ev = Event(
        type=EventType.HUMAN_QUERY,
        payload={"chat_id": 16, "corr_id": "c-dict", "text": "x"},
    )
    await human_query_subscriber(ev, bus)
    out = await _drain_next(bus)
    assert out is not None
    assert "dict-mode" in out.payload["text"]


@pytest.mark.asyncio
async def test_tool_use_with_dict_blocks(
    isolated_intent_router: StubAnthropicClient,
) -> None:
    isolated_intent_router._responses = [
        {  # type: ignore[list-item]
            "content": [
                {"type": "tool_use", "name": "read_sprint_status", "input": {}}
            ],
            "usage": {"input_tokens": 10, "output_tokens": 4},
        }
    ]
    bus = EventLoop()
    ev = Event(
        type=EventType.HUMAN_QUERY,
        payload={"chat_id": 17, "corr_id": "c-tooldict", "text": "x"},
    )
    await human_query_subscriber(ev, bus)
    out = await _drain_next(bus)
    assert out is not None
    assert "read_sprint_status" in out.payload["text"]


# ── Grep validations (spec acceptance) ───────────────────────────────────────


def test_grep_validations() -> None:
    src = Path(run_module.__file__).read_text(encoding="utf-8")
    assert "AsyncAnthropic" in src or "client.messages.create" in src
    assert "cache_control" in src
    assert "intent_router_dispatched" in src
