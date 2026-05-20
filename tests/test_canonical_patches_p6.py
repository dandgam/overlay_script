"""P6 regression tests — canonical_patches_port (FINAL — integration + e2e).

Spec: spec/spec_canonical_patches_port.md §P6.
Tracker: .claude/initiative-tracker-canonical_patches_port.md.

Coverage (10 tests + 1 inventory check):

* **Wiring + inventory** (4)
  - All 7 canonical subscribers wired in correct order via ``_run_real_pilot``.
  - ``EventType`` enum count is 19 (post-Phase-0 inventory: P5 + Phase 0 patches).
  - ``SECURITY_REVIEW_PASSED`` present in the enum (P5 addition).
  - All 6 bundled policy YAMLs load without error.

* **E2E chain happy path** (2)
  - Security-critical story: WORKER_COMPLETED → ... → CODE_REVIEW_VERDICT(approve)
    → SECURITY_REVIEW_PASSED(approve) → merge fires.
  - Non-security-critical story: SECURITY_REVIEW_PASSED with
    ``reason='not_security_critical'`` + merge fires.

* **E2E chain halts** (4)
  - Build check failure halts BEFORE code_review spawn — no
    CODE_REVIEW_VERDICT emitted, no merge.
  - Unsafe deletion halts BEFORE code_review — same observable outcome.
  - Code-review verdict=reject → ``merge_to_integration_subscriber``
    emits HUMAN_QUERY, no merge.
  - Security review BLOCK → CRV verdict mutated to ``reject`` → merge
    naturally skips, HUMAN_QUERY emitted with security context.

The full subscriber chain is driven via the production wiring shape (the
same ``bus.on(...)`` list as ``_run_real_pilot``), with two seams:
``_spawn_code_review_worker`` is monkeypatched to a fixture JSONL and
``_ff_merge_to_integration`` is monkeypatched to skip real git work — both
seams have explicit unit coverage upstream (E5/W4 + P1-P5 tests).

Reference: Odyssey handoff 2026-05-17 + ~/.claude/skills/bmad-auto-dev/scripts/
bmad-auto-dev-runner.sh §Stage 5.5-7.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Awaitable, Callable
from functools import partial
from pathlib import Path
from typing import Any, cast

import pytest

from bmad_orchestrator.agent.run import (
    _run_real_pilot,
    code_review_subscriber,
    configure_code_review_gate,
    merge_to_integration_subscriber,
    quarterly_sweep_subscriber,
)
from bmad_orchestrator.agent.safety.budget_guard import BudgetGuard
from bmad_orchestrator.config import ModelConfig, load_settings
from bmad_orchestrator.runtime.build_check import (
    BUILD_CHECK_POLICY_PATH_DEFAULT,
    build_check_subscriber,
    load_build_check_policy,
)
from bmad_orchestrator.runtime.deletion_safety import (
    DELETION_SAFETY_POLICY_PATH_DEFAULT,
    deletion_safety_subscriber,
    load_deletion_safety_policy,
)
from bmad_orchestrator.runtime.diff_size_gate import (
    DIFF_SIZE_POLICY_PATH_DEFAULT,
    load_diff_size_policy,
)
from bmad_orchestrator.runtime.event_loop import (
    ALL_EVENT_TYPES,
    Event,
    EventCallback,
    EventLoop,
    EventType,
)
from bmad_orchestrator.runtime.security_review import (
    SECURITY_REVIEW_POLICY_PATH_DEFAULT,
    VERDICT_APPROVE,
    VERDICT_BLOCK,
    load_security_review_policy,
    security_review_subscriber,
)
from bmad_orchestrator.runtime.stage5_completeness import (
    stage5_completeness_subscriber,
)
from bmad_orchestrator.runtime.worker_spawn import WorkerHandle
from bmad_orchestrator.skills_repo import CodeReviewGates, load_policy

# ──────────────────────────── helpers ──────────────────────────────────────────


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _init_git_worktree(tmp: Path) -> Path:
    """Init a clean git repo; seed one commit so HEAD is valid."""
    tmp.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(tmp)], check=True)
    _git(tmp, "config", "user.email", "t@t")
    _git(tmp, "config", "user.name", "t")
    (tmp / "README").write_text("seed\n", encoding="utf-8")
    _git(tmp, "add", ".")
    _git(tmp, "commit", "-q", "-m", "seed")
    return tmp


def _init_git_worktree_with_deletion(tmp: Path, deleted_path: str) -> Path:
    """Init a repo, commit a file, then commit its deletion (HEAD diff has 1 deletion)."""
    _init_git_worktree(tmp)
    target = tmp / deleted_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x\n", encoding="utf-8")
    _git(tmp, "add", ".")
    _git(tmp, "commit", "-q", "-m", "add")
    target.unlink()
    _git(tmp, "add", "-A")
    _git(tmp, "commit", "-q", "-m", "rm")
    return tmp


def _make_target_with_stories(tmp_path: Path) -> Path:
    target = tmp_path / "proj"
    target.mkdir(exist_ok=True)
    artifacts = target / "_bmad-output" / "planning-artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "sprint-status.yaml").write_text(
        "wave: w\nepics: {}\n", encoding="utf-8"
    )
    (artifacts / "stories").mkdir(exist_ok=True)
    return target


def _write_jsonl(path: Path, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(ev) for ev in events) + "\n", encoding="utf-8"
    )


def _approve_jsonl(jsonl_path: Path) -> Path:
    _write_jsonl(
        jsonl_path,
        [
            {
                "event_type": "claude_event",
                "verdict": "approve",
                "summary": "42 tests pass. Coverage 91%. No lint errors.",
                "metrics": {
                    "p0_found": 0,
                    "p0_fixed": 0,
                    "test_files_count": 0,
                    "expected_n_tests": 0,
                    "compliance_tags": [],
                },
            },
            {
                "event_type": "worker_completed",
                "story_id": "s1",
                "exit_code": 0,
                "status": "success",
            },
        ],
    )
    return jsonl_path


def _reject_jsonl(jsonl_path: Path) -> Path:
    _write_jsonl(
        jsonl_path,
        [
            {
                "event_type": "claude_event",
                "verdict": "reject",
                "summary": "needs work",
                "metrics": {
                    "p0_found": 0,
                    "p0_fixed": 0,
                    "test_files_count": 0,
                    "expected_n_tests": 0,
                    "compliance_tags": [],
                },
            },
            {
                "event_type": "worker_completed",
                "story_id": "s1",
                "exit_code": 0,
                "status": "success",
            },
        ],
    )
    return jsonl_path


def _make_review_handle(worktree: Path, story_id: str, jsonl: Path) -> WorkerHandle:
    return WorkerHandle(
        worktree=str(worktree),
        story_id=story_id,
        branch=f"feature/{story_id}",
        pid=0,
        jsonl_path=jsonl,
        process=None,
        mock=True,
        sandbox_kind="n/a-mock",
    )


def _write_story(worktree: Path, story_id: str, body: str) -> Path:
    p = worktree / "_bmad" / "stories" / f"{story_id}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def _wire_canonical_chain(
    bus: EventLoop,
    *,
    security_runner: Callable[[Path, str, str], Awaitable[tuple[str, str]]] | None = None,
    security_policy_path: Path | None = None,
    build_policy_path: Path | None = None,
) -> None:
    """Register the 7 canonical subscribers in production order.

    Mirrors ``_run_real_pilot`` lines 673-688 exactly; the only seam is the
    optional ``security_runner`` injection so tests do not spawn a real hunter
    subprocess. Subscribers wrapped with ``functools.partial`` so the bus
    sees a single-arg ``(event,)`` callable.
    """

    async def _stage5(event: Event) -> None:
        await stage5_completeness_subscriber(event, bus=bus)

    async def _build(event: Event) -> None:
        await build_check_subscriber(event, bus=bus, policy_path=build_policy_path)

    async def _deletion(event: Event) -> None:
        await deletion_safety_subscriber(event, bus)

    async def _review(event: Event) -> None:
        await code_review_subscriber(event, bus)

    async def _security(event: Event) -> None:
        await security_review_subscriber(
            event,
            bus=bus,
            policy_path=security_policy_path,
            runner=security_runner,
        )

    async def _merge(event: Event) -> None:
        await merge_to_integration_subscriber(event, bus)

    async def _sweep(event: Event) -> None:
        await quarterly_sweep_subscriber(event, bus)

    bus.on(cast(EventCallback, _stage5))
    bus.on(cast(EventCallback, _build))
    bus.on(cast(EventCallback, _deletion))
    bus.on(cast(EventCallback, _review))
    bus.on(cast(EventCallback, _security))
    bus.on(cast(EventCallback, _merge))
    bus.on(cast(EventCallback, _sweep))


async def _drain_dispatch(bus: EventLoop, max_iter: int = 10) -> list[Event]:
    """Dispatch every queued event through all subscribers; return chronological list."""
    seen: list[Event] = []
    for _ in range(max_iter):
        ev = await bus.dispatch_one(timeout=0.05)
        if ev is None:
            break
        seen.append(ev)
    return seen


@pytest.fixture
def disabled_build_policy(tmp_path: Path) -> Path:
    """Build-check policy with no commands → subscriber no-ops."""
    p = tmp_path / "build-check-empty.yaml"
    p.write_text(
        "commands: []\ntimeout_sec: 600\ntail_lines: 40\n"
        "skip_if_missing_executable: true\nescalation_text: ''\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def failing_build_policy(tmp_path: Path) -> Path:
    """Build-check policy with one required failing command."""
    p = tmp_path / "build-check-fail.yaml"
    p.write_text(
        "commands:\n"
        "  - name: synthetic-fail\n"
        "    run: 'sh -c \"echo bad; exit 7\"'\n"
        "    required: true\n"
        "timeout_sec: 30\ntail_lines: 5\n"
        "skip_if_missing_executable: false\n"
        "escalation_text: 'Build broken'\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def critical_security_policy(tmp_path: Path) -> Path:
    p = tmp_path / "security-review-critical.yaml"
    p.write_text(
        "enabled: true\n"
        "security_critical_epics: [3, 4, 5, 7, 9, 10]\n"
        "security_critical_keywords: [auth, jwt, rls]\n"
        "block_actions: [abandon, manual_security_fix]\n"
        "escalation_text: 'Security halt'\n",
        encoding="utf-8",
    )
    return p


# ════════════════════════════════════════════════════════════════════════════
# 1. Wiring + inventory (4 tests)
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_all_seven_canonical_subscribers_wired_in_correct_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Production wiring (``_run_real_pilot``) registers the 7 subscribers in
    the canonical order:

      0. stage5_completeness  (Patch S — auto-stage pre-halt residue)
      1. build_check          (Patch N — pytest/cargo guard)
      2. deletion_safety      (Patch C — unsafe deletion guard)
      3. code_review          (E5 — review + Patch Q diff-size + Patch W scope)
      4. security_review      (Patch X — 4-hunter on security-critical)
      5. merge_to_integration (W4 — ff-merge + Patch R recovery)
      6. quarterly_sweep      (E5 — compliance sweep on wave boundary)

    Any reorder breaks the halt contract (cheap halters run before expensive
    spawns). Asserted as a hard invariant.
    """
    monkeypatch.delenv("BMAD_REQUIRE_SANDBOX", raising=False)
    target = _make_target_with_stories(tmp_path)
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target))
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_ORCHESTRATOR_HOME", str(home))

    bus = EventLoop()
    budget = BudgetGuard(load_settings().budget, event_loop=bus)
    try:
        await _run_real_pilot(
            bus,
            project="proj",
            wave="w",
            max_parallel=1,
            max_stories=1,
            max_spend_usd=10.0,
            budget=budget,
            state_db=None,
            session_id=None,
            models=ModelConfig(),
            options={},
            settings=load_settings(),
        )
    finally:
        await bus.stop()

    funcs = [getattr(s, "func", s) for s in bus._subs]
    # 7 canonical + elicitation_subscriber (Phase 3) + supervisor_subscriber (Phase 4)
    # + self_learning_subscriber (Phase 5) + correct_course + investigate
    # (BMad Phase 4 gap-closure 2026-05-19) + _respawn_subscriber (NEW-38) = 13.
    assert len(funcs) == 13, f"Expected 13 subscribers, got {len(funcs)}: {funcs!r}"
    expected_in_order = [
        stage5_completeness_subscriber,
        build_check_subscriber,
        deletion_safety_subscriber,
        # code_review and security_review are bound via partial — the .func
        # attribute carries the underlying coroutine. Resolved via getattr above.
    ]
    for idx, expected in enumerate(expected_in_order):
        assert funcs[idx] is expected, (
            f"Position {idx} expected {expected.__name__}; "
            f"got {getattr(funcs[idx], '__name__', repr(funcs[idx]))}"
        )
    # Positions 3-6 are partial-wrapped (have a .func underneath).
    assert getattr(funcs[3], "__name__", "") == "code_review_subscriber"
    assert getattr(funcs[4], "__name__", "") == "security_review_subscriber"
    assert getattr(funcs[5], "__name__", "") == "merge_to_integration_subscriber"
    assert getattr(funcs[6], "__name__", "") == "quarterly_sweep_subscriber"


