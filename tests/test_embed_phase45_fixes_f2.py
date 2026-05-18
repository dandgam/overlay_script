"""F2 regression tests — critical P1 fixes for embed_phase45_with_selflearning.

Spec: spec/spec_embed_phase45_fixes.md §F2.

Coverage (18 tests):

* **P1-1** — DoS caps on lesson markdown ingestion
  (``_MAX_LINE_LEN`` + ``_MAX_FILE_BYTES``).
* **P1-2** — policy YAML backup + ``rollback_policy`` + new CLI command
  ``bmad-orchestrator policy-rollback``.
* **P1-3** — bounds guard formula now anchors on ``abs(current)`` so a
  50 % drift from current value is rejected even when the proposed value
  is larger.
* **P1-5** — ``_persist_project_memory_snapshot`` writes a fresh
  ``memory.yaml`` after each wave_boundary so the next pilot boots primed.
* **P1-7** — tightening (proposed > current) is escalated through
  HUMAN_QUERY regardless of bounds; loosening keeps the silent-apply path.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
import yaml

from bmad_orchestrator.agent.run import (
    CodeReviewGateConfig,
    ReviewMetrics,
    _apply_live_tuning,
    _persist_project_memory_snapshot,
    _run_real_pilot,
)
from bmad_orchestrator.agent.safety.budget_guard import BudgetGuard
from bmad_orchestrator.config import ModelConfig, load_settings
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.runtime.lesson_parser import (
    _MAX_FILE_BYTES,
    _MAX_LINE_LEN,
    LessonProposal,
    LessonProposalInvalidError,
    PolicyApplyError,
    _policy_backup_path,
    apply_proposal,
    parse_lesson_markdown,
    parse_lessons_dir,
    rollback_policy,
)
from bmad_orchestrator.runtime.live_tuning import (
    _within_bounds,
    atomic_write_gates_yaml,
)
from bmad_orchestrator.runtime.project_memory import (
    load_project_memory,
    memory_path,
)
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
    return target


def _seed_policy_yaml(skills_root: Path, *, p0_threshold: float = 0.8) -> Path:
    policy_dir = skills_root / "policy"
    policy_dir.mkdir(parents=True, exist_ok=True)
    path = policy_dir / "code-review-gates.yaml"
    payload = {
        "p0_threshold": p0_threshold,
        "test_coverage_threshold": 0.5,
        "compliance_tags": [],
        "sweep_every_stories": 50,
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _proposal(after: float = 0.85) -> LessonProposal:
    return LessonProposal(
        policy_file="code-review-gates",
        field="p0_threshold",
        before=0.8,
        after=after,
        rationale="F2 regression",
        source_file="tests/test_embed_phase45_fixes_f2.py",
    )


# ════════════════════════════════════════════════════════════════════════════
# P1-1 — lesson_parser DoS caps (3 tests)
# ════════════════════════════════════════════════════════════════════════════


def test_p1_1_parse_lesson_markdown_rejects_oversize_line(
    tmp_path: Path,
) -> None:
    """A single pathologically long line trips ``_MAX_LINE_LEN``."""
    fat_line = "x" * (_MAX_LINE_LEN + 1)
    src = tmp_path / "fat.md"
    src.write_text(fat_line + "\n", encoding="utf-8")
    with pytest.raises(LessonProposalInvalidError, match=r"line exceeds"):
        parse_lesson_markdown(src.read_text(encoding="utf-8"), source=src)


def test_p1_1_parse_lesson_markdown_rejects_oversize_file(
    tmp_path: Path,
) -> None:
    """Total encoded size > ``_MAX_FILE_BYTES`` rejected before splitlines()."""
    # 1024 lines of 1024 chars each = ~1 MB; +1 line pushes over the cap.
    fat = "\n".join("x" * 1024 for _ in range(_MAX_FILE_BYTES // 1024 + 2))
    src = tmp_path / "fat.md"
    src.write_text(fat, encoding="utf-8")
    with pytest.raises(LessonProposalInvalidError, match=r"exceeds .* bytes"):
        parse_lesson_markdown(src.read_text(encoding="utf-8"), source=src)


def test_p1_1_parse_lessons_dir_rejects_oversize_file_via_stat(
    tmp_path: Path,
) -> None:
    """``parse_lessons_dir`` stat()s each file before read_text — no OOM read."""
    big = tmp_path / "huge.md"
    big.write_bytes(b"y" * (_MAX_FILE_BYTES + 1))
    with pytest.raises(LessonProposalInvalidError, match=r"size .* exceeds"):
        parse_lessons_dir(tmp_path)


# ════════════════════════════════════════════════════════════════════════════
# P1-2 — policy backup + rollback (5 tests)
# ════════════════════════════════════════════════════════════════════════════


def test_p1_2_apply_proposal_writes_backup_snapshot(
    tmp_path: Path,
) -> None:
    """First apply creates a ``.yaml.bak-<ts>`` mirror of the pre-state."""
    policy = _seed_policy_yaml(tmp_path, p0_threshold=0.8)
    pre_contents = policy.read_text(encoding="utf-8")
    applied = apply_proposal(_proposal(after=0.85), skills_root=tmp_path)
    backups = sorted(policy.parent.glob("code-review-gates.yaml.bak-*"))
    assert len(backups) == 1
    # Backup holds the pre-apply contents byte-for-byte.
    assert backups[0].read_text(encoding="utf-8") == pre_contents
    # Live YAML moved to new value.
    new_contents = yaml.safe_load(policy.read_text(encoding="utf-8"))
    assert new_contents["p0_threshold"] == pytest.approx(0.85)
    # Audit entry carries both fields.
    assert applied.audit_entry["backup_path"] == str(backups[0])
    assert applied.audit_entry["proposal_id"]


def test_p1_2_apply_proposal_audit_carries_rollback_pointer(
    tmp_path: Path,
) -> None:
    """``proposal_id`` in the audit entry matches the backup file's ``-<ts>``."""
    _seed_policy_yaml(tmp_path)
    applied = apply_proposal(_proposal(after=0.9), skills_root=tmp_path)
    proposal_id = applied.audit_entry["proposal_id"]
    expected = _policy_backup_path(
        tmp_path / "policy" / "code-review-gates.yaml", proposal_id
    )
    assert expected.exists()


