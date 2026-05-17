"""F3 regression tests — P1 polish + P2 nice-to-haves.

Spec: spec/spec_embed_phase45_fixes.md §F3.

Coverage (10 tests):

* **P1-4** — ``fcntl.flock`` advisory locks on atomic YAML writes across
  three writer paths (``lesson_parser``, ``live_tuning``, ``project_memory``).
  Cross-process sidecar lockfile ``<parent>/.<name>.lock`` is created and
  serialises concurrent writers.
* **P1-6** — ``BudgetGuard.recent_story_costs()`` public accessor returns
  an immutable tuple snapshot; no callers reach into private
  ``_recent_story_costs`` deque.
* **P2-1** — ``skill_update.update_skills`` recomputes ``skills_count`` and
  ``skills`` list from the new ``current_upstream`` after swap (no longer
  blindly copies the old fixture values).
* **P2-2** — ``_corruption_result`` excludes ``bool`` from its
  ``isinstance(x, (int, float))`` guard so ``True`` / ``False`` do NOT
  become a silent ``1.0`` / ``0.0`` spent value.
* **P2-3** — narrowed ``except`` clauses in ``agent/run.py`` actually let
  unexpected exceptions propagate (a property test reading source).
"""

from __future__ import annotations

import inspect
import threading
from decimal import Decimal
from pathlib import Path

import yaml

from bmad_orchestrator.agent.safety.budget_guard import (
    BudgetGuard,
    _corruption_result,
)
from bmad_orchestrator.config import BudgetConfig
from bmad_orchestrator.runtime.lesson_parser import _atomic_yaml_write
from bmad_orchestrator.runtime.live_tuning import atomic_write_gates_yaml
from bmad_orchestrator.runtime.project_memory import (
    ProjectMemory,
    save_project_memory,
)
from bmad_orchestrator.runtime.skill_update import _enumerate_skills
from bmad_orchestrator.skills_repo import CodeReviewGates

# ── P1-4 — fcntl.flock sidecar lockfile ──────────────────────────────────────


def test_lesson_parser_atomic_write_creates_lock_sidecar(tmp_path: Path) -> None:
    target = tmp_path / "policy-proposals.yaml"
    _atomic_yaml_write(target, {"proposals": []})
    lock = tmp_path / ".policy-proposals.yaml.lock"
    assert target.exists()
    assert lock.exists(), "F3 P1-4 expects <parent>/.<name>.lock sidecar"


def test_live_tuning_atomic_write_creates_lock_sidecar(tmp_path: Path) -> None:
    policy_dir = tmp_path / "policy"
    policy_dir.mkdir()
    gates = CodeReviewGates(
        p0_threshold=0.95,
        test_coverage_threshold=0.7,
        compliance_tags=("OPENAPI_PRESENT",),
        sweep_every_stories=50,
    )
    target = policy_dir / "code-review-gates.yaml"
    atomic_write_gates_yaml(gates, target)
    lock = policy_dir / ".code-review-gates.yaml.lock"
    assert target.exists()
    assert lock.exists(), "F3 P1-4 expects <parent>/.<name>.lock sidecar"


def test_project_memory_atomic_write_creates_lock_sidecar(tmp_path: Path) -> None:
    home = tmp_path / "orch"
    home.mkdir()
    mem = ProjectMemory(project_slug="alpha")
    out_path = save_project_memory(mem, orchestrator_home=home)
    lock = out_path.parent / f".{out_path.name}.lock"
    assert out_path.exists()
    assert lock.exists(), "F3 P1-4 expects <parent>/.<name>.lock sidecar"