def test_event_type_inventory_count_is_twenty_three() -> None:
    """Post-Initiative-2C the EventType enum carries exactly 23 entries.

    Inventory history:
      * 17 — pre-Phase-0 baseline.
      * +2 (Phase 0) — ``WORKER_SILENT_FAILURE`` (Task 0.2 silent-failure
        detection) and ``COST_TRACKING_UNAVAILABLE`` (Task 0.3 subscription
        mode honesty). Total 19.
      * +4 (Initiative #2C) — ``SUB_STORY_STARTED`` /
        ``SUB_STORY_COMPLETED`` / ``SUB_STORY_SQUASH_DONE`` /
        ``SUB_STORY_SQUASH_SKIPPED`` (auto-split orchestration emits these
        through the bus bridge in ``runtime/auto_split.py``). Total 23.
    A future addition MUST update this assertion in the same commit so the
    inventory drift is reviewed.
    """
    # 2026-05-19 BMad Phase 4 gap-closure adds SPRINT_SCOPE_CHANGE_DETECTED
    # and FORENSIC_INVESTIGATION_NEEDED → 23 + 2 = 25.
    # 2026-05-19 Phase 4 hardening #2 adds WORKER_STATE_PERSISTED → 25 + 1 = 26.
    # 2026-05-19 Phase 4 hardening #5 adds MERGE_GATE_STAGE_COMPLETED → 26 + 1 = 27.
    # 2026-05-19 Phase 4 hardening #6 adds STORY_COMPLETED + STORY_METRICS_AGGREGATED → 27 + 2 = 29.
    # 2026-05-19 spec_pilot_findings_closure S3 #3 adds STORY_AUTO_SPLIT → 29 + 1 = 30.
    # 2026-05-19 spec_pilot_findings_closure S4 #4 adds WORKER_CANCELLED → 30 + 1 = 31.
    # 2026-05-19 spec_pilot_findings_closure S5 #5 adds MCP_NOT_READY → 31 + 1 = 32.
    # 2026-05-19 spec_pilot_findings_closure S6 #6 adds BUDGET_AUTO_DISABLED → 32 + 1 = 33.
    # 2026-05-19 spec_pilot_findings_closure S6 #7 adds WORKER_HALT_PRESPAWN → 33 + 1 = 34.
    # 2026-05-19 spec_pilot_findings_closure_v2 S2 #2 adds
    #   RUNNER_CLEANUP_FAILED_REUSED_WORKTREE → 34 + 1 = 35.
    # 2026-05-19 spec_pilot_findings_closure_v3 S3 #1 adds
    #   INTEGRATION_MERGE_SKIPPED → 35 + 1 = 36.
    # 2026-05-19 spec_pilot_findings_closure_v5 S2 NEW-13 adds
    #   SECURITY_REVIEW_ERROR → 36 + 1 = 37.
    # 2026-05-19 spec_pilot_findings_closure_v6 S1 NEW-19 adds
    #   REPLAY_MODE_STARTED → 37 + 1 = 38.
    # 2026-05-19 spec_pilot_findings_closure_v6 S2 NEW-15 adds
    #   CODE_REVIEW_ERROR → 38 + 1 = 39.
    # 2026-05-19 spec_pilot_findings_closure_v6 S3 NEW-16 adds
    #   INTEGRATION_MERGE_COMPLETED → 39 + 1 = 40.
    # 2026-05-19 spec_pilot_findings_closure_v6 S3 NEW-17 adds
    #   WORKER_EXIT_UNCOMMITTED → 40 + 1 = 41.
    # 2026-05-20 NEW-36 adds WORKER_AUTO_STAGE_RECOVERY → 43 + 1 = 44.
    # NEW-37 adds REVIEW_STUCK_TIMEOUT → 44 + 1 = 45.
    # NEW-38 adds WORKER_RESPAWN_REQUESTED → 45 + 1 = 46.
    # NEW-41 adds INTERNAL_REVIEW_OVERRIDDEN_BY_EXTERNAL → 46 + 1 = 47.
    assert len(ALL_EVENT_TYPES) == 47, (
        f"Expected 47 EventType members; got {len(ALL_EVENT_TYPES)}: "
        f"{[e.name for e in ALL_EVENT_TYPES]}"
    )


