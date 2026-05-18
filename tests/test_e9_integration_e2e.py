"""E9 acceptance — full embed_phase45+self-learning integration + e2e smoke.

Spec: spec/spec_embed_phase45_with_selflearning.md §E9 (FINAL).

Coverage (25 tests):

* Full pipeline e2e (3) — synthetic project → ``apply_embedded_skills`` →
  ``spawn_worker(mock)`` → ``WORKER_COMPLETED`` → ``code_review_subscriber``
  with 4 gates → ``_apply_live_tuning`` → ``save_project_memory`` →
  retrospective lesson markdown → ``parse_lessons_dir`` →
  ``apply_proposals_batch(auto)`` → policy YAML on disk updated.
* Embedded skills → worker (3) — overlay + spawn wiring junction.
* Memory persistence loop (4) — prime / round-trip / multi-project / corrupt.
* Lessons → policy (5) — markdown → proposals → atomic policy write +
  audit invariants.
* CLI policy-apply (3) — auto-apply happy path, no-proposals, malformed
  lesson exits non-zero.
* Docs presence + content (3) — file exists, sections present, smoke
  invocation included.
* Grep DoD (4) — the four cross-module wiring sites are present.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from bmad_orchestrator.agent.run import (
    code_review_subscriber,
    configure_code_review_gate,
)
from bmad_orchestrator.agent.safety.budget_guard import BudgetGuard
from bmad_orchestrator.cli.main import app
from bmad_orchestrator.config import BudgetConfig
from bmad_orchestrator.runtime.embedded_skills import apply_embedded_skills
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.runtime.lesson_parser import (
    LessonProposal,
    apply_proposal,
    apply_proposals_batch,
    parse_lesson_markdown,
    parse_lessons_dir,
    policy_file_path,
    proposals_yaml_path,
    save_proposals_yaml,
)
from bmad_orchestrator.runtime.project_memory import (
    ProjectMemory,
    load_project_memory,
    memory_path,
    save_project_memory,
)
from bmad_orchestrator.runtime.worker_spawn import WorkerHandle, spawn_worker
from bmad_orchestrator.skills_repo import EMBEDDED_SKILL_NAMES, CodeReviewGates

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_FILE = REPO_ROOT / "docs" / "embedded-skills-architecture.md"


# ── helpers ────────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> str:
    cp = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return cp.stdout.strip()


def _init_git_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@test.local")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "commit", "--allow-empty", "-m", "initial")


def _build_skills_overlay(
    skills_root: Path, names: list[str] | None = None
) -> Path:
    """Build minimal ``skills/`` overlay fixture (upstream + customize)."""
    upstream = skills_root / "upstream"
    customize = skills_root / "customize"
    upstream.mkdir(parents=True)
    customize.mkdir(parents=True)
    chosen = names if names is not None else sorted(EMBEDDED_SKILL_NAMES)
    for name in chosen:
        skill_dir = upstream / name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: stub for {name}\n---\n\n"
            f"# {name}\n\nBody.\n",
            encoding="utf-8",
        )
        (customize / f"{name}.customize.toml").write_text("", encoding="utf-8")
    return skills_root


def _write_baseline_policy_yamls(skills_root: Path) -> None:
    policy = skills_root / "policy"
    policy.mkdir(parents=True, exist_ok=True)
    (policy / "code-review-gates.yaml").write_text(
        "p0_threshold: 0.8\n"
        "test_coverage_threshold: 0.5\n"
        "compliance_tags:\n  - 152-ФЗ\n  - 187-ФЗ\n"
        "sweep_every_stories: 50\n",
        encoding="utf-8",
    )
    (policy / "cost-tuning.yaml").write_text(
        "daily_cap_usd: 50.0\nstory_reserve_usd: 0.5\nmax_concurrent_workers: 4\n",
        encoding="utf-8",
    )
    (policy / "retry-policy.yaml").write_text(
        "max_retries: 3\nbackoff_seconds: 30\n"
        "escalation_triggers:\n  - auto_rollback_failed\n",
        encoding="utf-8",
    )


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


def _drain(bus: EventLoop) -> list[Event]:
    out: list[Event] = []
    while True:
        try:
            out.append(bus.queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    # Phase 4 hardening #5 adds MERGE_GATE_STAGE_COMPLETED observability events;
    # filter them so pre-split assertions remain valid.
    return [e for e in out if e.type != EventType.MERGE_GATE_STAGE_COMPLETED]


def _approve_event(
    metrics: dict[str, Any], compliance_tags: tuple[str, ...] = ()
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "event_type": "claude_event",
        "verdict": "approve",
        "summary": "42 tests pass. Coverage 91%. No lint errors.",
        "metrics": {**metrics, "compliance_tags": list(compliance_tags)},
    }
    return payload


_TERMINAL_EVENT = {
    "event_type": "worker_completed",
    "story_id": "s1",
    "exit_code": 0,
    "status": "success",
}


def _read_audit(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.fixture
def reset_gate_config() -> Iterator[None]:
    configure_code_review_gate(target_project=None, wave=None)
    yield
    configure_code_review_gate(target_project=None, wave=None)


@pytest.fixture
def audit_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    target = tmp_path / "audit.jsonl"
    monkeypatch.setenv("BMAD_AUDIT_LOG", str(target))
    return target


# ════════════════════════════════════════════════════════════════════════════
# A. Full pipeline e2e (3 tests)
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_e9_full_pipeline_synthetic_project_to_policy_proposal_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reset_gate_config: None,
    audit_log: Path,
) -> None:
    """Walks every step from synthetic project to applied policy proposal.

    Asserts the chain stays connected: skills land in the worktree, the
    review subscriber pulls metrics from the mock JSONL, live tuning feeds
    BudgetGuard's rolling windows, project memory persists those windows
    across a reload, a retrospective lesson is parsed into a proposal, and
    the proposal lands on disk with an audit row that records before/after.
    """
    # 1. Synthetic orchestrator_home + target project skeleton.
    home = tmp_path / "orchestrator"
    home.mkdir()
    project_root = tmp_path / "target"
    project_root.mkdir()
    wt_root = project_root / ".worktrees"
    wt_root.mkdir()
    wt = wt_root / "wt-s1"
    wt.mkdir()

    skills_root = home / "skills"
    _build_skills_overlay(skills_root)
    _write_baseline_policy_yamls(skills_root)
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(project_root))

    # 2. Overlay embedded skills + spawn mock worker.
    apply_result = apply_embedded_skills(
        worktree=wt,
        skills_resolution_root=skills_root,
        allowed_worktree_root=wt_root,
    )
    assert len(apply_result.skills_applied) == 16
    assert (wt / ".claude" / "skills" / "bmad-code-review" / "SKILL.md").is_file()

    handle = await spawn_worker(
        worktree=str(wt),
        story_id="s1",
        branch="feature/s1",
        mock=True,
        use_sandbox=False,
        embedded_skills_root=skills_root,
        allowed_worktree_root=wt_root,
    )
    assert handle.jsonl_path.exists()

    # 3. Prime BudgetGuard from per-project memory (start empty for slug).
    budget = BudgetGuard(BudgetConfig())
    memory = load_project_memory("odyssey", orchestrator_home=home)
    budget.prime_from_memory(memory)
    assert budget.recent_p0_coverages() == ()

    # 4. Configure code-review gate + supply mock review JSONL.
    gates_yaml = skills_root / "policy" / "code-review-gates.yaml"
    gates = CodeReviewGates.model_validate({"p0_threshold": 0.8})
    review_jsonl = tmp_path / "review.s1.jsonl"
    _write_jsonl(
        review_jsonl,
        [
            _approve_event(
                {
                    "p0_found": 5,
                    "p0_fixed": 5,
                    "test_files_count": 8,
                    "expected_n_tests": 8,
                },
            ),
            _TERMINAL_EVENT,
        ],
    )

    async def fake_spawn(**_kw: Any) -> WorkerHandle:
        return _make_handle(str(wt), "s1", review_jsonl)

    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker", fake_spawn
    )
    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_merge_gate_quality_worker", fake_spawn
    )

    configure_code_review_gate(
        target_project=project_root,
        wave="1a",
        gates_override=gates,
        budget=budget,
        gates_path=gates_yaml,
    )

    # 5. WORKER_COMPLETED → review subscriber chain.
    bus = EventLoop()
    completed = Event(
        type=EventType.WORKER_COMPLETED,
        payload={"story_id": "s1", "worktree": str(wt), "status": "success"},
    )
    await code_review_subscriber(completed, bus)

    emitted = _drain(bus)
    assert [e.type for e in emitted] == [EventType.CODE_REVIEW_VERDICT]
    assert emitted[0].payload["verdict"] == "approve"

    # 6. Live tuning recorded the sample even though MIN_SAMPLES not reached.
    assert budget.recent_p0_coverages() == (1.0,)
    assert budget.recent_test_coverages() == (1.0,)

    # 7. Persist BudgetGuard windows back to per-project memory.
    persisted_mem = ProjectMemory(
        project_slug="odyssey",
        recent_p0_coverages=list(budget.recent_p0_coverages()),
        recent_test_coverages=list(budget.recent_test_coverages()),
        recent_review_iterations=list(budget.recent_review_iterations()),
    )
    save_project_memory(persisted_mem, orchestrator_home=home)
    reloaded = load_project_memory("odyssey", orchestrator_home=home)
    assert reloaded.recent_p0_coverages == [1.0]
    assert reloaded.recent_test_coverages == [1.0]

    # 8. Simulated retrospective lesson → policy proposal markdown.
    lessons_dir = skills_root / "lessons" / "odyssey"
    lessons_dir.mkdir(parents=True)
    (lessons_dir / "wave-1a.md").write_text(
        "# Wave 1a retrospective\n\n"
        "Test coverage was consistently 100% — threshold could rise safely.\n\n"
        "## Policy proposal: code-review-gates.test_coverage_threshold\n"
        "before: 0.5\n"
        "after: 0.7\n"
        "rationale: median coverage holding at 1.0 across the wave\n",
        encoding="utf-8",
    )

    # 9. Parse → save proposals YAML → batch apply (auto).
    proposals = parse_lessons_dir(lessons_dir)
    assert len(proposals) == 1
    out_path = save_proposals_yaml(
        proposals, slug="odyssey", orchestrator_home=home
    )
    assert out_path.exists()

    result = apply_proposals_batch(
        proposals, skills_root=skills_root, auto_apply=True
    )
    assert len(result.applied) == 1
    assert result.errors == []

    # 10. Policy YAML reflects the new value; audit row records before/after.
    persisted_policy = yaml.safe_load(gates_yaml.read_text(encoding="utf-8"))
    assert persisted_policy["test_coverage_threshold"] == 0.7
    audit_rows = _read_audit(audit_log)
    applied_rows = [r for r in audit_rows if r["event_type"] == "policy_proposal_applied"]
    assert len(applied_rows) == 1
    row = applied_rows[0]
    assert row["field"] == "test_coverage_threshold"
    assert row["before"] == 0.5
    assert row["after"] == 0.7


@pytest.mark.asyncio
async def test_e9_full_pipeline_p0_gate_overrides_approve_to_reject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reset_gate_config: None,
    audit_log: Path,
) -> None:
    """When review says approve but P0 coverage 1/10 < 0.8 → flipped to reject."""
    skills_root = tmp_path / "skills"
    _build_skills_overlay(skills_root, names=["bmad-code-review"])
    _write_baseline_policy_yamls(skills_root)

    gates_yaml = skills_root / "policy" / "code-review-gates.yaml"
    gates = CodeReviewGates.model_validate({"p0_threshold": 0.8})

    review_jsonl = tmp_path / "review.jsonl"
    _write_jsonl(
        review_jsonl,
        [
            _approve_event(
                {
                    "p0_found": 10,
                    "p0_fixed": 1,
                    "test_files_count": 8,
                    "expected_n_tests": 8,
                },
            ),
            _TERMINAL_EVENT,
        ],
    )

    async def fake_spawn(**_kw: Any) -> WorkerHandle:
        return _make_handle(str(tmp_path / "wt"), "s2", review_jsonl)

    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker", fake_spawn
    )
    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_merge_gate_quality_worker", fake_spawn
    )
    configure_code_review_gate(
        target_project=tmp_path,
        wave="1a",
        gates_override=gates,
        gates_path=gates_yaml,
    )

    bus = EventLoop()
    await code_review_subscriber(
        Event(
            type=EventType.WORKER_COMPLETED,
            payload={"story_id": "s2", "worktree": str(tmp_path / "wt"), "status": "success"},
        ),
        bus,
    )
    emitted = _drain(bus)
    assert [e.type for e in emitted] == [EventType.CODE_REVIEW_VERDICT]
    payload = emitted[0].payload
    assert payload["verdict"] == "reject"
    assert any("p0" in r.lower() for r in payload["gate_reasons"])


@pytest.mark.asyncio
async def test_e9_full_pipeline_compliance_violation_short_circuits_to_human_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reset_gate_config: None,
    audit_log: Path,
) -> None:
    """Missing required compliance tag escalates HUMAN_QUERY; no verdict emitted."""
    skills_root = tmp_path / "skills"
    _build_skills_overlay(skills_root, names=["bmad-code-review"])
    _write_baseline_policy_yamls(skills_root)

    gates = CodeReviewGates.model_validate(
        {"compliance_tags": ["152-ФЗ", "187-ФЗ"]}
    )

    review_jsonl = tmp_path / "review.jsonl"
    _write_jsonl(
        review_jsonl,
        [
            _approve_event(
                {
                    "p0_found": 5,
                    "p0_fixed": 5,
                    "test_files_count": 5,
                    "expected_n_tests": 5,
                },
                compliance_tags=("152-ФЗ",),  # missing 187-ФЗ
            ),
            _TERMINAL_EVENT,
        ],
    )

    async def fake_spawn(**_kw: Any) -> WorkerHandle:
        return _make_handle(str(tmp_path / "wt"), "s3", review_jsonl)

    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_merge_gate_spec_worker", fake_spawn
    )
    monkeypatch.setattr(
        "bmad_orchestrator.agent.run._spawn_merge_gate_quality_worker", fake_spawn
    )
    configure_code_review_gate(
        target_project=tmp_path,
        wave="1a",
        gates_override=gates,
    )

    bus = EventLoop()
    await code_review_subscriber(
        Event(
            type=EventType.WORKER_COMPLETED,
            payload={"story_id": "s3", "worktree": str(tmp_path / "wt"), "status": "success"},
        ),
        bus,
    )
    emitted = _drain(bus)
    assert [e.type for e in emitted] == [EventType.HUMAN_QUERY]
    assert emitted[0].payload["verdict"] == "compliance_violation"


# ════════════════════════════════════════════════════════════════════════════
# B. Embedded skills → worker spawn (3 tests)
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_e9_chain_embedded_skills_overlay_then_spawn_emits_applied_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(tmp_path))
    skills_root = _build_skills_overlay(
        tmp_path / "skills", names=["bmad-auto-dev", "bmad-code-review"]
    )
    wt_root = tmp_path / ".worktrees"
    wt_root.mkdir()
    wt = wt_root / "wt-1"
    wt.mkdir()

    handle = await spawn_worker(
        worktree=str(wt),
        story_id="s1",
        branch="feature/s1",
        mock=True,
        use_sandbox=False,
        embedded_skills_root=skills_root,
        allowed_worktree_root=wt_root,
    )
    events = [
        json.loads(line)
        for line in handle.jsonl_path.read_text(encoding="utf-8").splitlines()
    ]
    types = [ev["event_type"] for ev in events]
    assert "embedded_skills_applied" in types
    applied = next(ev for ev in events if ev["event_type"] == "embedded_skills_applied")
    assert sorted(applied["skills_applied"]) == ["bmad-auto-dev", "bmad-code-review"]


@pytest.mark.asyncio
async def test_e9_chain_worker_worktree_skills_match_upstream_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(tmp_path))
    skills_root = _build_skills_overlay(tmp_path / "skills")
    wt_root = tmp_path / ".worktrees"
    wt_root.mkdir()
    wt = wt_root / "wt-2"
    wt.mkdir()

    await spawn_worker(
        worktree=str(wt),
        story_id="s2",
        branch="feature/s2",
        mock=True,
        use_sandbox=False,
        embedded_skills_root=skills_root,
        allowed_worktree_root=wt_root,
    )
    on_disk = sorted(p.name for p in (wt / ".claude" / "skills").iterdir())
    assert on_disk == sorted(EMBEDDED_SKILL_NAMES)


@pytest.mark.asyncio
async def test_e9_chain_worker_spawn_without_embedded_root_emits_no_skills_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backward-compat: opt-out path leaves worktree's .claude/skills/ untouched."""
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(tmp_path))
    wt = tmp_path / "wt-3"
    wt.mkdir()
    handle = await spawn_worker(
        worktree=str(wt),
        story_id="s3",
        branch="feature/s3",
        mock=True,
        use_sandbox=False,
    )
    events = [
        json.loads(line)
        for line in handle.jsonl_path.read_text(encoding="utf-8").splitlines()
    ]
    types = [ev["event_type"] for ev in events]
    assert "embedded_skills_applied" not in types
    assert not (wt / ".claude" / "skills").exists()


