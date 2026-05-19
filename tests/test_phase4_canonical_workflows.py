"""BMad Phase 4 canonical workflows — coverage for gaps closed 2026-05-19.

Validates the 3 workflows wired into Virgil to match docs.bmad-method.org:
- ``bmad-sprint-planning`` — auto-init sprint-status.yaml on wave bootstrap
- ``bmad-correct-course``   — mid-sprint scope-change handler tool
- ``bmad-investigate``      — forensic deep-dive tool + heuristic gate

Scope:
* Embedded skill registration (covered also in test_e2_skills_repo.py).
* ``ensure_sprint_status_initialized`` happy path + missing-epics-md path +
  auto_init_sprint_status=False path.
* DagPlanner.from_target() calls ensure_sprint_status_initialized.
* ``should_investigate`` heuristic boundaries.
* Tool mock spawn produces seed artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bmad_orchestrator.agent.tools.correct_course import spawn_correct_course_worktree
from bmad_orchestrator.agent.tools.investigate import (
    RETRY_THRESHOLD,
    should_investigate,
    spawn_investigate_worktree,
)
from bmad_orchestrator.agent.tools.sprint_planning import (
    SprintStatusMissingError,
    _parse_epics_md,
    ensure_sprint_status_initialized,
    spawn_sprint_planning_worktree,
)
from bmad_orchestrator.skills_repo import EMBEDDED_SKILL_NAMES

SAMPLE_EPICS_MD = """\
# Wave 1 — Foundations

## Epic 1: Tenant onboarding

### Story 1.1: Signup form
Some body.

### Story 1.2: Onboarding email
More body.

## Epic 2: Billing

### Story 2.1: Stripe webhook
Body.

### Story 2-2: Invoice generation
Body.
"""


# ── 1. Embedded registration ────────────────────────────────────────────────


def test_sprint_planning_and_investigate_are_embedded() -> None:
    assert "bmad-sprint-planning" in EMBEDDED_SKILL_NAMES
    assert "bmad-investigate" in EMBEDDED_SKILL_NAMES
    assert "bmad-correct-course" in EMBEDDED_SKILL_NAMES  # was already, sanity


# ── 2. _parse_epics_md ──────────────────────────────────────────────────────


def test_parse_epics_md_extracts_epic_and_story_headings() -> None:
    data = _parse_epics_md(SAMPLE_EPICS_MD)
    epics = data["epics"]
    assert set(epics.keys()) == {"1", "2"}
    assert epics["1"]["title"] == "Tenant onboarding"
    assert set(epics["1"]["stories"].keys()) == {"1.1", "1.2"}
    # both `Story 2.1` and `Story 2-2` dialects are accepted
    assert set(epics["2"]["stories"].keys()) == {"2.1", "2.2"}
    assert epics["1"]["stories"]["1.1"]["status"] == "backlog"


def test_parse_epics_md_empty_input_returns_no_epics() -> None:
    data = _parse_epics_md("Just plain prose, no epic headers.\n")
    assert data["epics"] == {}


# ── 3. ensure_sprint_status_initialized ─────────────────────────────────────


def _seed_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, epics_md: str | None) -> Path:
    """Create a minimal target project layout. Returns target root."""
    target = tmp_path / "target"
    artifacts = target / "_bmad-output" / "planning-artifacts"
    artifacts.mkdir(parents=True)
    if epics_md is not None:
        (artifacts / "epics.md").write_text(epics_md, encoding="utf-8")
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target))
    # Force fresh Settings load picking up env var.
    import bmad_orchestrator.agent.tools._common as common
    common._settings_singleton = None  # type: ignore[attr-defined]
    return target


def test_ensure_sprint_status_initialized_creates_seed_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _seed_target(tmp_path, monkeypatch, epics_md=SAMPLE_EPICS_MD)

    result = ensure_sprint_status_initialized()
    assert result["action"] == "created_mock"
    assert result["epic_count"] == 2

    yaml_path = Path(result["path"])
    assert yaml_path.exists()
    assert yaml_path.name == "sprint-status.yaml"
    assert target in yaml_path.parents

    # Second call is a no-op.
    second = ensure_sprint_status_initialized()
    assert second["action"] == "exists"


def test_ensure_sprint_status_initialized_soft_skip_when_no_epics_md(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without canonical epics.md, auto-init logs a skip event and returns
    a soft marker — downstream code raises clearer per-call errors (story
    filter, wave loop). Loud raise reserved for ``auto_init`` disabled."""
    _seed_target(tmp_path, monkeypatch, epics_md=None)
    result = ensure_sprint_status_initialized()
    assert result["action"] == "skipped_no_epics_md"