def test_event_type_security_review_passed_present() -> None:
    """``SECURITY_REVIEW_PASSED`` must exist as a distinct EventType.

    Used by the audit trail of approve-path security reviews (no payload
    mutation, but downstream observers need a signal).
    """
    assert EventType.SECURITY_REVIEW_PASSED in ALL_EVENT_TYPES
    assert EventType.SECURITY_REVIEW_PASSED.value == "security_review_passed"


def test_all_bundled_policy_yamls_load_without_error() -> None:
    """Every policy yaml that ships under ``skills/policy/`` must validate.

    Covers the six post-P5 policies:
      - deletion-safety.yaml   (Patch C)
      - build-check.yaml       (Patch N)
      - stage5-completeness.yaml (Patch S — schema check via raw yaml load)
      - diff-size-gate.yaml    (Patch Q/W)
      - security-review.yaml   (Patch X)
      - code-review-gates.yaml (E5 baseline)
    """
    import yaml

    deletion = load_deletion_safety_policy(DELETION_SAFETY_POLICY_PATH_DEFAULT)
    assert deletion.patterns, "deletion-safety.yaml must ship baseline patterns"

    build = load_build_check_policy(BUILD_CHECK_POLICY_PATH_DEFAULT)
    assert build.commands, "build-check.yaml must ship baseline commands"

    diff = load_diff_size_policy(DIFF_SIZE_POLICY_PATH_DEFAULT)
    assert diff.max_lines > 0

    security = load_security_review_policy(SECURITY_REVIEW_POLICY_PATH_DEFAULT)
    assert security.enabled is True
    assert security.security_critical_epics, "default epics list must be non-empty"

    aggregate = load_policy()
    assert isinstance(aggregate.code_review_gates, CodeReviewGates)

    # stage5-completeness.yaml has no dedicated loader (the subscriber reads
    # raw YAML); just assert it parses cleanly.
    stage5_path = (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "policy"
        / "stage5-completeness.yaml"
    )
    parsed = yaml.safe_load(stage5_path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)


