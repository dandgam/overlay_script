"""F1 regression tests — P0 fixes for embed_phase45_with_selflearning.

Spec: spec/spec_embed_phase45_fixes.md §F1.

Coverage (12 tests):

* **P0-1** — 3 subscribers (code_review / merge_to_integration / quarterly_sweep)
  wired into ``EventLoop`` inside ``_run_real_pilot``.
* **P0-2** — ``WAVE_BOUNDARY_REACHED`` payload contains ``completed_stories``
  on both mock and real emit sites; quarterly sweep can pick it up.
* **P0-3** — ``_load_policy_yaml`` wraps ``yaml.YAMLError`` into
  :class:`PolicyApplyError` so batched apply doesn't abort on one bad file.
* **P0-4** — ``load_proposals_yaml`` rejects unknown / hostile field names
  (``../etc/passwd``) before they reach pydantic validation.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from bmad_orchestrator.agent import run as run_module
from bmad_orchestrator.agent.run import (
    _run_real_pilot,
    configure_code_review_gate,
    quarterly_sweep_subscriber,
)
from bmad_orchestrator.agent.safety.budget_guard import BudgetGuard
from bmad_orchestrator.config import ModelConfig, load_settings
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.runtime.lesson_parser import (
    LessonProposal,
    LessonProposalInvalidError,
    PolicyApplyError,
    _load_policy_yaml,
    apply_proposals_batch,
    load_proposals_yaml,
)
from bmad_orchestrator.runtime.worker_spawn import WorkerHandle
from bmad_orchestrator.skills_repo import CodeReviewGates

# ── shared helpers ────────────────────────────────────────────────────────────


def _make_target_with_stories(tmp_path: Path, *story_ids: str) -> Path:
    target = tmp_path / "proj"
    target.mkdir(exist_ok=True)
    # Patch Y 2026-05-18: _ensure_git_worktree (commit 358d77f) needs a real
    # git repo with HEAD commit at the target — initialise here.
    subprocess.run(["git", "init", "-q", "-b", "main", str(target)], check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.name", "t"], check=True)
    (target / "README").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(target), "add", "README"], check=True)
    subprocess.run(["git", "-C", str(target), "commit", "-q", "-m", "seed"], check=True)
    artifacts = target / "_bmad-output" / "planning-artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    stories_block = "\n".join(f"      {sid}: ready-for-dev" for sid in story_ids)
    (artifacts / "sprint-status.yaml").write_text(
        "wave: w\n"
        "epics:\n"
        "  e1:\n"
        "    stories:\n"
        f"{stories_block}\n",
        encoding="utf-8",
    )
    stories_dir = artifacts / "stories"
    stories_dir.mkdir(exist_ok=True)
    for sid in story_ids:
        (stories_dir / f"{sid}.md").write_text(
            f"# Story {sid}\n\n"
            "- **epic:** 1\n"
            "- **status:** ready\n"
            "- **risk:** low\n"
            "- **estimated_tokens:** 1000\n"
            "- **estimated_minutes:** 5\n"
            "- **touches_files:** []\n"
            "- **touches_shared:** []\n"
            "- **depends_on:** []\n",
            encoding="utf-8",
        )
    return target


def _seed_jsonl_completed(jsonl_path: Path, story_id: str) -> None:
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "event_type": "worker_completed",
        "worktree": str(jsonl_path.parent),
        "story_id": story_id,
        "exit_code": 0,
        "status": "success",
        "ts": "2026-05-17T00:00:00+00:00",
    }
    jsonl_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _make_fake_spawn(jsonl_root: Path) -> Any:
    async def _fake_spawn(
        *,
        worktree: str,
        story_id: str,
        branch: str,
        mock: bool = False,
        sandbox_network: str = "full",
        **_: Any,
    ) -> WorkerHandle:
        jsonl_path = jsonl_root / f"{story_id}.jsonl"
        _seed_jsonl_completed(jsonl_path, story_id)
        return WorkerHandle(
            worktree=worktree,
            story_id=story_id,
            branch=branch,
            pid=12345,
            jsonl_path=jsonl_path,
            process=None,
            mock=mock,
            sandbox_kind="bwrap",
        )

    return _fake_spawn


async def _drain(bus: EventLoop) -> list[Event]:
    captured: list[Event] = []

    async def _capture(ev: Event) -> None:
        captured.append(ev)

    bus.on(_capture)
    while await bus.dispatch_one(timeout=0.01) is not None:
        pass
    await bus.stop()
    return captured


def _record_events(bus: EventLoop) -> list[Event]:
    """Attach a capture subscriber BEFORE the pilot runs.

    Since NEW-7 the real pilot drains its own bus (``EventLoop.drain``) before
    returning, so the queue is empty post-run. Capture must subscribe up front
    and observe events as the production drain dispatches them.
    """
    captured: list[Event] = []

    async def _capture(ev: Event) -> None:
        captured.append(ev)

    bus.on(_capture)
    return captured


# ── P0-1: subscribers wired into bus ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_p0_1_real_pilot_wires_three_subscribers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """``_run_real_pilot`` must register exactly 7 subscribers on the bus
    (stage5_completeness [Patch S] + build_check [Patch N] + deletion_safety
    [Patch C] + code_review + security_review [Patch X] + merge_to_integration
    + quarterly_sweep).
    Pre-call ``len(bus._subs) == 0``; post-call ``len(bus._subs) == 7``.
    """
    monkeypatch.delenv("BMAD_REQUIRE_SANDBOX", raising=False)
    target = _make_target_with_stories(tmp_path)  # zero stories → empty DAG
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target))

    bus = EventLoop()
    budget = BudgetGuard(load_settings().budget, event_loop=bus)
    assert len(bus._subs) == 0

    try:
        await _run_real_pilot(
            bus, project="proj", wave="w", max_parallel=1, max_stories=1,
            max_spend_usd=10.0, budget=budget, state_db=None, session_id=None,
            models=ModelConfig(), options={}, settings=load_settings(),
        )
    finally:
        await bus.stop()

    # 7 canonical + elicitation_subscriber (Phase 3) + supervisor_subscriber (Phase 4)
    # + self_learning_subscriber (Phase 5) + correct_course + investigate
    # (BMad Phase 4 gap-closure 2026-05-19) + _respawn_subscriber (NEW-38) = 13.
    assert len(bus._subs) == 13, (
        f"Expected 13 subscribers wired after _run_real_pilot, "
        f"got {len(bus._subs)}"
    )


@pytest.mark.asyncio
async def test_p0_1_subscribers_are_callable_event_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Wired subscribers must satisfy ``EventCallback`` contract — callable
    with a single ``Event`` arg, returning an awaitable. Without ``partial``
    (or equivalent), ``cb(event)`` would TypeError because the originals take
    ``(event, bus)``."""
    monkeypatch.delenv("BMAD_REQUIRE_SANDBOX", raising=False)
    target = _make_target_with_stories(tmp_path)
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target))

    bus = EventLoop()
    budget = BudgetGuard(load_settings().budget, event_loop=bus)
    try:
        await _run_real_pilot(
            bus, project="proj", wave="w", max_parallel=1, max_stories=1,
            max_spend_usd=10.0, budget=budget, state_db=None, session_id=None,
            models=ModelConfig(), options={}, settings=load_settings(),
        )
    finally:
        await bus.stop()

    # Each subscriber accepts a single Event arg without TypeError.
    ev = Event(type=EventType.PHASE4_COMPLETE, payload={"info": "noop"})
    for sub in bus._subs:
        # Each callback must be awaitable from a single-Event call.
        result = sub(ev)
        # Must produce an awaitable (coroutine or Future-like).
        assert hasattr(result, "__await__") or hasattr(result, "__iter__"), (
            f"Subscriber {sub!r} did not return an awaitable for Event-only call"
        )
        # Drain it so the coroutine doesn't warn about un-awaited.
        await result


