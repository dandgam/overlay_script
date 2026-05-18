"""Phase 4 hardening #7 — pass^k metric in eval suite.

Spec: spec_phase4_hardening §2.7.

Coverage (9 tests):

Unit — pass^k math:
  * k=1 trivial: pass_consistency_at_k == pass_at_k (same as pass@1 = pass@1)
  * k=3 all-pass = 1.0
  * k=3 any fail = 0.0 (one case fails one attempt)
  * k=3 partial cases (mix of all-pass and some-fail)
  * empty results → 0.0
  * k > available repeats → uses available (graceful truncation)

Integration:
  * eval runner with repeat=3 collects 3 results per case
  * both pass@k and pass^k computed and present in extra_metrics
  * pass^k ≤ pass@k always (math sanity invariant)
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bmad_orchestrator.eval.metrics import pass_at_k, pass_consistency_at_k
from bmad_orchestrator.eval.runner import run_eval_suite

# ── Unit tests: pass^k math ───────────────────────────────────────────────────


def test_k1_pass_consistency_equals_pass_at_1() -> None:
    """k=1: pass^1 should equal pass@1 (trivially — 1 attempt)."""
    results = {
        "TC-001": [True],
        "TC-002": [False],
        "TC-003": [True],
    }
    pak = pass_at_k(results, k=1)
    pck = pass_consistency_at_k(results, k=1)
    assert abs(pak - pck) < 1e-9, "pass@1 must equal pass^1"
    assert abs(pak - 2 / 3) < 1e-9


def test_k3_all_pass_is_1() -> None:
    """k=3, all 3 attempts pass for every case → pass^3 = 1.0."""
    results = {
        "TC-001": [True, True, True],
        "TC-002": [True, True, True],
    }
    assert pass_consistency_at_k(results, k=3) == 1.0


def test_k3_any_fail_is_0() -> None:
    """k=3, one case has a failing attempt → pass^3 = 0.0 for that case."""
    results = {
        "TC-001": [True, True, False],
    }
    assert pass_consistency_at_k(results, k=3) == 0.0


def test_k3_partial_cases_mix() -> None:
    """k=3, some cases all-pass, some have failures → correct fraction."""
    results = {
        "TC-001": [True, True, True],   # all pass
        "TC-002": [True, True, False],  # one fail → doesn't count
        "TC-003": [True, True, True],   # all pass
        "TC-004": [False, True, True],  # one fail → doesn't count
    }
    pck = pass_consistency_at_k(results, k=3)
    # 2 out of 4 cases are all-pass → 0.5
    assert abs(pck - 0.5) < 1e-9

    # pass@3 should be higher (at least one pass per case)
    pak = pass_at_k(results, k=3)
    assert pak >= pck  # math sanity


def test_empty_results_returns_zero() -> None:
    """Empty results dict → both metrics return 0.0."""
    assert pass_at_k({}, k=3) == 0.0
    assert pass_consistency_at_k({}, k=3) == 0.0


def test_k_greater_than_available_repeats_uses_available() -> None:
    """k > len(results) for a case → uses available results (graceful truncation)."""
    results = {"TC-001": [True, True]}  # only 2 repeats
    # k=5 but only 2 available — should use 2, not crash
    pak = pass_at_k(results, k=5)
    pck = pass_consistency_at_k(results, k=5)
    # Both attempts pass → pass@k=1.0, pass^k=1.0
    assert pak == 1.0
    assert pck == 1.0


# ── Integration tests ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_eval_runner_repeat3_collects_3_results_per_case(
    tmp_path: Path,
) -> None:
    """Eval runner with repeat=3 runs each case 3 times and populates results_per_case."""
    evals_root = tmp_path / "evals"
    worktree_root = tmp_path / "wts"
    evals_root.mkdir()
    worktree_root.mkdir()

    cases_yaml = evals_root / "cases.yaml"
    cases_yaml.write_text(
        yaml.dump({
            "cases": [
                {
                    "id": "TC-001",
                    "level": "easy",
                    "story_id": "1.1",
                    "story_path": "stories/1.1.md",
                    "expected": {"final_verdict": "any"},
                },
                {
                    "id": "TC-002",
                    "level": "medium",
                    "story_id": "1.2",
                    "story_path": "stories/1.2.md",
                    "expected": {"final_verdict": "any"},
                },
            ]
        }),
        encoding="utf-8",
    )

    results, _aggregate, extra_metrics = await run_eval_suite(
        evals_root=evals_root,
        worktree_root=worktree_root,
        mode="mock",
        repeat=3,
    )

    # 2 cases × 3 repeats = 6 total results
    assert len(results) == 6

    # extra_metrics must be populated
    assert "results_per_case" in extra_metrics
    rpc = extra_metrics["results_per_case"]
    assert "TC-001" in rpc
    assert "TC-002" in rpc
    # Each case has exactly 3 repeat results
    assert len(rpc["TC-001"]) == 3
    assert len(rpc["TC-002"]) == 3


@pytest.mark.asyncio
async def test_eval_runner_repeat3_computes_both_metrics(
    tmp_path: Path,
) -> None:
    """Extra metrics contain both pass@k and pass^k when repeat > 1."""
    evals_root = tmp_path / "evals"
    worktree_root = tmp_path / "wts"
    evals_root.mkdir()
    worktree_root.mkdir()

    cases_yaml = evals_root / "cases.yaml"
    cases_yaml.write_text(
        yaml.dump({
            "cases": [
                {
                    "id": "TC-A",
                    "level": "easy",
                    "story_id": "2.1",
                    "story_path": "stories/2.1.md",
                    "expected": {"final_verdict": "any"},
                },
            ]
        }),
        encoding="utf-8",
    )

    _results, _aggregate, extra_metrics = await run_eval_suite(
        evals_root=evals_root,
        worktree_root=worktree_root,
        mode="mock",
        repeat=3,
    )

    assert "pass_at_k" in extra_metrics
    assert "pass_caret_k" in extra_metrics
    assert extra_metrics["k"] == 3


@pytest.mark.asyncio
async def test_pass_caret_k_le_pass_at_k_invariant(
    tmp_path: Path,
) -> None:
    """pass^k must always be ≤ pass@k (math sanity invariant)."""
    evals_root = tmp_path / "evals"
    worktree_root = tmp_path / "wts"
    evals_root.mkdir()
    worktree_root.mkdir()

    cases_yaml = evals_root / "cases.yaml"
    cases_yaml.write_text(
        yaml.dump({
            "cases": [
                {
                    "id": f"TC-{i:03d}",
                    "level": "easy",
                    "story_id": f"3.{i}",
                    "story_path": f"stories/3.{i}.md",
                    "expected": {"final_verdict": "any"},
                }
                for i in range(1, 6)
            ]
        }),
        encoding="utf-8",
    )

    _results, _aggregate, extra_metrics = await run_eval_suite(
        evals_root=evals_root,
        worktree_root=worktree_root,
        mode="mock",
        repeat=3,
    )

    pak = extra_metrics["pass_at_k"]
    pck = extra_metrics["pass_caret_k"]
    assert pck <= pak + 1e-9, (
        f"pass^k ({pck:.4f}) must be ≤ pass@k ({pak:.4f})"
    )