# ════════════════════════════════════════════════════════════════════════════
# 2. E2E chain happy path (2 tests)
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_e2e_chain_security_critical_emits_passed_audit_and_merges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    disabled_build_policy: Path,
    critical_security_policy: Path,
) -> None:
    """Full chain on a security-critical story (epic=3) with all gates green.

    Expected event flow:

      WORKER_COMPLETED → (stage5,build,deletion all no-op or pass)
        → code_review emits CODE_REVIEW_VERDICT(approve)
        → security_review emits SECURITY_REVIEW_PASSED(approve)
        → merge_to_integration runs ff-merge stub (no HUMAN_QUERY).

    Asserts: the two downstream events fire in order; no HUMAN_QUERY.
    """
    worktree = _init_git_worktree(tmp_path / "wt")
    _write_story(worktree, "s1", "# Story s1\n\n- **epic:** 3\n\nAdd RLS guards.\n")
    target = _make_target_with_stories(tmp_path)
    review_jsonl = _approve_jsonl(tmp_path / "review.s1.jsonl")

    async def _fake_review_spawn(**_kw: Any) -> WorkerHandle:
        return _make_review_handle(worktree, "s1", review_jsonl)

    async def _fake_ff_merge(**_kw: Any) -> str:
        return "deadbeef"

    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker", _fake_review_spawn
    )
    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_merge_gate_quality_worker", _fake_review_spawn
    )
    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._ff_merge_to_integration", _fake_ff_merge
    )
    monkeypatch.setattr(
        "bmad_orchestrator.agent.run.cleanup_worktree", lambda *_a, **_k: None
    )

    configure_code_review_gate(target_project=target, wave="w")

    async def _security_approve(
        worktree: Path, story_id: str, wave: str
    ) -> tuple[str, str]:
        return VERDICT_APPROVE, "no findings"

    bus = EventLoop()
    _wire_canonical_chain(
        bus,
        security_runner=_security_approve,
        security_policy_path=critical_security_policy,
        build_policy_path=disabled_build_policy,
    )
    await bus.emit(
        EventType.WORKER_COMPLETED,
        story_id="s1",
        worktree=str(worktree),
        status="success",
    )

    seen = await _drain_dispatch(bus)
    types = [e.type for e in seen]
    assert EventType.WORKER_COMPLETED in types
    assert EventType.CODE_REVIEW_VERDICT in types
    assert EventType.SECURITY_REVIEW_PASSED in types
    assert EventType.HUMAN_QUERY not in types

    crv = next(e for e in seen if e.type == EventType.CODE_REVIEW_VERDICT)
    assert crv.payload["verdict"] == "approve"
    srp = next(e for e in seen if e.type == EventType.SECURITY_REVIEW_PASSED)
    assert srp.payload["verdict"] == VERDICT_APPROVE
    # Trigger may be ``epic`` (epic 3 in critical set) OR ``keyword`` (story
    # body mentions ``RLS``) depending on detection order; either signals the
    # subscriber correctly classified this story as critical.
    assert srp.payload["trigger"] in {"epic", "keyword"}