@pytest.mark.asyncio
async def test_p0_1_quarterly_sweep_subscriber_wired_emits_on_wave_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """End-to-end: real pilot runs (wires subs), then we manually feed a
    WAVE_BOUNDARY_REACHED with completed_stories=50 through the bus and
    confirm a COMPLIANCE_SWEEP_NEEDED comes back — proves the wired
    quarterly_sweep_subscriber actually fires via the bus."""
    monkeypatch.delenv("BMAD_REQUIRE_SANDBOX", raising=False)
    target = _make_target_with_stories(tmp_path)
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target))

    bus = EventLoop()
    budget = BudgetGuard(load_settings().budget, event_loop=bus)
    await _run_real_pilot(
        bus, project="proj", wave="w", max_parallel=1, max_stories=1,
        max_spend_usd=10.0, budget=budget, state_db=None, session_id=None,
        models=ModelConfig(), options={}, settings=load_settings(),
    )

    # Real pilot already emitted WAVE_BOUNDARY_REACHED with completed_stories=0
    # (empty DAG). Drain that one out, then emit a fresh 50-story boundary so
    # the modulo gate trips.
    while await bus.dispatch_one(timeout=0.01) is not None:
        pass

    configure_code_review_gate(
        target_project=target, wave="w",
        gates_override=CodeReviewGates(sweep_every_stories=50),
    )
    await bus.emit(
        EventType.WAVE_BOUNDARY_REACHED,
        wave="w", spawned=[], rounds=1, completed_stories=50,
    )
    # Subscribers fire under EventLoop.dispatch_one — feed the queued event
    # through and capture downstream emissions.
    captured: list[Event] = []
    async def _capture(ev: Event) -> None:
        captured.append(ev)
    bus.on(_capture)

    # First dispatch_one handles the WAVE_BOUNDARY_REACHED and lets the
    # quarterly_sweep_subscriber emit COMPLIANCE_SWEEP_NEEDED into the queue.
    await bus.dispatch_one(timeout=0.1)
    # Second drains the COMPLIANCE_SWEEP_NEEDED so _capture sees it.
    while await bus.dispatch_one(timeout=0.01) is not None:
        pass
    await bus.stop()
    configure_code_review_gate(target_project=None, wave=None)

    compliance = [e for e in captured if e.type == EventType.COMPLIANCE_SWEEP_NEEDED]
    assert len(compliance) == 1, (
        f"Expected wired quarterly_sweep_subscriber to emit "
        f"COMPLIANCE_SWEEP_NEEDED; got events {[e.type for e in captured]}"
    )
    assert compliance[0].payload["completed_stories"] == 50


