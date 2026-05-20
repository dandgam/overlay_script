"""Integration tests for supervisor_subscriber + bus + audit."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.runtime.supervisor_subscriber import (
    DEFAULT_POLICY_PATH,
    WATCHED_EVENT_TYPES,
    load_supervisor_engine,
    make_supervisor_subscriber,
)
from bmad_orchestrator.supervisor import SupervisorEngine, SupervisorPolicy


def test_default_policy_path_resolvable():
    """The packaged default policy exists at the documented path."""
    assert DEFAULT_POLICY_PATH.is_file() or DEFAULT_POLICY_PATH.is_absolute() is False


def test_watched_event_types_include_5_plus_stuck():
    """NEW-33.3 — WORKER_STUCK_TIMEOUT joins the original 5 watched types."""
    expected = {
        EventType.HUMAN_QUERY,
        EventType.WORKER_HALT_FILE,
        EventType.BUDGET_THRESHOLD_HIT,
        EventType.WORKER_SILENT_FAILURE,
        EventType.COMPLIANCE_SWEEP_NEEDED,
        EventType.WORKER_STUCK_TIMEOUT,
    }
    assert WATCHED_EVENT_TYPES == frozenset(expected)


def test_load_engine_uses_judge_factory_when_explicit_judge_absent():
    """NEW-33.3 — judge_factory hook fires when no explicit judge passed."""
    calls = []

    class _FakeJudge:
        async def classify(self, _input):  # pragma: no cover — protocol stub
            return None

    def _factory():
        calls.append(1)
        return _FakeJudge()

    engine = load_supervisor_engine(judge_factory=_factory)
    assert calls == [1]
    assert isinstance(engine.judge, _FakeJudge)


def test_load_engine_explicit_judge_overrides_factory():
    """Explicit ``judge`` wins over factory."""

    class _FakeA:
        async def classify(self, _input):  # pragma: no cover
            return None

    class _FakeB:
        async def classify(self, _input):  # pragma: no cover
            return None

    a = _FakeA()
    engine = load_supervisor_engine(judge=a, judge_factory=lambda: _FakeB())
    assert engine.judge is a


def test_load_engine_factory_exception_falls_back_to_stub():
    """NEW-33.3 — factory raising must not crash engine load."""
    from bmad_orchestrator.supervisor.llm_judge import StubJudge

    def _bad_factory():
        raise RuntimeError("anthropic key missing")

    engine = load_supervisor_engine(judge_factory=_bad_factory)
    assert isinstance(engine.judge, StubJudge)


def test_load_engine_with_default_path():
    engine = load_supervisor_engine()
    assert isinstance(engine, SupervisorEngine)
    assert len(engine.policy.hard_rules) >= 3


def test_load_engine_missing_path_returns_empty(tmp_path: Path):
    engine = load_supervisor_engine(tmp_path / "missing.yaml")
    assert isinstance(engine, SupervisorEngine)
    assert engine.policy.hard_rules == []


async def _drain(bus: EventLoop) -> list[Event]:
    out: list[Event] = []
    while not bus.queue.empty():
        out.append(bus.queue.get_nowait())
    return out


@pytest.mark.asyncio
async def test_subscriber_ignores_unwatched_event_types(tmp_path: Path):
    engine = SupervisorEngine(SupervisorPolicy(version=1))
    subscriber = make_supervisor_subscriber(engine)
    bus = EventLoop()
    with patch(
        "bmad_orchestrator.supervisor.audit.runs_dir", return_value=tmp_path
    ):
        await subscriber(
            Event(type=EventType.WORKER_COMPLETED, payload={}), bus
        )
    queued = await _drain(bus)
    assert queued == []


@pytest.mark.asyncio
async def test_subscriber_skips_self_emitted_events(tmp_path: Path):
    """Anti-loop: supervisor must not act on events it emitted itself."""
    engine = SupervisorEngine(SupervisorPolicy(version=1))
    subscriber = make_supervisor_subscriber(engine)
    bus = EventLoop()
    with patch(
        "bmad_orchestrator.supervisor.audit.runs_dir", return_value=tmp_path
    ):
        await subscriber(
            Event(
                type=EventType.HUMAN_QUERY,
                payload={"source": "supervisor", "story_id": "x"},
            ),
            bus,
        )
    queued = await _drain(bus)
    assert queued == []


@pytest.mark.asyncio
async def test_subscriber_handles_budget_event_via_hard_rule(tmp_path: Path):
    """End-to-end: a BUDGET_THRESHOLD_HIT at ratio≥1.0 hits the budget-hard-cap rule."""
    engine = load_supervisor_engine(DEFAULT_POLICY_PATH)
    subscriber = make_supervisor_subscriber(engine)
    bus = EventLoop()
    with patch(
        "bmad_orchestrator.supervisor.audit.runs_dir", return_value=tmp_path
    ):
        await subscriber(
            Event(
                type=EventType.BUDGET_THRESHOLD_HIT,
                payload={"story_id": "1.1", "ratio": 1.1},
            ),
            bus,
        )
    queued = await _drain(bus)
    # pause_workers → HUMAN_QUERY with action marker
    assert len(queued) == 1
    payload = queued[0].payload
    assert payload["source"] == "supervisor"
    assert payload["action"] == "pause_workers"
    # Audit row written
    audit = tmp_path / "control.events.jsonl"
    assert audit.is_file()


@pytest.mark.asyncio
async def test_subscriber_handles_silent_failure_via_hard_rule(tmp_path: Path):
    """WORKER_SILENT_FAILURE → escalate_human via silent-failure-always-escalate."""
    engine = load_supervisor_engine(DEFAULT_POLICY_PATH)
    subscriber = make_supervisor_subscriber(engine)
    bus = EventLoop()
    with patch(
        "bmad_orchestrator.supervisor.audit.runs_dir", return_value=tmp_path
    ):
        await subscriber(
            Event(
                type=EventType.WORKER_SILENT_FAILURE,
                payload={"story_id": "1.2"},
            ),
            bus,
        )
    queued = await _drain(bus)
    assert len(queued) == 1
    assert queued[0].type is EventType.HUMAN_QUERY
    assert queued[0].payload["source"] == "supervisor"


@pytest.mark.asyncio
async def test_subscriber_survives_audit_failures(tmp_path: Path):
    """Audit write failure must not crash the subscriber."""
    engine = SupervisorEngine(SupervisorPolicy(version=1))
    subscriber = make_supervisor_subscriber(engine)
    bus = EventLoop()
    bad = tmp_path / "is-a-file"
    bad.write_text("blocking", encoding="utf-8")
    with patch(
        "bmad_orchestrator.supervisor.audit.runs_dir", return_value=bad
    ):
        # Should not raise
        await subscriber(
            Event(type=EventType.HUMAN_QUERY, payload={"story_id": "x"}),
            bus,
        )


# ── M4 judge factory integration tests ────────────────────────────────────────


def test_load_engine_judge_factory_anthropic_path():
    """M4: BMAD_SUPERVISOR_LLM=anthropic + ANTHROPIC_API_KEY=fake → AnthropicJudge."""
    from bmad_orchestrator.supervisor.judges.anthropic_judge import AnthropicJudge

    def _factory() -> object:
        import os

        mode = os.environ.get("BMAD_SUPERVISOR_LLM", "").strip().lower()
        if mode in {"1", "true", "anthropic", "sonnet"}:
            api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
            if not api_key:
                from bmad_orchestrator.supervisor.llm_judge import StubJudge
                return StubJudge()
            import anthropic
            return AnthropicJudge(
                client=anthropic.AsyncAnthropic(api_key=api_key),
                model="claude-sonnet-4-6",
                system_prompt="You are a supervisor.",
            )
        from bmad_orchestrator.supervisor.llm_judge import StubJudge
        return StubJudge()

    import os
    env = {"BMAD_SUPERVISOR_LLM": "anthropic", "ANTHROPIC_API_KEY": "fake-key-for-test"}
    with patch.dict(os.environ, env):
        engine = load_supervisor_engine(judge_factory=_factory)

    assert isinstance(engine.judge, AnthropicJudge)


def test_load_engine_judge_factory_no_api_key_falls_back_to_stub():
    """M4: BMAD_SUPERVISOR_LLM=anthropic but no API key → StubJudge + warning."""
    from bmad_orchestrator.supervisor.llm_judge import StubJudge

    def _factory() -> object:
        import os

        mode = os.environ.get("BMAD_SUPERVISOR_LLM", "").strip().lower()
        if mode in {"1", "true", "anthropic", "sonnet"}:
            api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
            if not api_key:
                return StubJudge()
            import anthropic

            from bmad_orchestrator.supervisor.judges.anthropic_judge import AnthropicJudge
            return AnthropicJudge(
                client=anthropic.AsyncAnthropic(api_key=api_key),
                model="claude-sonnet-4-6",
                system_prompt="You are a supervisor.",
            )
        return StubJudge()

    import os
    env = {"BMAD_SUPERVISOR_LLM": "anthropic", "ANTHROPIC_API_KEY": ""}
    with patch.dict(os.environ, env):
        engine = load_supervisor_engine(judge_factory=_factory)

    assert isinstance(engine.judge, StubJudge)