def test_ensure_sprint_status_initialized_raises_when_auto_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_target(tmp_path, monkeypatch, epics_md=SAMPLE_EPICS_MD)
    from bmad_orchestrator.agent.tools import _common

    settings = _common.get_settings()
    settings.auto_init_sprint_status = False
    with pytest.raises(SprintStatusMissingError, match="Run `bmad-orchestrator sprint-planning`"):
        ensure_sprint_status_initialized(settings)


# ── 4. DagPlanner.from_target wires the guard ───────────────────────────────


def test_dag_planner_from_target_triggers_sprint_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When sprint-status.yaml is missing, ``DagPlanner.from_target()`` must
    auto-init via the embedded sprint-planning helper rather than silently
    returning an empty wave."""
    _seed_target(tmp_path, monkeypatch, epics_md=SAMPLE_EPICS_MD)

    from bmad_orchestrator.agent.tools._common import sprint_status_path

    yaml_path = sprint_status_path()
    assert not yaml_path.exists()  # precondition

    from bmad_orchestrator.runtime.dag_planner import DagPlanner

    planner = DagPlanner.from_target()
    assert yaml_path.exists(), "sprint-status.yaml must be created by from_target"
    # Sanity: epics parsed and surfaced through the normalised shape.
    assert {"1", "2"}.issubset(planner.sprint_status["epics"].keys())


# ── 5. should_investigate heuristic ─────────────────────────────────────────


@pytest.mark.parametrize(
    "retry_count,category,expected",
    [
        (0, "timeout", False),
        (1, "timeout", False),
        (RETRY_THRESHOLD - 1, "timeout", False),
        (RETRY_THRESHOLD, "timeout", True),
        (RETRY_THRESHOLD + 1, "timeout", True),
        (0, "unclassified", True),
        (0, "UNKNOWN", True),
        (0, "other", True),
        (0, None, False),
        (0, "compilation_error", False),
    ],
)
def test_should_investigate_thresholds(
    retry_count: int, category: str | None, expected: bool
) -> None:
    assert should_investigate(retry_count, category) is expected


# ── 6. Mock spawn tools emit seed artifacts ─────────────────────────────────


async def _invoke(tool_obj: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Mirror tests/test_s7_memory_retro.py::_call — @tool wraps into SdkMcpTool."""
    reply = await tool_obj.handler(args)
    body = reply["content"][0]["text"]
    payload = json.loads(body)
    assert isinstance(payload, dict)
    return payload


@pytest.mark.asyncio
async def test_spawn_sprint_planning_mock_creates_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_target(tmp_path, monkeypatch, epics_md=SAMPLE_EPICS_MD)
    payload = await _invoke(spawn_sprint_planning_worktree, {"real": False})
    assert payload["mock"] is True
    assert payload["action"] in ("created_mock", "exists")


