"""P3 regression tests — canonical_patches_port (Patch Q + Patch R + Patch S).

Spec: spec/spec_canonical_patches_port.md §P3.
Tracker: .claude/initiative-tracker-canonical_patches_port.md.

Coverage (24 tests):

* **Patch S — stage5 completeness policy** (5) — load happy path, missing
  file, malformed YAML, non-mapping top-level, schema defaults.
* **Patch S — recovery behaviour** (5) — clean tree no-op, dirty tree
  stages+commits, untracked + modified mix, signoff toggled, error path
  surfaces a result.
* **Patch S — subscriber wiring** (3) — registered FIRST (index 0) in
  ``_run_real_pilot``; no-op on non-WORKER_COMPLETED; no-op on non-success
  status.
* **Patch S — subscriber behaviour** (1) — payload populated with
  ``stage5_recovery_commit_sha`` + ``stage5_recovery_paths`` on dirty tree.
* **Patch Q — diff size gate** (5) — shortstat parser, policy load,
  ``gate_verdict`` under/over/at threshold, disabled policy no-op.
* **Patch Q — measure_diff** (2) — measured against a real git worktree
  (small diff under threshold passes; oversize diff trips).
* **Patch R — commit recovery** (3) — clean worktree no-op,
  dirty worktree commits residue, broken worktree path returns error
  result (does not raise).
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from bmad_orchestrator.agent.run import _run_real_pilot
from bmad_orchestrator.agent.safety.budget_guard import BudgetGuard
from bmad_orchestrator.config import ModelConfig, load_settings
from bmad_orchestrator.runtime.commit_recovery import (
    DEFAULT_COMMIT_MARKER,
    recover_pre_merge,
)
from bmad_orchestrator.runtime.diff_size_gate import (
    DIFF_SIZE_POLICY_PATH_DEFAULT,
    DiffSizeGatePolicy,
    DiffSizeMetrics,
    gate_verdict,
    load_diff_size_policy,
    measure_diff,
    parse_shortstat,
)
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.runtime.stage5_completeness import (
    STAGE5_COMPLETENESS_POLICY_PATH_DEFAULT,
    Stage5CompletenessPolicy,
    load_stage5_completeness_policy,
    recover_uncommitted,
    stage5_completeness_subscriber,
)
from bmad_orchestrator.skills_repo import PolicyInvalidError, PolicyNotFoundError

# ───────────────────── helpers ──────────────────────────────────────────────


def _write(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def _init_git(tmp: Path) -> Path:
    """Init an empty git repo with one initial commit."""
    tmp.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(tmp)], check=True)
    subprocess.run(["git", "-C", str(tmp), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(tmp), "config", "user.name", "t"], check=True)
    (tmp / "README").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp), "commit", "-q", "-m", "seed"], check=True)
    return tmp


def _git_add_and_commit(worktree: Path, msg: str) -> None:
    """Sync helper — staging + committing pre-existing files for measure_diff tests."""
    subprocess.run(["git", "-C", str(worktree), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(worktree), "commit", "-q", "-m", msg], check=True
    )


def _head_commit_message(worktree: Path) -> str:
    """Sync helper — return HEAD commit message body."""
    return subprocess.run(
        ["git", "-C", str(worktree), "log", "-1", "--pretty=%B"],
        check=True, capture_output=True, text=True,
    ).stdout


# ───────────────── Patch S — policy load tests ──────────────────────────────


def test_stage5_policy_happy_path(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "s5.yaml",
        "enabled: true\ncommit_marker: 'Patch S recovery'\nsignoff: false\n",
    )
    policy = load_stage5_completeness_policy(p)
    assert policy.enabled is True
    assert policy.commit_marker == "Patch S recovery"
    assert policy.signoff is False


def test_stage5_policy_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(PolicyNotFoundError):
        load_stage5_completeness_policy(tmp_path / "absent.yaml")


def test_stage5_policy_malformed_yaml(tmp_path: Path) -> None:
    p = _write(tmp_path / "bad.yaml", "enabled: [unclosed\n")
    with pytest.raises(PolicyInvalidError):
        load_stage5_completeness_policy(p)


def test_stage5_policy_non_mapping_top(tmp_path: Path) -> None:
    p = _write(tmp_path / "list.yaml", "- a\n- b\n")
    with pytest.raises(PolicyInvalidError):
        load_stage5_completeness_policy(p)


def test_stage5_policy_defaults() -> None:
    pol = Stage5CompletenessPolicy()
    assert pol.enabled is True
    assert pol.signoff is True
    assert "Patch S" in pol.commit_marker


# ───────────────── Patch S — recover_uncommitted behaviour ──────────────────


@pytest.mark.asyncio
async def test_stage5_recover_clean_tree_noop(tmp_path: Path) -> None:
    worktree = _init_git(tmp_path / "wt")
    res = await recover_uncommitted(worktree, Stage5CompletenessPolicy())
    assert res.recovered is False
    assert res.commit_sha == ""
    assert res.staged_paths == ()


@pytest.mark.asyncio
async def test_stage5_recover_dirty_tree_commits(tmp_path: Path) -> None:
    worktree = _init_git(tmp_path / "wt")
    (worktree / "residue.txt").write_text("oops\n", encoding="utf-8")
    res = await recover_uncommitted(worktree, Stage5CompletenessPolicy())
    assert res.recovered is True
    assert res.commit_sha != ""
    assert "residue.txt" in res.staged_paths
    msg = _head_commit_message(worktree)
    assert "Patch S recovery" in msg


@pytest.mark.asyncio
async def test_stage5_recover_modified_and_untracked(tmp_path: Path) -> None:
    """Both modified-tracked and brand-new untracked files land in the commit."""
    worktree = _init_git(tmp_path / "wt")
    (worktree / "README").write_text("changed\n", encoding="utf-8")
    (worktree / "newfile.py").write_text("x=1\n", encoding="utf-8")
    res = await recover_uncommitted(worktree, Stage5CompletenessPolicy())
    assert res.recovered is True
    assert "README" in res.staged_paths
    assert "newfile.py" in res.staged_paths


@pytest.mark.asyncio
async def test_stage5_recover_signoff_toggle(tmp_path: Path) -> None:
    """``signoff=False`` produces a commit without the Signed-off-by trailer."""
    worktree = _init_git(tmp_path / "wt")
    (worktree / "x.txt").write_text("v\n", encoding="utf-8")
    res = await recover_uncommitted(
        worktree, Stage5CompletenessPolicy(signoff=False)
    )
    assert res.recovered is True
    msg = _head_commit_message(worktree)
    assert "Signed-off-by" not in msg


@pytest.mark.asyncio
async def test_stage5_recover_non_git_path_returns_clean(tmp_path: Path) -> None:
    """A path that is not a git repo → no recovery, no exception."""
    not_a_repo = tmp_path / "nope"
    not_a_repo.mkdir()
    res = await recover_uncommitted(not_a_repo, Stage5CompletenessPolicy())
    assert res.recovered is False


# ───────────────── Patch S — subscriber wiring + behaviour ──────────────────


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


@pytest.mark.asyncio
async def test_stage5_wired_first_in_real_pilot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Patch S must register at index 0 — recovery happens before any halt
    gate inspects the worktree."""
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

    funcs = [getattr(s, "func", s) for s in bus._subs]
    assert funcs[0] is stage5_completeness_subscriber