def test_p1_2_apply_proposal_prunes_to_last_three_backups(
    tmp_path: Path,
) -> None:
    """After 4 applies the policy dir should hold only the 3 newest backups."""
    _seed_policy_yaml(tmp_path)
    # Force distinct timestamp suffixes by stubbing the backup_timestamp helper.
    timestamps = iter(
        ["20260517T100000Z", "20260517T100100Z", "20260517T100200Z", "20260517T100300Z"]
    )
    import bmad_orchestrator.runtime.lesson_parser as lp

    original = lp._backup_timestamp
    lp._backup_timestamp = lambda now=None: next(timestamps)  # type: ignore[assignment]
    try:
        for v in (0.81, 0.82, 0.83, 0.84):
            apply_proposal(_proposal(after=v), skills_root=tmp_path)
    finally:
        lp._backup_timestamp = original  # type: ignore[assignment]

    backups = sorted(
        (tmp_path / "policy").glob("code-review-gates.yaml.bak-*"),
        key=lambda p: p.name,
    )
    assert [b.name.rsplit("-", 1)[-1] for b in backups] == [
        "20260517T100100Z",
        "20260517T100200Z",
        "20260517T100300Z",
    ]


def test_p1_2_rollback_policy_restores_from_backup(tmp_path: Path) -> None:
    """``rollback_policy`` writes the backup payload back into the policy YAML."""
    policy = _seed_policy_yaml(tmp_path, p0_threshold=0.8)
    applied = apply_proposal(_proposal(after=0.85), skills_root=tmp_path)
    proposal_id = applied.audit_entry["proposal_id"]
    # Mid-state: live YAML = 0.85.
    assert yaml.safe_load(policy.read_text(encoding="utf-8"))["p0_threshold"] == pytest.approx(0.85)

    restored, backup = rollback_policy(
        skills_root=tmp_path, proposal_id=proposal_id
    )
    assert restored == policy
    assert backup == _policy_backup_path(policy, proposal_id)
    after = yaml.safe_load(policy.read_text(encoding="utf-8"))
    assert after["p0_threshold"] == pytest.approx(0.8)


def test_p1_2_rollback_policy_raises_on_unknown_proposal_id(
    tmp_path: Path,
) -> None:
    """Unknown ``proposal_id`` must surface as ``PolicyApplyError``."""
    _seed_policy_yaml(tmp_path)
    apply_proposal(_proposal(after=0.85), skills_root=tmp_path)
    with pytest.raises(PolicyApplyError, match=r"no backup found"):
        rollback_policy(skills_root=tmp_path, proposal_id="19990101T000000Z")


# ════════════════════════════════════════════════════════════════════════════
# P1-3 — bounds guard anchored on current (2 tests)
# ════════════════════════════════════════════════════════════════════════════


def test_p1_3_bounds_guard_rejects_fifty_percent_jump_when_proposed_larger() -> None:
    """0.5 → 0.8 is a 60 % move; old formula accepted it (scaled to 0.8),
    new formula rejects it (scaled to 0.5)."""
    assert _within_bounds(current=0.5, proposed=0.8, max_movement_fraction=0.5) is False