def test_lesson_parser_flock_serialises_concurrent_threads(tmp_path: Path) -> None:
    """Two threads racing into _atomic_yaml_write produce a single, valid YAML.

    The lock guarantees that the second writer's tempfile rename does not
    interleave with the first's fsync. We can't easily prove serialization
    without races, but we can assert the final file is well-formed (no
    half-written tempfile-rename artefact) and contains exactly one of the
    two payloads — neither a truncated file nor a yaml ParserError.
    """
    target = tmp_path / "proposals.yaml"

    def write_payload(value: int) -> None:
        _atomic_yaml_write(target, {"proposals": [{"id": value} for _ in range(50)]})

    threads = [threading.Thread(target=write_payload, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # File must parse cleanly and contain exactly one full payload (one of
    # the eight, whichever raced last on the rename).
    payload = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert "proposals" in payload
    assert len(payload["proposals"]) == 50
    ids = {p["id"] for p in payload["proposals"]}
    assert len(ids) == 1 and 0 <= next(iter(ids)) < 8


# ── P1-6 — BudgetGuard.recent_story_costs() public accessor ──────────────────


def test_budget_guard_recent_story_costs_empty_initially() -> None:
    guard = BudgetGuard(cfg=BudgetConfig())
    assert guard.recent_story_costs() == ()


def test_budget_guard_recent_story_costs_returns_decimal_snapshot() -> None:
    guard = BudgetGuard(cfg=BudgetConfig())
    guard.record_story_cost(0.10)
    guard.record_story_cost(0.25)
    guard.record_story_cost(0.50)
    snapshot = guard.recent_story_costs()
    assert isinstance(snapshot, tuple)
    assert all(isinstance(x, Decimal) for x in snapshot)
    assert [float(x) for x in snapshot] == [0.10, 0.25, 0.50]
    # Mutating the underlying deque (via another record) must NOT change a
    # previously-taken snapshot — public accessor is a copy.
    guard.record_story_cost(0.75)  # bumps oldest (deque maxlen=3)
    assert [float(x) for x in snapshot] == [0.10, 0.25, 0.50]
    assert [float(x) for x in guard.recent_story_costs()] == [0.25, 0.50, 0.75]


# ── P2-2 — bool excluded from int/float numeric guard ────────────────────────


def test_corruption_result_excludes_bool_from_numeric_guard() -> None:
    # Pre-fix: True and False would pass the isinstance(int, float) test and
    # be smuggled into BudgetResult.spent_usd as 1.0 / 0.0.
    res_true = _corruption_result(True, alarm=10.0, halt=20.0, scope="story")
    res_false = _corruption_result(False, alarm=10.0, halt=20.0, scope="story")
    assert res_true.spent_usd == 0.0
    assert res_false.spent_usd == 0.0
    # Numeric values are still passed through.
    res_num = _corruption_result(5.5, alarm=10.0, halt=20.0, scope="story")
    assert res_num.spent_usd == 5.5


# ── P2-1 — skill_update recomputes skills metadata from current_upstream ─────


def test_enumerate_skills_returns_sorted_skill_dirs(tmp_path: Path) -> None:
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    # 3 real skill dirs (have SKILL.md) + 1 distractor dir (no SKILL.md) + 1
    # stray file at root.
    for name in ("zeta", "alpha", "beta"):
        (upstream / name).mkdir()
        (upstream / name / "SKILL.md").write_text("stub", encoding="utf-8")
    (upstream / "not-a-skill").mkdir()
    (upstream / "not-a-skill" / "README.md").write_text("nope", encoding="utf-8")
    (upstream / "stray.txt").write_text("ignore me", encoding="utf-8")
    skills = _enumerate_skills(upstream)
    assert skills == ["alpha", "beta", "zeta"]


# ── P2-3 — narrowed except clauses in agent/run.py ───────────────────────────


def test_run_intent_router_load_narrowed_to_skill_error() -> None:
    """The load_skill_body try/except must NOT catch arbitrary Exception."""
    from bmad_orchestrator.agent import run as run_mod

    src = inspect.getsource(run_mod._dispatch_intent_router)
    # Specific narrow class present.
    assert "except (SkillError, OSError)" in src
    # The matched try-block of load_skill_body must not be wrapped by a
    # bare ``except Exception`` anymore.
    skill_block = src.split("load_skill_body(\"intent-router\")", 1)[1].split("\n\n", 1)[0]
    assert "except Exception" not in skill_block


def test_run_worktree_cleanup_narrowed_to_oserror() -> None:
    from bmad_orchestrator.agent import run as run_mod

    src = inspect.getsource(run_mod.merge_to_integration_subscriber)
    # cleanup_worktree wraps only OSError.
    assert "except OSError" in src
    # Cross-check that the specific cleanup block was rewritten (no broad
    # except remains with the cleanup_worktree comment).
    cleanup_idx = src.find("cleanup_worktree")
    assert cleanup_idx > 0
    tail = src[cleanup_idx : cleanup_idx + 400]
    assert "except OSError as exc:" in tail
    assert "except Exception as exc:" not in tail