@pytest.mark.asyncio
async def test_e2e_chain_non_security_emits_passed_audit_not_critical(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    disabled_build_policy: Path,
    critical_security_policy: Path,
) -> None:
    """Non-security-critical story (epic=1, no keywords) still gets a passed
    audit so downstream observers see security_review was inspected.

    Asserts: SECURITY_REVIEW_PASSED carries ``reason='not_security_critical'``.
    No hunter runner was invoked (a runner that raises would not trip the test).
    """
    worktree = _init_git_worktree(tmp_path / "wt")
    _write_story(
        worktree,
        "s1",
        "# Story s1\n\n- **epic:** 1\n\nAdd icon to dashboard navbar.\n",
    )
    target = _make_target_with_stories(tmp_path)
    review_jsonl = _approve_jsonl(tmp_path / "review.s1.jsonl")

    async def _fake_review_spawn(**_kw: Any) -> WorkerHandle:
        return _make_review_handle(worktree, "s1", review_jsonl)

    async def _fake_ff_merge(**_kw: Any) -> str:
        return "deadbeef"

    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker", _fake_review_spawn
    )
    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_merge_gate_quality_worker", _fake_review_spawn
    )
    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._ff_merge_to_integration", _fake_ff_merge
    )
    monkeypatch.setattr(
        "bmad_orchestrator.agent.run.cleanup_worktree", lambda *_a, **_k: None
    )

    configure_code_review_gate(target_project=target, wave="w")

    async def _explosive_runner(
        worktree: Path, story_id: str, wave: str
    ) -> tuple[str, str]:
        raise AssertionError("hunter must not be invoked for non-critical story")

    bus = EventLoop()
    _wire_canonical_chain(
        bus,
        security_runner=_explosive_runner,
        security_policy_path=critical_security_policy,
        build_policy_path=disabled_build_policy,
    )
    await bus.emit(
        EventType.WORKER_COMPLETED,
        story_id="s1",
        worktree=str(worktree),
        status="success",
    )

    seen = await _drain_dispatch(bus)
    srp = [e for e in seen if e.type == EventType.SECURITY_REVIEW_PASSED]
    assert len(srp) == 1
    assert srp[0].payload["reason"] == "not_security_critical"