def test_p1_3_bounds_guard_uses_eps_floor_for_zero_current() -> None:
    """A zero-current threshold still uses ``_EPS`` as denominator floor so
    we never accept «infinite drift»."""
    assert _within_bounds(current=0.0, proposed=0.5, max_movement_fraction=0.5) is False


# ════════════════════════════════════════════════════════════════════════════
# P1-5 — save_project_memory snapshot after wave_boundary (3 tests)
# ════════════════════════════════════════════════════════════════════════════


def test_p1_5_persist_snapshot_writes_memory_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Helper writes a ``memory.yaml`` under ``_config/projects/<slug>/``."""
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
    bg = BudgetGuard(load_settings().budget)
    bg.record_review_metrics(
        p0_found=10, p0_fixed=7, test_files_count=4, expected_n_tests=5
    )
    out = _persist_project_memory_snapshot(
        project="proj_p1_5", wave="w-test",
        budget=bg, orchestrator_home=tmp_path,
    )
    assert out == memory_path("proj_p1_5", orchestrator_home=tmp_path)
    assert out.exists()


def test_p1_5_persist_snapshot_updates_last_wave_and_windows(
    tmp_path: Path,
) -> None:
    """Loaded memory reflects the just-completed wave + rolling deques."""
    bg = BudgetGuard(load_settings().budget)
    bg.record_review_metrics(
        p0_found=10, p0_fixed=8, test_files_count=4, expected_n_tests=5
    )
    bg.record_review_metrics(
        p0_found=10, p0_fixed=6, test_files_count=3, expected_n_tests=5
    )
    _persist_project_memory_snapshot(
        project="proj_p1_5b", wave="w-final",
        budget=bg, orchestrator_home=tmp_path,
    )
    reloaded = load_project_memory("proj_p1_5b", orchestrator_home=tmp_path)
    assert reloaded.last_wave == "w-final"
    assert reloaded.recent_p0_coverages == [0.8, 0.6]
    assert reloaded.recent_test_coverages == [0.8, 0.6]


@pytest.mark.asyncio
async def test_p1_5_real_pilot_persists_snapshot_after_wave_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """``_run_real_pilot`` calls the helper after WAVE_BOUNDARY_REACHED."""
    monkeypatch.delenv("BMAD_REQUIRE_SANDBOX", raising=False)
    target = _make_target_with_stories(tmp_path)  # zero stories → empty DAG
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target))
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_ORCHESTRATOR_HOME", str(home))

    bus = EventLoop()
    budget = BudgetGuard(load_settings().budget, event_loop=bus)
    try:
        await _run_real_pilot(
            bus, project="proj_p1_5c", wave="w",
            max_parallel=1, max_stories=1, max_spend_usd=10.0,
            budget=budget, state_db=None, session_id=None,
            models=ModelConfig(), options={},
        )
    finally:
        await bus.stop()

    persisted = memory_path("proj_p1_5c", orchestrator_home=home)
    assert persisted.exists(), (
        f"expected snapshot at {persisted}; directory contents: "
        f"{list((home / '_config' / 'projects').iterdir()) if (home / '_config' / 'projects').exists() else '<missing>'}"
    )
    raw = yaml.safe_load(persisted.read_text(encoding="utf-8"))
    assert raw["last_wave"] == "w"


# ════════════════════════════════════════════════════════════════════════════
# P1-7 — tighten direction escalation (5 tests)
# ════════════════════════════════════════════════════════════════════════════


def _gates_cfg(tmp_path: Path, *, p0_threshold: float = 0.5) -> tuple[
    CodeReviewGateConfig, BudgetGuard, Path
]:
    gates = CodeReviewGates.model_validate(
        {"p0_threshold": p0_threshold, "test_coverage_threshold": 0.5}
    )
    gates_yaml = tmp_path / "code-review-gates.yaml"
    atomic_write_gates_yaml(gates, gates_yaml)
    bg = BudgetGuard(load_settings().budget)
    cfg = CodeReviewGateConfig(
        target_project=tmp_path,
        wave="w",
        escalation_chat_id=None,
        gates_override=gates,
        budget=bg,
        gates_path=gates_yaml,
    )
    return cfg, bg, gates_yaml


def _drain_events(bus: EventLoop) -> list[Event]:
    out: list[Event] = []
    while True:
        try:
            out.append(bus.queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return out


@pytest.mark.asyncio
async def test_p1_7_tighten_emits_human_query(tmp_path: Path) -> None:
    """Median samples > current → HUMAN_QUERY (no silent write)."""
    cfg, bg, _gates_yaml = _gates_cfg(tmp_path, p0_threshold=0.5)
    # 5 samples each = 0.7 → median 0.7 > current 0.5 (tightening).  Drift is
    # within 50 % bound (0.2 vs 0.5 * 0.5 = 0.25) so it would have been
    # silently applied pre-P1-7.
    for _ in range(5):
        bg.record_review_metrics(
            p0_found=10, p0_fixed=7, test_files_count=0, expected_n_tests=0
        )
    bus = EventLoop()
    await _apply_live_tuning(
        bus=bus, cfg=cfg,
        gates=CodeReviewGates(p0_threshold=0.5),
        metrics=ReviewMetrics(),
        story_id="S-tighten",
    )
    queued = _drain_events(bus)
    human_queries = [e for e in queued if e.type == EventType.HUMAN_QUERY]
    assert len(human_queries) == 1, queued
    assert human_queries[0].payload["verdict"] == "live_tuning_tighten"


@pytest.mark.asyncio
async def test_p1_7_tighten_does_not_overwrite_yaml(tmp_path: Path) -> None:
    """Tighten escalation skips ``atomic_write_gates_yaml`` (YAML untouched)."""
    cfg, bg, gates_yaml = _gates_cfg(tmp_path, p0_threshold=0.5)
    for _ in range(5):
        bg.record_review_metrics(
            p0_found=10, p0_fixed=7, test_files_count=0, expected_n_tests=0
        )
    bus = EventLoop()
    await _apply_live_tuning(
        bus=bus, cfg=cfg,
        gates=CodeReviewGates(p0_threshold=0.5),
        metrics=ReviewMetrics(),
        story_id="S-tighten-yaml",
    )
    persisted = yaml.safe_load(gates_yaml.read_text(encoding="utf-8"))
    assert persisted["p0_threshold"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_p1_7_tighten_payload_has_actions_for_operator(
    tmp_path: Path,
) -> None:
    """Escalation payload must expose the standard approve / keep buttons."""
    cfg, bg, _ = _gates_cfg(tmp_path, p0_threshold=0.5)
    for _ in range(5):
        bg.record_review_metrics(
            p0_found=10, p0_fixed=7, test_files_count=0, expected_n_tests=0
        )
    bus = EventLoop()
    await _apply_live_tuning(
        bus=bus, cfg=cfg,
        gates=CodeReviewGates(p0_threshold=0.5),
        metrics=ReviewMetrics(),
        story_id="S-act",
    )
    queued = _drain_events(bus)
    payload = next(e.payload for e in queued if e.type == EventType.HUMAN_QUERY)
    assert payload["actions"] == ["approve_update", "keep_current"]
    assert payload["proposed_value"] == pytest.approx(0.7)
    assert payload["current_value"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_p1_7_loosen_still_silent_applies(tmp_path: Path) -> None:
    """Proposed < current is still safe (no HUMAN_QUERY, YAML updated)."""
    cfg, bg, gates_yaml = _gates_cfg(tmp_path, p0_threshold=0.8)
    for _ in range(5):
        bg.record_review_metrics(
            p0_found=10, p0_fixed=7, test_files_count=0, expected_n_tests=0
        )
    bus = EventLoop()
    await _apply_live_tuning(
        bus=bus, cfg=cfg,
        gates=CodeReviewGates(p0_threshold=0.8),
        metrics=ReviewMetrics(),
        story_id="S-loose",
    )
    queued = _drain_events(bus)
    assert not [e for e in queued if e.type == EventType.HUMAN_QUERY]
    persisted = yaml.safe_load(gates_yaml.read_text(encoding="utf-8"))
    assert persisted["p0_threshold"] == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_p1_7_mixed_batch_loose_applies_tight_escalates(
    tmp_path: Path,
) -> None:
    """Loosen p0 + tighten test_coverage in one batch → split paths."""
    cfg, bg, gates_yaml = _gates_cfg(tmp_path, p0_threshold=0.8)
    # p0 samples ~0.7  → loosens 0.8 → 0.7 (silent).
    # test_coverage samples ~0.7 → tightens 0.5 → 0.7 (HUMAN_QUERY).
    for _ in range(5):
        bg.record_review_metrics(
            p0_found=10, p0_fixed=7, test_files_count=7, expected_n_tests=10
        )
    bus = EventLoop()
    await _apply_live_tuning(
        bus=bus, cfg=cfg,
        gates=CodeReviewGates(p0_threshold=0.8, test_coverage_threshold=0.5),
        metrics=ReviewMetrics(),
        story_id="S-mixed",
    )
    queued = _drain_events(bus)
    human_queries = [e for e in queued if e.type == EventType.HUMAN_QUERY]
    assert len(human_queries) == 1
    assert human_queries[0].payload["metric"] == "test_coverage_threshold"
    assert human_queries[0].payload["verdict"] == "live_tuning_tighten"
    persisted = yaml.safe_load(gates_yaml.read_text(encoding="utf-8"))
    # p0 loosened to 0.7, test_coverage held at 0.5.
    assert persisted["p0_threshold"] == pytest.approx(0.7)
    assert persisted["test_coverage_threshold"] == pytest.approx(0.5)