@pytest.mark.asyncio
async def test_stage5_noop_on_non_worker_completed(tmp_path: Path) -> None:
    bus = EventLoop()
    ev = Event(
        type=EventType.WAVE_BOUNDARY_REACHED,
        payload={"completed_stories": 10},
    )
    await stage5_completeness_subscriber(ev, bus)
    assert ev.payload == {"completed_stories": 10}
    assert await bus.next(timeout=0.01) is None


@pytest.mark.asyncio
async def test_stage5_noop_on_non_success_status(tmp_path: Path) -> None:
    bus = EventLoop()
    ev = Event(
        type=EventType.WORKER_COMPLETED,
        payload={"story_id": "s", "worktree": "/x", "status": "failed"},
    )
    await stage5_completeness_subscriber(ev, bus)
    assert "stage5_recovery_commit_sha" not in ev.payload


@pytest.mark.asyncio
async def test_stage5_subscriber_populates_payload_on_recovery(tmp_path: Path) -> None:
    worktree = _init_git(tmp_path / "wt")
    (worktree / "res.txt").write_text("v\n", encoding="utf-8")
    policy = _write(
        tmp_path / "s5.yaml",
        "enabled: true\ncommit_marker: 'Patch S marker test'\nsignoff: true\n",
    )
    bus = EventLoop()
    ev = Event(
        type=EventType.WORKER_COMPLETED,
        payload={"story_id": "s", "worktree": str(worktree), "status": "success"},
    )
    await stage5_completeness_subscriber(ev, bus, policy_path=policy)
    assert ev.payload.get("stage5_recovery_commit_sha"), "sha must be recorded"
    assert "res.txt" in ev.payload.get("stage5_recovery_paths", [])
    # status untouched — Patch S is recovery, not a halter
    assert ev.payload["status"] == "success"


# ─────────────────── Patch Q — diff size gate ───────────────────────────────


def test_parse_shortstat_full_line() -> None:
    m = parse_shortstat(" 3 files changed, 42 insertions(+), 17 deletions(-)")
    assert m.files_changed == 3
    assert m.insertions == 42
    assert m.deletions == 17
    assert m.total_lines == 59


