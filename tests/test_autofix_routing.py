"""S3 — spec_pilot_findings_closure §1 #3 rycag 1+2: autofix model routing.

The bmad-auto-dev runner Stage 6.retry hard-coded ``claude --model sonnet`` for
auto-fix attempts. Sonnet's 300-LOC diff cap halts security-critical stories
and re-review attempts that genuinely need Opus. Routing rules:

  * Story tag ``security-critical`` → opus
  * Review iteration ≥ ``opus_min_iteration`` (default 2) → opus
  * ENV ``BMAD_AUTOFIX_MODEL`` highest precedence
  * Else → ``default_model`` (sonnet)

Coverage (10 tests, exceeds +9 target):

  Routing rules (4):
    1. security-critical tag → opus
    2. iteration ≥ 2 → opus
    3. plain story + iteration 1 → sonnet
    4. ENV override → forced model regardless of rules

  Auto-split subscriber (3):
    5. WORKER_HALT_FILE + loc_cap_exceeded → STORY_AUTO_SPLIT emitted
    6. WORKER_HALT_FILE + other reason → ignored
    7. STORY_AUTO_SPLIT carries triggered flag = BMAD_AUTO_SPLIT setting

  Policy loading (3):
    8. defaults preserved on empty yaml
    9. invalid yaml → PolicyInvalidError
    10. missing file → PolicyNotFoundError

  CLI integration (2):
    11. CLI prints sonnet/opus for story file + iteration
    12. CLI --print-cli-name maps via policy
"""

from __future__ import annotations

import subprocess
import sys
from functools import partial
from pathlib import Path
from typing import cast

import pytest

from bmad_orchestrator.runtime.autofix_routing import (
    AutofixRoutingPolicy,
    load_autofix_routing_policy,
    pick_autofix_model,
)
from bmad_orchestrator.runtime.decomposer_subscriber import (
    LOC_CAP_EXCEEDED_REASON,
    decomposer_subscriber,
)
from bmad_orchestrator.runtime.event_loop import (
    Event,
    EventCallback,
    EventLoop,
    EventType,
)
from bmad_orchestrator.skills_repo import PolicyInvalidError, PolicyNotFoundError

# ---------------------------------------------------------------- routing rules

def test_routing_security_critical_tag_picks_opus() -> None:
    story = {"id": "3.1", "tags": ["security-critical", "auth"]}
    assert pick_autofix_model(story, review_iteration=1, env={}) == "opus"


def test_routing_iteration_threshold_picks_opus() -> None:
    story = {"id": "1.1", "tags": []}
    assert pick_autofix_model(story, review_iteration=2, env={}) == "opus"
    assert pick_autofix_model(story, review_iteration=5, env={}) == "opus"


def test_routing_default_sonnet_on_iteration_1_plain_story() -> None:
    story = {"id": "1.2", "tags": ["routine"]}
    assert pick_autofix_model(story, review_iteration=1, env={}) == "sonnet"


def test_routing_env_override_wins_over_rules() -> None:
    story = {"id": "3.1", "tags": ["security-critical"]}
    # Force sonnet even though security-critical tag would pick opus.
    assert (
        pick_autofix_model(
            story, review_iteration=5, env={"BMAD_AUTOFIX_MODEL": "sonnet"}
        )
        == "sonnet"
    )
    # Force opus even though plain + iter 1 would pick sonnet.
    assert (
        pick_autofix_model({}, review_iteration=1, env={"BMAD_AUTOFIX_MODEL": "OPUS"})
        == "opus"
    )


# ---------------------------------------------------------- auto-split subscriber