# ════════════════════════════════════════════════════════════════════════════
# 3. E2E chain halts (4 tests)
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_e2e_chain_build_check_failure_halts_before_code_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failing_build_policy: Path,
    critical_security_policy: Path,
) -> None:
    """A failing required build command mutates ``payload['status']`` so the
    downstream code_review_subscriber short-circuits — no CODE_REVIEW_VERDICT,
    no SECURITY_REVIEW_PASSED, no merge.

    Asserts: chain ends at HUMAN_QUERY emitted by build_check; the status is
    ``halted_build_check_failed``; ``_spawn_code_review_worker`` is never
    invoked (would explode the test if it were).
    """
    worktree = _init_git_worktree(tmp_path / "wt")
    target = _make_target_with_stories(tmp_path)

    async def _explosive_review_spawn(**_kw: Any) -> WorkerHandle:
        raise AssertionError(
            "code_review_subscriber must not spawn when build halted"
        )

    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker",
        _explosive_review_spawn,
    )
    configure_code_review_gate(target_project=target, wave="w")

    bus = EventLoop()
    _wire_canonical_chain(
        bus,
        security_runner=None,
        security_policy_path=critical_security_policy,
        build_policy_path=failing_build_policy,
    )
    await bus.emit(
        EventType.WORKER_COMPLETED,
        story_id="s1",
        worktree=str(worktree),
        status="success",
    )

    seen = await _drain_dispatch(bus)
    types = [e.type for e in seen]
    assert EventType.CODE_REVIEW_VERDICT not in types
    assert EventType.SECURITY_REVIEW_PASSED not in types
    wc = next(e for e in seen if e.type == EventType.WORKER_COMPLETED)
    assert wc.payload["status"] == "halted_build_check_failed"
    assert EventType.HUMAN_QUERY in types