@pytest.mark.asyncio
async def test_spawn_correct_course_mock_creates_seed_md(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_target(tmp_path, monkeypatch, epics_md=None)
    payload = await _invoke(
        spawn_correct_course_worktree,
        {"story_id": "1.1", "reason": "PM dropped Stripe scope", "real": False},
    )
    assert payload["mock"] is True
    assert payload["story_id"] == "1.1"
    seed = Path(payload["path"])
    assert seed.exists()
    text = seed.read_text(encoding="utf-8")
    assert "PM dropped Stripe scope" in text


@pytest.mark.asyncio
async def test_spawn_correct_course_rejects_missing_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_target(tmp_path, monkeypatch, epics_md=None)
    payload = await _invoke(
        spawn_correct_course_worktree,
        {"story_id": "1.1", "reason": "", "real": False},
    )
    # error() helper returns {"error": "...", "code": "invalid_arg"} shape.
    assert payload.get("code") == "invalid_arg" or "error" in payload


@pytest.mark.asyncio
async def test_spawn_investigate_mock_creates_seed_md(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_target(tmp_path, monkeypatch, epics_md=None)
    payload = await _invoke(
        spawn_investigate_worktree,
        {
            "subject": "story-1.1",
            "reason": "retry_count=4, suspect flaky integration test",
            "real": False,
        },
    )
    assert payload["mock"] is True
    assert payload["subject"] == "story-1.1"
    seed = Path(payload["path"])
    assert seed.exists()
    assert "flaky integration test" in seed.read_text(encoding="utf-8")


# ── 7. Phase 4 subscribers end-to-end via bus ────────────────────────────────


@pytest.mark.asyncio
async def test_correct_course_subscriber_handles_scope_change_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from functools import partial

    from bmad_orchestrator.runtime.event_loop import EventLoop, EventType
    from bmad_orchestrator.runtime.phase4_subscribers import (
        correct_course_subscriber,
    )

    _seed_target(tmp_path, monkeypatch, epics_md=None)

    bus = EventLoop()
    bus.on(partial(correct_course_subscriber, bus=bus))
    await bus.emit(
        EventType.SPRINT_SCOPE_CHANGE_DETECTED,
        story_id="2.1",
        reason="PM removed Stripe AC",
        real=False,
    )
    await bus.dispatch_one(timeout=1.0)

    # Seed artifact must exist after the subscriber fired.
    from bmad_orchestrator.agent.tools._common import artifacts_dir

    seed = artifacts_dir() / "correct-course" / "2.1-correct-course.md"
    assert seed.exists()
    assert "PM removed Stripe AC" in seed.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_correct_course_subscriber_skips_self_emitted_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from functools import partial

    from bmad_orchestrator.runtime.event_loop import EventLoop, EventType
    from bmad_orchestrator.runtime.phase4_subscribers import (
        correct_course_subscriber,
    )

    _seed_target(tmp_path, monkeypatch, epics_md=None)

    bus = EventLoop()
    bus.on(partial(correct_course_subscriber, bus=bus))
    await bus.emit(
        EventType.SPRINT_SCOPE_CHANGE_DETECTED,
        source="phase4",  # self-emitted — must be ignored
        story_id="3.1",
        reason="loop test",
        real=False,
    )
    await bus.dispatch_one(timeout=1.0)

    from bmad_orchestrator.agent.tools._common import artifacts_dir

    seed = artifacts_dir() / "correct-course" / "3.1-correct-course.md"
    assert not seed.exists(), "self-emitted event must not trigger spawn"


@pytest.mark.asyncio
async def test_investigate_subscriber_respects_heuristic_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from functools import partial

    from bmad_orchestrator.runtime.event_loop import EventLoop, EventType
    from bmad_orchestrator.runtime.phase4_subscribers import investigate_subscriber

    _seed_target(tmp_path, monkeypatch, epics_md=None)

    bus = EventLoop()
    bus.on(partial(investigate_subscriber, bus=bus))
    # Below threshold AND classified category → must be skipped.
    await bus.emit(
        EventType.FORENSIC_INVESTIGATION_NEEDED,
        subject="story-1.1",
        reason="single timeout, retrying",
        retry_count=1,
        error_category="timeout",
        force=False,
        real=False,
    )
    await bus.dispatch_one(timeout=1.0)

    from bmad_orchestrator.agent.tools._common import artifacts_dir

    seed_dir = artifacts_dir() / "investigate"
    assert not seed_dir.exists() or not list(seed_dir.glob("*.md")), (
        "investigate must not spawn when heuristic gate not satisfied"
    )


@pytest.mark.asyncio
async def test_investigate_subscriber_spawns_on_retry_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from functools import partial

    from bmad_orchestrator.runtime.event_loop import EventLoop, EventType
    from bmad_orchestrator.runtime.phase4_subscribers import investigate_subscriber

    _seed_target(tmp_path, monkeypatch, epics_md=None)

    bus = EventLoop()
    bus.on(partial(investigate_subscriber, bus=bus))
    await bus.emit(
        EventType.FORENSIC_INVESTIGATION_NEEDED,
        subject="story-2.3",
        reason="recurring failure after 4 retries",
        retry_count=4,
        error_category="test_fail",
        force=False,
        real=False,
    )
    await bus.dispatch_one(timeout=1.0)

    from bmad_orchestrator.agent.tools._common import artifacts_dir

    seed = artifacts_dir() / "investigate" / "story-2.3-investigate.md"
    assert seed.exists()
    assert "recurring failure after 4 retries" in seed.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_investigate_subscriber_force_bypasses_heuristic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from functools import partial

    from bmad_orchestrator.runtime.event_loop import EventLoop, EventType
    from bmad_orchestrator.runtime.phase4_subscribers import investigate_subscriber

    _seed_target(tmp_path, monkeypatch, epics_md=None)

    bus = EventLoop()
    bus.on(partial(investigate_subscriber, bus=bus))
    await bus.emit(
        EventType.FORENSIC_INVESTIGATION_NEEDED,
        subject="story-9.9",
        reason="manual deep-dive request",
        retry_count=0,
        error_category="timeout",
        force=True,  # CLI manual trigger
        real=False,
    )
    await bus.dispatch_one(timeout=1.0)

    from bmad_orchestrator.agent.tools._common import artifacts_dir

    seed = artifacts_dir() / "investigate" / "story-9.9-investigate.md"
    assert seed.exists()