# ── P0-2: WAVE_BOUNDARY_REACHED payload carries completed_stories ─────────────


@pytest.mark.asyncio
async def test_p0_2_real_pilot_wave_boundary_has_completed_stories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Real-mode WAVE_BOUNDARY_REACHED emit must include ``completed_stories``
    equal to ``len(spawned)``."""
    monkeypatch.delenv("BMAD_REQUIRE_SANDBOX", raising=False)
    target = _make_target_with_stories(tmp_path, "s1", "s2")
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target))

    monkeypatch.setattr(
        run_module, "runtime_spawn_worker", _make_fake_spawn(tmp_path / "jsonl"),
    )

    bus = EventLoop()
    budget = BudgetGuard(load_settings().budget, event_loop=bus)
    events = _record_events(bus)
    await _run_real_pilot(
        bus, project="proj", wave="w", max_parallel=2, max_stories=10,
        max_spend_usd=100.0, budget=budget, state_db=None, session_id=None,
        models=ModelConfig(), options={}, settings=load_settings(),
    )

    await _drain(bus)
    boundaries = [e for e in events if e.type == EventType.WAVE_BOUNDARY_REACHED]
    assert len(boundaries) == 1
    payload = boundaries[0].payload
    assert "completed_stories" in payload, (
        f"WAVE_BOUNDARY_REACHED missing 'completed_stories' key: {payload!r}"
    )
    assert payload["completed_stories"] == len(payload["spawned"])


@pytest.mark.asyncio
async def test_p0_2_real_pilot_completed_stories_matches_spawned_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """``completed_stories`` must equal the number of spawned story IDs."""
    monkeypatch.delenv("BMAD_REQUIRE_SANDBOX", raising=False)
    target = _make_target_with_stories(tmp_path, "s1", "s2", "s3")
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target))
    monkeypatch.setattr(
        run_module, "runtime_spawn_worker", _make_fake_spawn(tmp_path / "jsonl"),
    )

    bus = EventLoop()
    budget = BudgetGuard(load_settings().budget, event_loop=bus)
    events = _record_events(bus)
    await _run_real_pilot(
        bus, project="proj", wave="w", max_parallel=3, max_stories=10,
        max_spend_usd=100.0, budget=budget, state_db=None, session_id=None,
        models=ModelConfig(), options={}, settings=load_settings(),
    )

    await _drain(bus)
    boundaries = [e for e in events if e.type == EventType.WAVE_BOUNDARY_REACHED]
    assert boundaries[0].payload["completed_stories"] == 3


@pytest.mark.asyncio
async def test_p0_2_mock_pilot_wave_boundary_has_completed_stories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Mock-mode WAVE_BOUNDARY_REACHED emit (line ~547) must also include
    ``completed_stories``. The mock path is what the dispatch tests cover —
    leaving it stale would cause real and mock paths to diverge silently."""
    from bmad_orchestrator.agent.run import _run_mock_pilot

    target = _make_target_with_stories(tmp_path, "s1")
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target))
    monkeypatch.setattr(
        run_module, "runtime_spawn_worker", _make_fake_spawn(tmp_path / "jsonl"),
    )

    bus = EventLoop()
    budget = BudgetGuard(load_settings().budget, event_loop=bus)
    await _run_mock_pilot(
        bus, wave="w", max_parallel=1, budget=budget, settings=load_settings()
    )
    events = await _drain(bus)

    boundaries = [e for e in events if e.type == EventType.WAVE_BOUNDARY_REACHED]
    assert len(boundaries) == 1
    payload = boundaries[0].payload
    assert "completed_stories" in payload
    assert payload["completed_stories"] == len(payload.get("spawned", []))