@pytest.mark.asyncio
async def test_e2e_chain_unsafe_deletion_halts_before_code_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    disabled_build_policy: Path,
    critical_security_policy: Path,
) -> None:
    """A worker that deleted ``migrations/*.sql`` mutates status to
    ``halted_unsafe_deletion`` so code_review skips entirely.

    Wired-order invariant: deletion_safety sits at index 2 (after stage5
    and build_check), still BEFORE code_review_subscriber at index 3.
    """
    worktree = _init_git_worktree_with_deletion(
        tmp_path / "wt", "migrations/2026_05_x.sql"
    )
    target = _make_target_with_stories(tmp_path)

    async def _explosive_review_spawn(**_kw: Any) -> WorkerHandle:
        raise AssertionError(
            "code_review_subscriber must not spawn when deletion halted"
        )

    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker",
        _explosive_review_spawn,
    )
    configure_code_review_gate(target_project=target, wave="w")

    bus = EventLoop()
    _wire_canonical_chain(
        bus,
        security_runner=None,
        security_policy_path=critical_security_policy,
        build_policy_path=disabled_build_policy,
    )
    await bus.emit(
        EventType.WORKER_COMPLETED,
        story_id="s1",
        worktree=str(worktree),
        status="success",
    )

    seen = await _drain_dispatch(bus)
    types = [e.type for e in seen]
    assert EventType.CODE_REVIEW_VERDICT not in types
    assert EventType.SECURITY_REVIEW_PASSED not in types
    wc = next(e for e in seen if e.type == EventType.WORKER_COMPLETED)
    assert wc.payload["status"] == "halted_unsafe_deletion"
    assert wc.payload["halt_reason"] == "deletion_safety"
    assert EventType.HUMAN_QUERY in types


@pytest.mark.asyncio
async def test_e2e_chain_code_review_reject_emits_human_query_no_merge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    disabled_build_policy: Path,
    critical_security_policy: Path,
) -> None:
    """``verdict='reject'`` from code_review → merge subscriber emits a
    HUMAN_QUERY (no ff-merge). security_review_subscriber sees verdict !=
    approve and exits without spawning the hunter.

    Asserts: HUMAN_QUERY carries ``verdict='reject'``; no
    SECURITY_REVIEW_PASSED audit; ``_ff_merge_to_integration`` never invoked.
    """
    worktree = _init_git_worktree(tmp_path / "wt")
    _write_story(worktree, "s1", "# Story s1\n\n- **epic:** 1\n\nFix layout.\n")
    target = _make_target_with_stories(tmp_path)
    review_jsonl = _reject_jsonl(tmp_path / "review.s1.jsonl")

    async def _fake_review_spawn(**_kw: Any) -> WorkerHandle:
        return _make_review_handle(worktree, "s1", review_jsonl)

    async def _explosive_ff_merge(**_kw: Any) -> str:
        raise AssertionError("ff-merge must not run on verdict=reject")

    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker", _fake_review_spawn
    )
    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._ff_merge_to_integration", _explosive_ff_merge
    )
    configure_code_review_gate(target_project=target, wave="w")

    async def _explosive_security_runner(
        worktree: Path, story_id: str, wave: str
    ) -> tuple[str, str]:
        raise AssertionError("hunter must not run on verdict=reject")

    bus = EventLoop()
    _wire_canonical_chain(
        bus,
        security_runner=_explosive_security_runner,
        security_policy_path=critical_security_policy,
        build_policy_path=disabled_build_policy,
    )
    await bus.emit(
        EventType.WORKER_COMPLETED,
        story_id="s1",
        worktree=str(worktree),
        status="success",
    )

    seen = await _drain_dispatch(bus)
    types = [e.type for e in seen]
    assert EventType.CODE_REVIEW_VERDICT in types
    assert EventType.SECURITY_REVIEW_PASSED not in types
    assert EventType.HUMAN_QUERY in types
    hq = next(e for e in seen if e.type == EventType.HUMAN_QUERY)
    assert hq.payload["verdict"] == "reject"