# ════════════════════════════════════════════════════════════════════════════
# C. Memory persistence loop (4 tests)
# ════════════════════════════════════════════════════════════════════════════


def test_e9_chain_memory_prime_then_review_then_persist_roundtrip(
    tmp_path: Path,
) -> None:
    # Pre-populate memory from a prior wave.
    prior = ProjectMemory(
        project_slug="odyssey",
        recent_p0_coverages=[0.9, 0.85, 0.95],
        recent_test_coverages=[0.6, 0.7, 0.8],
        recent_review_iterations=[1, 1, 2],
    )
    save_project_memory(prior, orchestrator_home=tmp_path)

    bg = BudgetGuard(BudgetConfig())
    bg.prime_from_memory(load_project_memory("odyssey", orchestrator_home=tmp_path))
    assert bg.recent_p0_coverages() == (0.9, 0.85, 0.95)

    # New review pushes one more sample.
    bg.record_review_metrics(
        p0_found=10, p0_fixed=10, test_files_count=5, expected_n_tests=5
    )

    # Persist back, reload, identity preserved.
    updated = ProjectMemory(
        project_slug="odyssey",
        recent_p0_coverages=list(bg.recent_p0_coverages()),
        recent_test_coverages=list(bg.recent_test_coverages()),
        recent_review_iterations=list(bg.recent_review_iterations()),
    )
    save_project_memory(updated, orchestrator_home=tmp_path)
    reread = load_project_memory("odyssey", orchestrator_home=tmp_path)
    assert reread.recent_p0_coverages == [0.9, 0.85, 0.95, 1.0]
    assert reread.recent_test_coverages == [0.6, 0.7, 0.8, 1.0]


