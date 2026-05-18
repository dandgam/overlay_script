"""Phase 4 hardening #3 — Banned-phrase linter gate.

Spec: spec_phase4_hardening §1.3.

Coverage (8 tests):

* Unit: positive hit for each phrase category (hedging, exclamatory,
  blanket-pass).
* Unit: clean summary returns None.
* Unit: case-insensitive matching.
* Unit: multi-hit truncation to 3 phrases in the reason string.
* Unit: policy override via custom YAML — custom path adds + extends defaults.
* Unit: override-only YAML (no default intersection) extends the union.
* Integration: gate wired in ``code_review_subscriber`` flips approve → reject
  when summary contains a banned phrase.
* Integration: gate does NOT trip on a clean summary.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from bmad_orchestrator.agent.run import (
    _gate_banned_phrases,
    _load_banned_phrases,
    code_review_subscriber,
    configure_code_review_gate,
)
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.runtime.worker_spawn import WorkerHandle

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
    out: list[Event] = []
    while True:
        try:
            out.append(bus.queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    # Phase 4 hardening #5 adds MERGE_GATE_STAGE_COMPLETED observability events;
    # filter them so pre-split assertions remain valid.
    return [e for e in out if e.type != EventType.MERGE_GATE_STAGE_COMPLETED]


@pytest.fixture
def reset_gate_config() -> Iterator[None]:
    configure_code_review_gate(target_project=None, wave=None)
    yield
    configure_code_review_gate(target_project=None, wave=None)


# ────────────────────────────────────────────────────────────────────────────
# A. Unit — pure _gate_banned_phrases function (6 tests)
# ────────────────────────────────────────────────────────────────────────────


def test_banned_phrases_positive_hedging() -> None:
    """Hedging phrase 'should work' trips the gate."""
    reason = _gate_banned_phrases("The changes should work correctly now.")
    assert reason is not None
    assert "should work" in reason
    assert reason.startswith("banned_phrases_in_completion:")


def test_banned_phrases_positive_exclamatory() -> None:
    """Exclamatory phrase 'Done!' trips the gate."""
    reason = _gate_banned_phrases("Done! All tests pass.")
    assert reason is not None
    assert "done!" in reason


def test_banned_phrases_positive_blanket_pass() -> None:
    """Blanket-pass phrase 'looks good to me' trips the gate."""
    reason = _gate_banned_phrases("Looks good to me, ready to merge.")
    assert reason is not None
    assert "looks good to me" in reason


def test_banned_phrases_clean_summary_returns_none() -> None:
    """Summary with specific evidence does not trip the gate."""
    clean = (
        "All 42 tests pass (pytest -x output attached). "
        "Files modified: src/foo.py, tests/test_foo.py. "
        "No lint errors. Coverage 94%."
    )
    assert _gate_banned_phrases(clean) is None


def test_banned_phrases_case_insensitive() -> None:
    """Matching is case-insensitive: 'SHOULD WORK' trips same as 'should work'."""
    reason = _gate_banned_phrases("THIS SHOULD WORK for all cases.")
    assert reason is not None
    assert "should work" in reason


def test_banned_phrases_multi_hit_truncation() -> None:
    """Multiple hits are truncated to 3 in the reason string."""
    # Construct a summary that hits many phrases
    summary = "Great! Done! Perfect! All good, no issues, looks good to me."
    reason = _gate_banned_phrases(summary)
    assert reason is not None
    # Reason should list at most 3 phrases
    hit_part = reason.removeprefix("banned_phrases_in_completion:")
    hits = hit_part.split(",")
    assert len(hits) <= 3


# ────────────────────────────────────────────────────────────────────────────
# B. Unit — policy override via YAML (2 tests)
# ────────────────────────────────────────────────────────────────────────────


def test_banned_phrases_yaml_override_extends_defaults(tmp_path: Path) -> None:
    """Custom YAML adds project-specific phrases to the default set."""
    yaml_content = "phrases:\n  - 'trust me'\n  - 'just works'\n"
    override = tmp_path / "my-banned.yaml"
    override.write_text(yaml_content, encoding="utf-8")

    phrases = _load_banned_phrases(override_path=override)

    # Custom phrases present
    assert "trust me" in phrases
    assert "just works" in phrases
    # Default phrases still present (union, not replace)
    assert "should work" in phrases
    assert "done!" in phrases


def test_banned_phrases_yaml_override_gate_trips_on_custom_phrase(tmp_path: Path) -> None:
    """Gate trips on a phrase added via override YAML."""
    yaml_content = "phrases:\n  - 'trust me bro'\n"
    override = tmp_path / "custom.yaml"
    override.write_text(yaml_content, encoding="utf-8")

    # Without override the phrase is not in default set.
    # We only assert it DOES trip with the override.
    reason = _gate_banned_phrases("trust me bro it will work fine", override_path=override)
    assert reason is not None
    assert "trust me bro" in reason


# ────────────────────────────────────────────────────────────────────────────
# C. Integration — gate wired in code_review_subscriber (2 tests)
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_banned_phrases_gate_flips_approve_to_request_changes(
    reset_gate_config: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gate trips: verdict goes from approve → reject (request_changes) in subscriber."""
    from bmad_orchestrator.skills_repo import CodeReviewGates

    review_jsonl = tmp_path / "review.jsonl"
    # Worker summary contains a banned phrase alongside an approve verdict
    _write_jsonl(
        review_jsonl,
        [
            {
                "event_type": "claude_event",
                "verdict": "approve",
                "summary": "Done! Looks good to me, everything should work.",
            },
            {"event_type": "worker_completed", "story_id": "s1", "status": "success"},
        ],
    )

    async def fake_spawn(*, worktree: str, story_id: str, wave: str) -> WorkerHandle:
        return _make_handle(worktree, story_id, review_jsonl)

    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker", fake_spawn
    )
    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_merge_gate_quality_worker", fake_spawn
    )

    configure_code_review_gate(
        target_project=tmp_path,
        wave="1a",
        gates_override=CodeReviewGates(),
    )
    bus = EventLoop()
    event = Event(
        type=EventType.WORKER_COMPLETED,
        payload={"story_id": "s1", "worktree": str(tmp_path / "wt"), "status": "success"},
    )

    await code_review_subscriber(event, bus)
    await bus.stop()

    emitted = _collect_emitted(bus)
    verdict_events = [
        e for e in emitted if e.type == EventType.CODE_REVIEW_VERDICT
    ]
    assert verdict_events, "expected CODE_REVIEW_VERDICT event"
    verdict = verdict_events[-1].payload.get("verdict")
    assert verdict == "reject", f"expected 'reject', got {verdict!r}"
    summary = verdict_events[-1].payload.get("summary", "")
    assert "banned_phrases_in_completion" in summary


