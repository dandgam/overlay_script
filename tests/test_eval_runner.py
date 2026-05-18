"""Integration tests for bmad_orchestrator.eval.runner (mock mode)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bmad_orchestrator.eval.runner import (
    load_cases,
    run_eval_suite,
    save_report,
)


def _write_cases_yaml(path: Path, cases: list[dict]) -> None:
    import yaml

    path.write_text(yaml.safe_dump({"cases": cases}), encoding="utf-8")


def _write_story(path: Path, title: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"# Story {title}\n\nBody.\n",
        encoding="utf-8",
    )


# ── load_cases ─────────────────────────────────────────────────────────────


def test_load_cases_parses_minimal_manifest(tmp_path: Path) -> None:
    cases_yaml = tmp_path / "cases.yaml"
    _write_cases_yaml(
        cases_yaml,
        [
            {
                "id": "TC-001",
                "level": "easy",
                "story_id": "1-1",
                "story_path": "cases/easy/1-1.md",
                "expected": {"final_verdict": "approve"},
            }
        ],
    )
    parsed = load_cases(cases_yaml)
    assert len(parsed) == 1
    assert parsed[0]["id"] == "TC-001"


def test_load_cases_rejects_missing_required_key(tmp_path: Path) -> None:
    cases_yaml = tmp_path / "cases.yaml"
    _write_cases_yaml(
        cases_yaml,
        [
            {"id": "TC-X", "level": "easy"}  # missing story_id, story_path, expected
        ],
    )
    with pytest.raises(ValueError, match="missing required key"):
        load_cases(cases_yaml)


# ── run_eval_suite (mock mode end-to-end) ──────────────────────────────────


@pytest.mark.asyncio
async def test_run_eval_suite_smoke_one_case(tmp_path: Path) -> None:
    """End-to-end mock run on a single synthetic case must pass."""
    evals_root = tmp_path / "evals"
    evals_root.mkdir()
    story = evals_root / "cases" / "easy" / "1-1.md"
    _write_story(story, "1.1: Trivial")

    _write_cases_yaml(
        evals_root / "cases.yaml",
        [
            {
                "id": "TC-001",
                "level": "easy",
                "story_id": "1-1",
                "story_path": "cases/easy/1-1.md",
                "expected": {
                    "final_verdict": "approve",
                    "max_iterations": 1,
                    "max_cost_usd": 1.0,
                    "must_emit": ["worker_completed", "code_review_verdict"],
                },
            }
        ],
    )

    worktree_root = tmp_path / "worktrees"
    worktree_root.mkdir()

    results, agg, _ = await run_eval_suite(
        evals_root=evals_root,
        worktree_root=worktree_root,
        mode="mock",
    )

    assert len(results) == 1
    r = results[0]
    assert r.case_id == "TC-001"
    assert r.passed is True, f"expected pass, got reasons: {r.failure_reasons}"
    assert r.final_verdict == "approve"
    assert r.review_iteration == 1
    # Synthetic code_review_verdict injected by runner in mock mode.
    assert "code_review_verdict" in r.emitted_event_types
    assert "worker_completed" in r.emitted_event_types
    assert agg.pass_rate == 1.0


@pytest.mark.asyncio
async def test_run_eval_suite_filter_by_case_id(tmp_path: Path) -> None:
    evals_root = tmp_path / "evals"
    evals_root.mkdir()
    for sid in ("1-1", "1-2"):
        _write_story(evals_root / "cases" / "easy" / f"{sid}.md", sid)

    _write_cases_yaml(
        evals_root / "cases.yaml",
        [
            {
                "id": "TC-001",
                "level": "easy",
                "story_id": "1-1",
                "story_path": "cases/easy/1-1.md",
                "expected": {"final_verdict": "approve"},
            },
            {
                "id": "TC-002",
                "level": "easy",
                "story_id": "1-2",
                "story_path": "cases/easy/1-2.md",
                "expected": {"final_verdict": "approve"},
            },
        ],
    )

    worktree_root = tmp_path / "worktrees"
    worktree_root.mkdir()
    results, _, _extra = await run_eval_suite(
        evals_root=evals_root,
        worktree_root=worktree_root,
        case_filter="TC-002",
        mode="mock",
    )
    assert len(results) == 1
    assert results[0].case_id == "TC-002"


@pytest.mark.asyncio
async def test_run_eval_suite_filter_no_match_raises(tmp_path: Path) -> None:
    evals_root = tmp_path / "evals"
    evals_root.mkdir()
    _write_story(evals_root / "cases" / "easy" / "1-1.md", "1.1")
    _write_cases_yaml(
        evals_root / "cases.yaml",
        [
            {
                "id": "TC-001",
                "level": "easy",
                "story_id": "1-1",
                "story_path": "cases/easy/1-1.md",
                "expected": {"final_verdict": "approve"},
            }
        ],
    )
    worktree_root = tmp_path / "worktrees"
    worktree_root.mkdir()
    with pytest.raises(ValueError, match="matched zero cases"):
        await run_eval_suite(
            evals_root=evals_root,
            worktree_root=worktree_root,
            case_filter="TC-999",
            mode="mock",
        )


# ── save_report ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_report_writes_json(tmp_path: Path) -> None:
    """JSON report includes per-case + aggregate + timestamp."""
    evals_root = tmp_path / "evals"
    evals_root.mkdir()
    _write_story(evals_root / "cases" / "easy" / "1-1.md", "1.1")
    _write_cases_yaml(
        evals_root / "cases.yaml",
        [
            {
                "id": "TC-001",
                "level": "easy",
                "story_id": "1-1",
                "story_path": "cases/easy/1-1.md",
                "expected": {"final_verdict": "approve"},
            }
        ],
    )
    worktree_root = tmp_path / "worktrees"
    worktree_root.mkdir()
    results, agg, _ = await run_eval_suite(
        evals_root=evals_root,
        worktree_root=worktree_root,
        mode="mock",
    )

    report_path = tmp_path / "report.json"
    save_report(results, agg, report_path)

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert "results" in payload
    assert "aggregate" in payload
    assert "generated_at" in payload
    assert payload["aggregate"]["pass_rate"] == 1.0
    assert len(payload["results"]) == 1