def test_e9_chain_memory_multi_project_isolation(tmp_path: Path) -> None:
    save_project_memory(
        ProjectMemory(project_slug="odyssey", recent_p0_coverages=[0.9]),
        orchestrator_home=tmp_path,
    )
    save_project_memory(
        ProjectMemory(project_slug="crm", recent_p0_coverages=[0.3, 0.4]),
        orchestrator_home=tmp_path,
    )
    odyssey = load_project_memory("odyssey", orchestrator_home=tmp_path)
    crm = load_project_memory("crm", orchestrator_home=tmp_path)
    assert odyssey.recent_p0_coverages == [0.9]
    assert crm.recent_p0_coverages == [0.3, 0.4]
    # File paths must be distinct directories.
    assert memory_path("odyssey", orchestrator_home=tmp_path) != memory_path(
        "crm", orchestrator_home=tmp_path
    )


def test_e9_chain_memory_corrupt_yaml_returns_defaults_loud(tmp_path: Path) -> None:
    """Corrupt YAML → invalid-error so caller can decide fallback (not silent default)."""
    from bmad_orchestrator.runtime.project_memory import ProjectMemoryInvalidError

    p = memory_path("odyssey", orchestrator_home=tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(":::not yaml::\n", encoding="utf-8")

    with pytest.raises(ProjectMemoryInvalidError):
        load_project_memory("odyssey", orchestrator_home=tmp_path)


def test_e9_chain_memory_aggregate_median_story_cost_recomputable(
    tmp_path: Path,
) -> None:
    """Aggregate field can be derived from rolling window and round-trips."""
    samples = [Decimal("1.0"), Decimal("2.0"), Decimal("4.0")]
    median_cost = float(sorted(samples)[len(samples) // 2])
    mem = ProjectMemory(
        project_slug="odyssey",
        recent_story_costs=[float(s) for s in samples],
        median_story_cost_usd=median_cost,
    )
    save_project_memory(mem, orchestrator_home=tmp_path)
    reread = load_project_memory("odyssey", orchestrator_home=tmp_path)
    assert reread.median_story_cost_usd == 2.0
    assert reread.recent_story_costs == [1.0, 2.0, 4.0]


# ════════════════════════════════════════════════════════════════════════════
# D. Lessons → policy (5 tests)
# ════════════════════════════════════════════════════════════════════════════


def test_e9_chain_lesson_markdown_to_policy_yaml_auto_apply_full_path(
    tmp_path: Path, audit_log: Path
) -> None:
    skills_root = tmp_path / "skills"
    _build_skills_overlay(skills_root, names=["bmad-code-review"])
    _write_baseline_policy_yamls(skills_root)

    lessons = skills_root / "lessons" / "odyssey"
    lessons.mkdir(parents=True)
    (lessons / "wave-1a.md").write_text(
        "## Policy proposal: code-review-gates.p0_threshold\n"
        "before: 0.8\n"
        "after: 0.85\n"
        "rationale: median coverage rose post-pilot\n",
        encoding="utf-8",
    )

    proposals = parse_lessons_dir(lessons)
    out = save_proposals_yaml(
        proposals, slug="odyssey", orchestrator_home=tmp_path
    )
    assert out == proposals_yaml_path("odyssey", orchestrator_home=tmp_path)

    result = apply_proposals_batch(
        proposals, skills_root=skills_root, auto_apply=True
    )
    assert len(result.applied) == 1
    persisted = yaml.safe_load(
        (skills_root / "policy" / "code-review-gates.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert persisted["p0_threshold"] == 0.85


def test_e9_chain_lesson_multi_proposal_updates_three_policy_files(
    tmp_path: Path, audit_log: Path
) -> None:
    skills_root = tmp_path / "skills"
    _build_skills_overlay(skills_root, names=["bmad-code-review"])
    _write_baseline_policy_yamls(skills_root)

    text = (
        "## Policy proposal: code-review-gates.p0_threshold\n"
        "before: 0.8\nafter: 0.7\n\n"
        "## Policy proposal: cost-tuning.daily_cap_usd\n"
        "before: 50.0\nafter: 75.0\n\n"
        "## Policy proposal: retry-policy.max_retries\n"
        "before: 3\nafter: 5\n"
    )
    proposals = parse_lesson_markdown(text, source=tmp_path / "x.md")
    result = apply_proposals_batch(
        proposals, skills_root=skills_root, auto_apply=True
    )
    assert len(result.applied) == 3

    crg = yaml.safe_load(
        policy_file_path("code-review-gates", skills_root=skills_root).read_text()
    )
    cst = yaml.safe_load(
        policy_file_path("cost-tuning", skills_root=skills_root).read_text()
    )
    rty = yaml.safe_load(
        policy_file_path("retry-policy", skills_root=skills_root).read_text()
    )
    assert crg["p0_threshold"] == 0.7
    assert cst["daily_cap_usd"] == 75.0
    assert rty["max_retries"] == 5


def test_e9_chain_lessons_dir_two_files_aggregates_proposals_sorted(
    tmp_path: Path,
) -> None:
    skills_root = tmp_path / "skills"
    lessons = skills_root / "lessons" / "odyssey"
    lessons.mkdir(parents=True)
    # File b before file a in glob — sorted() makes order deterministic.
    (lessons / "wave-1b.md").write_text(
        "## Policy proposal: code-review-gates.test_coverage_threshold\n"
        "before: 0.5\nafter: 0.6\n",
        encoding="utf-8",
    )
    (lessons / "wave-1a.md").write_text(
        "## Policy proposal: code-review-gates.p0_threshold\n"
        "before: 0.8\nafter: 0.85\n",
        encoding="utf-8",
    )
    proposals = parse_lessons_dir(lessons)
    fields = [p.field for p in proposals]
    # wave-1a sorts before wave-1b.
    assert fields == ["p0_threshold", "test_coverage_threshold"]


def test_e9_chain_proposal_invalid_schema_keeps_policy_byte_equal_pre(
    tmp_path: Path, audit_log: Path
) -> None:
    from bmad_orchestrator.runtime.lesson_parser import PolicyApplyError

    skills_root = tmp_path / "skills"
    _build_skills_overlay(skills_root, names=["bmad-code-review"])
    _write_baseline_policy_yamls(skills_root)

    crg = policy_file_path("code-review-gates", skills_root=skills_root)
    before_bytes = crg.read_bytes()

    bad = LessonProposal(
        policy_file="code-review-gates",
        field="p0_threshold",
        before=0.8,
        after=2.0,  # out-of-range
        rationale=None,
        source_file="x.md",
    )
    with pytest.raises(PolicyApplyError):
        apply_proposal(bad, skills_root=skills_root)
    assert crg.read_bytes() == before_bytes


def test_e9_chain_audit_log_carries_before_after_for_rollback_invariant(
    tmp_path: Path, audit_log: Path
) -> None:
    """Two applied proposals → two audit rows, each with full before/after."""
    skills_root = tmp_path / "skills"
    _build_skills_overlay(skills_root, names=["bmad-code-review"])
    _write_baseline_policy_yamls(skills_root)

    text = (
        "## Policy proposal: code-review-gates.p0_threshold\n"
        "before: 0.8\nafter: 0.7\nrationale: tuning down\n\n"
        "## Policy proposal: cost-tuning.daily_cap_usd\n"
        "before: 50.0\nafter: 80.0\nrationale: increase budget\n"
    )
    proposals = parse_lesson_markdown(text, source=tmp_path / "lesson.md")
    apply_proposals_batch(proposals, skills_root=skills_root, auto_apply=True)

    rows = [
        r
        for r in _read_audit(audit_log)
        if r["event_type"] == "policy_proposal_applied"
    ]
    assert len(rows) == 2
    for r in rows:
        assert "before" in r and "after" in r
        assert "rationale" in r
        assert "policy_path" in r


# ════════════════════════════════════════════════════════════════════════════
# E. CLI policy-apply (3 tests)
# ════════════════════════════════════════════════════════════════════════════


def test_e9_cli_policy_apply_auto_apply_synthesises_full_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_log: Path
) -> None:
    skills_root = tmp_path / "skills"
    _build_skills_overlay(skills_root, names=["bmad-code-review"])
    _write_baseline_policy_yamls(skills_root)
    lessons = skills_root / "lessons" / "odyssey"
    lessons.mkdir(parents=True)
    (lessons / "wave-1a.md").write_text(
        "## Policy proposal: code-review-gates.p0_threshold\n"
        "before: 0.8\nafter: 0.65\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ORCHESTRATOR_ORCHESTRATOR_HOME", str(tmp_path))

    runner = CliRunner()
    res = runner.invoke(
        app,
        [
            "policy-apply",
            "odyssey",
            "--auto-apply",
            "--skills-root",
            str(skills_root),
            "--orchestrator-home",
            str(tmp_path),
        ],
    )
    assert res.exit_code == 0, res.output
    persisted = yaml.safe_load(
        (skills_root / "policy" / "code-review-gates.yaml").read_text()
    )
    assert persisted["p0_threshold"] == 0.65


def test_e9_cli_policy_apply_no_lessons_dir_emits_no_proposals_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skills_root = tmp_path / "skills"
    _build_skills_overlay(skills_root, names=["bmad-code-review"])
    _write_baseline_policy_yamls(skills_root)

    runner = CliRunner()
    res = runner.invoke(
        app,
        [
            "policy-apply",
            "ghostproject",
            "--auto-apply",
            "--skills-root",
            str(skills_root),
            "--orchestrator-home",
            str(tmp_path),
        ],
    )
    assert res.exit_code == 0
    assert "no proposals found" in res.output


def test_e9_cli_policy_apply_invalid_lesson_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skills_root = tmp_path / "skills"
    _build_skills_overlay(skills_root, names=["bmad-code-review"])
    _write_baseline_policy_yamls(skills_root)
    lessons = skills_root / "lessons" / "odyssey"
    lessons.mkdir(parents=True)
    (lessons / "broken.md").write_text(
        "## Policy proposal: code-review-gates.p0_threshold\n"
        "before: 0.8\n",  # missing after
        encoding="utf-8",
    )

    runner = CliRunner()
    res = runner.invoke(
        app,
        [
            "policy-apply",
            "odyssey",
            "--auto-apply",
            "--skills-root",
            str(skills_root),
            "--orchestrator-home",
            str(tmp_path),
        ],
    )
    assert res.exit_code == 2


# ════════════════════════════════════════════════════════════════════════════
# F. Docs (3 tests)
# ════════════════════════════════════════════════════════════════════════════


def test_e9_docs_embedded_skills_architecture_file_exists() -> None:
    assert DOCS_FILE.is_file(), f"missing: {DOCS_FILE}"


def test_e9_docs_has_required_sections() -> None:
    body = DOCS_FILE.read_text(encoding="utf-8")
    for heading in (
        "Design rationale",
        "Upgrade flow",
        "customize",
        "policy",
        "lessons",
    ):
        assert heading.lower() in body.lower(), f"docs missing section: {heading}"


def test_e9_docs_has_manual_smoke_instructions() -> None:
    body = DOCS_FILE.read_text(encoding="utf-8")
    assert "bmad-orchestrator run" in body
    assert "--wave" in body and "--max-stories" in body


# ════════════════════════════════════════════════════════════════════════════
# G. Grep DoD (4 tests) — cross-module wiring invariants
# ════════════════════════════════════════════════════════════════════════════


def test_e9_grep_apply_embedded_skills_wired_in_worker_spawn() -> None:
    body = (REPO_ROOT / "src/bmad_orchestrator/runtime/worker_spawn.py").read_text(
        encoding="utf-8"
    )
    assert "apply_embedded_skills" in body


def test_e9_grep_apply_live_tuning_wired_in_code_review_subscriber() -> None:
    body = (REPO_ROOT / "src/bmad_orchestrator/agent/run.py").read_text(
        encoding="utf-8"
    )
    assert "_apply_live_tuning" in body
    assert "code_review_subscriber" in body


def test_e9_grep_prime_from_memory_wired_in_agent_run_boot_path() -> None:
    body = (REPO_ROOT / "src/bmad_orchestrator/agent/run.py").read_text(
        encoding="utf-8"
    )
    assert "prime_from_memory" in body


def test_e9_grep_policy_apply_subcommand_registered_in_cli_main() -> None:
    body = (REPO_ROOT / "src/bmad_orchestrator/cli/main.py").read_text(
        encoding="utf-8"
    )
    assert 'name="policy-apply"' in body or "policy-apply" in body
    assert "def policy_apply(" in body