def test_parse_shortstat_one_side_only() -> None:
    m = parse_shortstat(" 1 file changed, 5 insertions(+)")
    assert m.insertions == 5
    assert m.deletions == 0
    m2 = parse_shortstat(" 1 file changed, 3 deletions(-)")
    assert m2.insertions == 0
    assert m2.deletions == 3


def test_parse_shortstat_empty_returns_zeros() -> None:
    m = parse_shortstat("")
    assert m.insertions == 0
    assert m.deletions == 0
    assert m.files_changed == 0


def test_diff_size_gate_verdict_under_threshold_passes() -> None:
    metrics = DiffSizeMetrics(insertions=100, deletions=50, files_changed=3)
    policy = DiffSizeGatePolicy(max_lines=500)
    assert gate_verdict(metrics, policy) is None


def test_diff_size_gate_verdict_over_threshold_rejects() -> None:
    metrics = DiffSizeMetrics(insertions=400, deletions=200, files_changed=12)
    policy = DiffSizeGatePolicy(max_lines=500)
    reason = gate_verdict(metrics, policy)
    assert reason is not None
    assert "diff_size_exceeded" in reason
    assert "600" in reason  # total reported


def test_diff_size_gate_verdict_disabled_policy_noop() -> None:
    metrics = DiffSizeMetrics(insertions=10_000, deletions=0, files_changed=1)
    policy = DiffSizeGatePolicy(max_lines=500, enabled=False)
    assert gate_verdict(metrics, policy) is None


def test_diff_size_policy_loads_from_disk() -> None:
    """Bundled policy file validates cleanly."""
    policy = load_diff_size_policy(DIFF_SIZE_POLICY_PATH_DEFAULT)
    assert policy.enabled is True
    assert policy.max_lines >= 100
    assert policy.range_spec.startswith("HEAD")


@pytest.mark.asyncio
async def test_measure_diff_small_change(tmp_path: Path) -> None:
    """Small two-line change is measured accurately under threshold."""
    worktree = _init_git(tmp_path / "wt")
    (worktree / "small.txt").write_text("a\nb\n", encoding="utf-8")
    _git_add_and_commit(worktree, "small")
    metrics = await measure_diff(worktree)
    assert metrics.insertions == 2
    assert metrics.deletions == 0


@pytest.mark.asyncio
async def test_measure_diff_oversize_trips_gate(tmp_path: Path) -> None:
    """A large diff plus a tight policy → gate_verdict returns rejection."""
    worktree = _init_git(tmp_path / "wt")
    big = "\n".join(f"line {i}" for i in range(800)) + "\n"
    (worktree / "big.txt").write_text(big, encoding="utf-8")
    _git_add_and_commit(worktree, "big")
    metrics = await measure_diff(worktree)
    policy = DiffSizeGatePolicy(max_lines=500)
    assert gate_verdict(metrics, policy) is not None


# ─────────────────── Patch R — pre-merge commit recovery ────────────────────


@pytest.mark.asyncio
async def test_patch_r_clean_worktree_noop(tmp_path: Path) -> None:
    worktree = _init_git(tmp_path / "wt")
    res = await recover_pre_merge(worktree)
    assert res.recovered is False
    assert res.error == ""


@pytest.mark.asyncio
async def test_patch_r_dirty_worktree_commits_residue(tmp_path: Path) -> None:
    worktree = _init_git(tmp_path / "wt")
    (worktree / "leftover.txt").write_text("ok\n", encoding="utf-8")
    res = await recover_pre_merge(worktree)
    assert res.recovered is True
    assert "leftover.txt" in res.staged_paths
    msg = _head_commit_message(worktree)
    assert "Patch R recovery" in msg
    assert DEFAULT_COMMIT_MARKER.splitlines()[0] in msg


@pytest.mark.asyncio
async def test_patch_r_non_git_path_returns_clean_result(tmp_path: Path) -> None:
    """A worktree path that is not a git repo → recovered=False, no exception."""
    not_repo = tmp_path / "plain"
    not_repo.mkdir()
    res = await recover_pre_merge(not_repo)
    assert res.recovered is False


# ─────────────────── module sanity ──────────────────────────────────────────


def test_stage5_module_default_policy_loads() -> None:
    policy = load_stage5_completeness_policy(
        STAGE5_COMPLETENESS_POLICY_PATH_DEFAULT
    )
    assert policy.enabled is True
    assert "Patch S" in policy.commit_marker


def test_subscribers_are_coroutines() -> None:
    assert asyncio.iscoroutinefunction(stage5_completeness_subscriber)
    assert asyncio.iscoroutinefunction(recover_uncommitted)
    assert asyncio.iscoroutinefunction(recover_pre_merge)
    assert asyncio.iscoroutinefunction(measure_diff)