@pytest.mark.asyncio
async def test_p0_2_quarterly_sweep_fires_at_completed_stories_50() -> None:
    """When ``completed_stories=50`` lands in WAVE_BOUNDARY_REACHED payload
    and ``sweep_every_stories=50`` is configured, the subscriber emits
    COMPLIANCE_SWEEP_NEEDED — directly verifies P0-2 unblocks the quarterly
    sweep machinery (without P0-2 the field would be missing, ``raw=0``,
    early-return)."""
    configure_code_review_gate(
        target_project=Path("/tmp"), wave="1a",
        gates_override=CodeReviewGates(sweep_every_stories=50),
    )
    try:
        bus = EventLoop()
        ev = Event(
            type=EventType.WAVE_BOUNDARY_REACHED,
            payload={"wave": "1a", "spawned": ["x"] * 50, "completed_stories": 50},
        )
        await quarterly_sweep_subscriber(ev, bus)
        emitted: list[Event] = []
        while True:
            try:
                emitted.append(bus.queue.get_nowait())
            except Exception:
                break
        assert len(emitted) == 1
        assert emitted[0].type == EventType.COMPLIANCE_SWEEP_NEEDED
        assert emitted[0].payload["completed_stories"] == 50
        await bus.stop()
    finally:
        configure_code_review_gate(target_project=None, wave=None)


# ── P0-3: _load_policy_yaml wraps yaml.YAMLError ──────────────────────────────