@pytest.mark.asyncio
async def test_banned_phrases_gate_clean_summary_keeps_approve(
    reset_gate_config: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gate does not trip on a clean summary — verdict stays approve."""
    from bmad_orchestrator.skills_repo import CodeReviewGates

    review_jsonl = tmp_path / "review_clean.jsonl"
    _write_jsonl(
        review_jsonl,
        [
            {
                "event_type": "claude_event",
                "verdict": "approve",
                "summary": (
                    "All 12 tests pass. Files changed: src/bar.py, tests/test_bar.py. "
                    "Coverage 91%. No lint errors. CI green."
                ),
            },
            {"event_type": "worker_completed", "story_id": "s2", "status": "success"},
        ],
    )

    async def fake_spawn(*, worktree: str, story_id: str, wave: str) -> WorkerHandle:
        return _make_handle(worktree, story_id, review_jsonl)

    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker", fake_spawn
    )
    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_merge_gate_quality_worker", fake_spawn
    )

    configure_code_review_gate(
        target_project=tmp_path,
        wave="1a",
        gates_override=CodeReviewGates(),
    )
    bus = EventLoop()
    event = Event(
        type=EventType.WORKER_COMPLETED,
        payload={"story_id": "s2", "worktree": str(tmp_path / "wt"), "status": "success"},
    )

    await code_review_subscriber(event, bus)
    await bus.stop()

    emitted = _collect_emitted(bus)
    verdict_events = [
        e for e in emitted if e.type == EventType.CODE_REVIEW_VERDICT
    ]
    assert verdict_events, "expected CODE_REVIEW_VERDICT event"
    verdict = verdict_events[-1].payload.get("verdict")
    assert verdict == "approve", f"expected 'approve', got {verdict!r}"