@pytest.mark.asyncio
async def test_subscriber_emits_story_auto_split_on_loc_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BMAD_AUTO_SPLIT", raising=False)
    bus = EventLoop()
    bus.on(cast(EventCallback, partial(decomposer_subscriber, bus=bus)))

    await bus.emit(
        EventType.WORKER_HALT_FILE,
        story_id="5.3",
        halt_reason=LOC_CAP_EXCEEDED_REASON,
        worktree="/tmp/wt-5-3",
    )

    seen = await _drain(bus)
    auto_split = [e for e in seen if e.type == EventType.STORY_AUTO_SPLIT]
    assert len(auto_split) == 1
    payload = auto_split[0].payload
    assert payload["parent_story_id"] == "5.3"
    assert payload["halt_reason"] == LOC_CAP_EXCEEDED_REASON
    assert payload["worktree"] == "/tmp/wt-5-3"
    assert payload["source"] == "decomposer_subscriber"
    assert payload["triggered"] is False  # env not set


@pytest.mark.asyncio
async def test_subscriber_ignores_non_loc_cap_halts() -> None:
    bus = EventLoop()
    bus.on(cast(EventCallback, partial(decomposer_subscriber, bus=bus)))

    await bus.emit(
        EventType.WORKER_HALT_FILE,
        story_id="5.3",
        halt_reason="build_check",
    )

    seen = await _drain(bus)
    assert [e for e in seen if e.type == EventType.STORY_AUTO_SPLIT] == []


@pytest.mark.asyncio
async def test_subscriber_triggered_flag_reflects_auto_split_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BMAD_AUTO_SPLIT", "1")
    bus = EventLoop()
    bus.on(cast(EventCallback, partial(decomposer_subscriber, bus=bus)))

    await bus.emit(
        EventType.WORKER_HALT_FILE,
        story_id="5.4",
        halt_reason=LOC_CAP_EXCEEDED_REASON,
    )
    seen = await _drain(bus)
    auto_split = [e for e in seen if e.type == EventType.STORY_AUTO_SPLIT]
    assert len(auto_split) == 1
    assert auto_split[0].payload["triggered"] is True


# ----------------------------------------------------------- policy loading

def test_policy_defaults_preserved_on_empty_yaml(tmp_path: Path) -> None:
    yaml_path = tmp_path / "autofix-routing.yaml"
    yaml_path.write_text("", encoding="utf-8")
    policy = load_autofix_routing_policy(yaml_path)
    assert isinstance(policy, AutofixRoutingPolicy)
    assert policy.enabled is True
    assert policy.security_critical_tag == "security-critical"
    assert policy.opus_min_iteration == 2
    assert policy.default_model == "sonnet"


def test_policy_invalid_yaml_raises(tmp_path: Path) -> None:
    yaml_path = tmp_path / "bad.yaml"
    yaml_path.write_text("opus_min_iteration: 0\n", encoding="utf-8")  # ge=1 violation
    with pytest.raises(PolicyInvalidError):
        load_autofix_routing_policy(yaml_path)


def test_policy_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(PolicyNotFoundError):
        load_autofix_routing_policy(tmp_path / "missing.yaml")


# ------------------------------------------------------------------ CLI shim

def test_cli_prints_sonnet_for_plain_iter1(tmp_path: Path) -> None:
    story = tmp_path / "1.1.md"
    story.write_text("# Story 1.1: tiny\n\n- **epic:** 1\n", encoding="utf-8")
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "bmad_orchestrator.runtime.autofix_routing",
            "--story-file",
            str(story),
            "--iteration",
            "1",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "sonnet"


def test_cli_print_cli_name_maps_via_policy(tmp_path: Path) -> None:
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        "opus_model: claude-opus-4-7\nsonnet_model: claude-sonnet-4-6\n",
        encoding="utf-8",
    )
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "bmad_orchestrator.runtime.autofix_routing",
            "--iteration",
            "3",  # forces opus
            "--policy-file",
            str(policy),
            "--print-cli-name",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "claude-opus-4-7"


# ------------------------------------------------------------------ helpers

async def _drain(bus: EventLoop, max_iter: int = 10) -> list[Event]:
    seen: list[Event] = []
    for _ in range(max_iter):
        ev = await bus.dispatch_one(timeout=0.05)
        if ev is None:
            break
        seen.append(ev)
    return seen