def test_p0_3_load_policy_yaml_malformed_raises_policy_apply_error(
    tmp_path: Path,
) -> None:
    """Malformed YAML on disk → :class:`PolicyApplyError` (not raw
    ``yaml.YAMLError``). Without the wrap, ``apply_proposals_batch`` would
    crash mid-loop on one bad policy file instead of recording the error and
    continuing to the next proposal."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(":\n  - this is not valid\n: yaml at all\n", encoding="utf-8")

    with pytest.raises(PolicyApplyError, match="not valid"):
        _load_policy_yaml(bad)


def test_p0_3_load_policy_yaml_does_not_propagate_raw_yamlerror(
    tmp_path: Path,
) -> None:
    """Verify the original ``yaml.YAMLError`` chains via ``__cause__`` but the
    surface exception is :class:`PolicyApplyError`. Callers that catch
    PolicyApplyError in batch mode must not see a leaking YAMLError."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("a: [b: c\n", encoding="utf-8")  # unterminated list

    try:
        _load_policy_yaml(bad)
    except yaml.YAMLError:
        pytest.fail("Raw yaml.YAMLError leaked past PolicyApplyError wrap")
    except PolicyApplyError as e:
        assert isinstance(e.__cause__, yaml.YAMLError)


def test_p0_3_apply_proposals_batch_continues_on_bad_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Batched apply must record the PolicyApplyError in ``result.errors``
    and proceed to the next proposal — not abort. This is the production
    invariant the audit triad flagged: one corrupted policy YAML must not
    poison the rest of the batch."""
    skills_root = tmp_path / "skills"
    policy = skills_root / "policy"
    policy.mkdir(parents=True)
    # Bad policy YAML — code-review-gates is malformed.
    (policy / "code-review-gates.yaml").write_text(":\nfoo: [bar\n", encoding="utf-8")
    # Good policy YAML for the second proposal.
    (policy / "cost-tuning.yaml").write_text(
        "daily_cap_usd: 50.0\nstory_reserve_usd: 0.5\nmax_concurrent_workers: 4\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("BMAD_AUDIT_LOG", str(tmp_path / "audit.jsonl"))

    proposals = [
        LessonProposal(
            policy_file="code-review-gates", field="p0_threshold",
            before=0.8, after=0.7, rationale=None, source_file="x.md",
        ),
        LessonProposal(
            policy_file="cost-tuning", field="daily_cap_usd",
            before=50.0, after=75.0, rationale=None, source_file="x.md",
        ),
    ]

    result = apply_proposals_batch(
        proposals, skills_root=skills_root, auto_apply=True,
    )

    assert len(result.errors) == 1, f"expected 1 error, got {result.errors!r}"
    assert len(result.applied) == 1, (
        f"expected the good proposal to still apply; got {result.applied!r}"
    )
    bad_proposal, msg = result.errors[0]
    assert bad_proposal.policy_file == "code-review-gates"
    assert "not valid" in msg.lower()


# ── P0-4: load_proposals_yaml validates field name ────────────────────────────


def test_p0_4_load_proposals_yaml_rejects_unknown_field(tmp_path: Path) -> None:
    """``field`` not on the pydantic model → LessonProposalInvalidError."""
    orchestrator_home = tmp_path / "home"
    config_dir = orchestrator_home / "_config" / "projects" / "odyssey"
    config_dir.mkdir(parents=True)
    (config_dir / "policy-proposals.yaml").write_text(
        "proposals:\n"
        "  - policy_file: code-review-gates\n"
        "    field: not_a_real_field\n"
        "    before: 0.8\n"
        "    after: 0.7\n",
        encoding="utf-8",
    )

    with pytest.raises(LessonProposalInvalidError, match=r"unknown field"):
        load_proposals_yaml("odyssey", orchestrator_home=orchestrator_home)


def test_p0_4_load_proposals_yaml_rejects_path_traversal_field(
    tmp_path: Path,
) -> None:
    """Hostile field name (``../etc/passwd``) → rejected. Defence-in-depth:
    even though pydantic ``extra='ignore'`` would silently drop it, we never
    want such a key to round-trip back into the proposals YAML."""
    orchestrator_home = tmp_path / "home"
    config_dir = orchestrator_home / "_config" / "projects" / "odyssey"
    config_dir.mkdir(parents=True)
    (config_dir / "policy-proposals.yaml").write_text(
        "proposals:\n"
        "  - policy_file: code-review-gates\n"
        "    field: ../etc/passwd\n"
        "    before: 0.8\n"
        "    after: 0.7\n",
        encoding="utf-8",
    )

    with pytest.raises(LessonProposalInvalidError, match=r"unknown field"):
        load_proposals_yaml("odyssey", orchestrator_home=orchestrator_home)


def test_p0_4_load_proposals_yaml_accepts_valid_field(tmp_path: Path) -> None:
    """Sanity: valid field on the model passes through."""
    orchestrator_home = tmp_path / "home"
    config_dir = orchestrator_home / "_config" / "projects" / "odyssey"
    config_dir.mkdir(parents=True)
    (config_dir / "policy-proposals.yaml").write_text(
        "proposals:\n"
        "  - policy_file: code-review-gates\n"
        "    field: p0_threshold\n"
        "    before: 0.8\n"
        "    after: 0.7\n",
        encoding="utf-8",
    )

    proposals = load_proposals_yaml("odyssey", orchestrator_home=orchestrator_home)
    assert len(proposals) == 1
    assert proposals[0].field == "p0_threshold"
    assert proposals[0].after == 0.7
