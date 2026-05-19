"""NEW-20 — security_review verdict=error must not unconditionally block merge.

Before NEW-20 a persistent ``verdict=error`` from the security-review runner
(a *technical* failure) mutated the in-flight CODE_REVIEW_VERDICT payload to
``reject`` and escalated — unconditionally. NEW-15 already gave ``code_review``
a runner-log fallback; NEW-20 brings ``security_review`` to symmetry:

  * on persistent error, try the shared Stage-6 runner-log fallback
    (``parse_security_runner_fallback`` → ``parse_runner_review_log`` core);
  * a recovered holistic verdict (approve / merge_with_fixes / block) is
    applied — approve/merge_with_fixes → SECURITY_REVIEW_PASSED, merge proceeds;
  * only when NO signal exists anywhere does the story escalate to a human.

3 unit + 2 integration. See spec/spec_pilot_findings_closure_v7.md §2.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.runtime.security_review import security_review_subscriber
from bmad_orchestrator.runtime.verdict_fallback import parse_security_runner_fallback

# ── Helpers ──────────────────────────────────────────────────────────────────


def _drain(bus: EventLoop) -> list[Event]:
    out: list[Event] = []
    while True:
        try:
            out.append(bus.queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return out


def _code_review_verdict(worktree: Path, story_id: str = "7.1") -> Event:
    """An approve verdict for a story in a security-critical epic (epic 7)."""
    return Event(
        type=EventType.CODE_REVIEW_VERDICT,
        payload={
            "story_id": story_id,
            "worktree": str(worktree),
            "verdict": "approve",
        },
    )


def _write_runner_log(worktree: Path, story_id: str, token: str) -> None:
    """Drop a bmad-auto-dev Stage-6 review log carrying a verdict token."""
    reviews = worktree / "_bmad" / "auto-dev-state" / "reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    (reviews / f"{story_id}.log").write_text(
        f"Stage 6 review complete.\nreview verdict: {token}\n", encoding="utf-8"
    )


async def _error_runner(worktree: Path, story_id: str, wave: str) -> tuple[str, str]:
    """A security-review runner that always technically fails."""
    return "error", "runner produced no parseable verdict"


# ════════════════════════════════════════════════════════════════════════════
# Unit (3)
# ════════════════════════════════════════════════════════════════════════════


def test_security_runner_fallback_maps_code_verdicts(tmp_path: Path) -> None:
    """parse_security_runner_fallback reuses the shared Stage-6 log core and
    maps PASS/NEEDS-FIX/BLOCKED onto the security verdict vocabulary."""
    for token, expected in (
        ("PASS", "approve"),
        ("NEEDS-FIX", "merge_with_fixes"),
        ("BLOCKED", "block"),
    ):
        wt = tmp_path / token
        wt.mkdir()
        _write_runner_log(wt, "7.1", token)
        result = parse_security_runner_fallback(wt, "7.1")
        assert result is not None
        assert result[0] == expected
    # No log at all → None (caller escalates to a human).
    bare = tmp_path / "bare"
    bare.mkdir()
    assert parse_security_runner_fallback(bare, "7.1") is None


@pytest.mark.asyncio
async def test_error_with_fallback_approve_does_not_block(tmp_path: Path) -> None:
    """verdict=error + a PASS Stage-6 log → SECURITY_REVIEW_PASSED, the
    in-flight CODE_REVIEW_VERDICT payload is NOT mutated to reject."""
    _write_runner_log(tmp_path, "7.1", "PASS")
    event = _code_review_verdict(tmp_path)
    bus = EventLoop()

    await security_review_subscriber(event, bus, runner=_error_runner)

    emitted = _drain(bus)
    types = [e.type for e in emitted]
    assert EventType.SECURITY_REVIEW_PASSED in types
    assert EventType.HUMAN_QUERY not in types
    # merge not blocked — payload verdict untouched.
    assert event.payload["verdict"] == "approve"


@pytest.mark.asyncio
async def test_error_without_signal_escalates_single_story(tmp_path: Path) -> None:
    """verdict=error + NO Stage-6 log anywhere → exactly one HUMAN_QUERY
    (verdict=security_review_error); payload rejected so the merge pauses for
    the human, but this is now conditional on the fallback finding nothing."""
    event = _code_review_verdict(tmp_path)  # no runner log written
    bus = EventLoop()

    await security_review_subscriber(event, bus, runner=_error_runner)

    emitted = _drain(bus)
    human = [e for e in emitted if e.type == EventType.HUMAN_QUERY]
    assert len(human) == 1
    assert human[0].payload["verdict"] == "security_review_error"
    assert event.payload["verdict"] == "reject"


# ════════════════════════════════════════════════════════════════════════════
# Integration (2)
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_error_with_fallback_block_still_halts(tmp_path: Path) -> None:
    """A recovered fallback verdict of BLOCK still halts the merge — the
    fallback is not a rubber stamp, it carries the holistic verdict faithfully."""
    _write_runner_log(tmp_path, "7.1", "BLOCKED")
    event = _code_review_verdict(tmp_path)
    bus = EventLoop()

    await security_review_subscriber(event, bus, runner=_error_runner)

    emitted = _drain(bus)
    human = [e for e in emitted if e.type == EventType.HUMAN_QUERY]
    assert len(human) == 1
    assert event.payload["verdict"] == "reject"
    assert "security_review_block" in event.payload.get("gate_reasons", [])


@pytest.mark.asyncio
async def test_error_retries_then_fallback_only_once(tmp_path: Path) -> None:
    """The runner is retried (NEW-13) and the fallback runs once after retries
    exhaust — a single SECURITY_REVIEW_ERROR-per-attempt audit trail, then the
    fallback recovers approve → PASSED, no HUMAN_QUERY."""
    _write_runner_log(tmp_path, "7.1", "PASS")
    calls = {"n": 0}

    async def _counting_runner(wt: Path, sid: str, wave: str) -> tuple[str, str]:
        calls["n"] += 1
        return "error", "no verdict"

    event = _code_review_verdict(tmp_path)
    bus = EventLoop()
    await security_review_subscriber(event, bus, runner=_counting_runner)

    assert calls["n"] >= 2  # initial + at least one retry (error_retry_max≥1)
    emitted = _drain(bus)
    assert EventType.SECURITY_REVIEW_PASSED in [e.type for e in emitted]
    assert EventType.HUMAN_QUERY not in [e.type for e in emitted]