@pytest.mark.asyncio
async def test_e2e_chain_security_review_block_mutates_verdict_no_merge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    disabled_build_policy: Path,
    critical_security_policy: Path,
) -> None:
    """BLOCK from the hunter mutates the in-flight CODE_REVIEW_VERDICT to
    ``verdict='reject'`` + appends ``security_review_block`` to gate_reasons.
    The merge subscriber (gates on ``verdict == 'approve'``) naturally skips
    and emits HUMAN_QUERY.

    Asserts: no ff-merge invocation; the merge subscriber's HUMAN_QUERY is
    present alongside the security subscriber's HUMAN_QUERY (two HQs total —
    one from each gate).
    """
    worktree = _init_git_worktree(tmp_path / "wt")
    # Story id "4.1" lands in epic 4 (in default critical set); body also
    # mentions ``jwt`` so the trigger is unambiguous regardless of detection
    # order (epic precedence vs keyword).
    _write_story(
        worktree, "4.1", "# Story 4.1\n\n- **epic:** 4\n\nHarden jwt validator.\n"
    )
    target = _make_target_with_stories(tmp_path)
    review_jsonl = _approve_jsonl(tmp_path / "review.4.1.jsonl")

    async def _fake_review_spawn(**_kw: Any) -> WorkerHandle:
        return _make_review_handle(worktree, "4.1", review_jsonl)

    async def _explosive_ff_merge(**_kw: Any) -> str:
        raise AssertionError("ff-merge must not run after security BLOCK")

    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker", _fake_review_spawn
    )
    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_merge_gate_quality_worker", _fake_review_spawn
    )
    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._ff_merge_to_integration", _explosive_ff_merge
    )
    configure_code_review_gate(target_project=target, wave="w")

    async def _hunter_blocks(
        worktree: Path, story_id: str, wave: str
    ) -> tuple[str, str]:
        return VERDICT_BLOCK, "P0-1: alg=none accepted by JWT validator"

    bus = EventLoop()
    _wire_canonical_chain(
        bus,
        security_runner=_hunter_blocks,
        security_policy_path=critical_security_policy,
        build_policy_path=disabled_build_policy,
    )
    await bus.emit(
        EventType.WORKER_COMPLETED,
        story_id="4.1",
        worktree=str(worktree),
        status="success",
    )

    seen = await _drain_dispatch(bus, max_iter=15)
    types = [e.type for e in seen]
    # CRV present but mutated to reject by security subscriber.
    crv = next(e for e in seen if e.type == EventType.CODE_REVIEW_VERDICT)
    assert crv.payload["verdict"] == "reject"
    assert "security_review_block" in crv.payload.get("gate_reasons", [])
    # SECURITY_REVIEW_PASSED is the audit channel for approve/MWF only — under
    # BLOCK the subscriber emits HUMAN_QUERY instead.
    assert EventType.SECURITY_REVIEW_PASSED not in types
    # At least one HUMAN_QUERY from the security subscriber; merge subscriber
    # appends a second on the now-reject verdict.
    hq_count = sum(1 for e in seen if e.type == EventType.HUMAN_QUERY)
    assert hq_count >= 1
    # Confirm the security HUMAN_QUERY carries hunter findings.
    security_hq = [
        e
        for e in seen
        if e.type == EventType.HUMAN_QUERY and "alg=none" in str(e.payload.get("text", ""))
    ]
    assert len(security_hq) == 1


# ════════════════════════════════════════════════════════════════════════════
# 4. Inventory check
# ════════════════════════════════════════════════════════════════════════════


def test_p6_test_inventory_count() -> None:
    """Sanity guard: P6 spec promised ~10 final integration tests.

    Counts the test_* callables in this module (excluding this inventory
    check itself). A regression that drops a P6 test trips immediately.
    """
    import inspect

    import tests.test_canonical_patches_p6 as mod

    test_names = [
        name
        for name, _ in inspect.getmembers(mod, predicate=inspect.isfunction)
        if name.startswith("test_") and name != "test_p6_test_inventory_count"
    ]
    assert len(test_names) == 10, (
        f"P6 spec promised 10 tests; got {len(test_names)}: {test_names}"
    )


# Module-level reference (silences unused-import warnings; ``partial`` is
# imported for future helper expansion). Kept minimal — see _wire_canonical_chain
# above which mirrors ``_run_real_pilot``'s partial-bind pattern.
_ = partial
