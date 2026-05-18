"""Phase 3 Step B — real-mode eval harness tests.

Covers:
* YAML schema acceptance of optional ``tags`` field
* Discovery of the bundled ``evals/cases/real/`` manifest (≥5 cases)
* ``filter_cases_by_tags`` helper (match + no-match)
* ``run_eval_suite`` honours ``cases_dir`` override
* ``--project-root`` pins ``ORCHESTRATOR_TARGET_PROJECT`` in real mode
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from bmad_orchestrator.eval.runner import (
    filter_cases_by_tags,
    load_cases,
    run_eval_suite,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_CASES_DIR = REPO_ROOT / "evals" / "cases" / "real"


# ── schema ─────────────────────────────────────────────────────────────────


def test_load_cases_accepts_optional_tags_field(tmp_path: Path) -> None:
    """Cases with a ``tags: [...]`` list parse without error."""
    manifest = tmp_path / "cases.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "cases": [
                    {
                        "id": "TC-T1",
                        "level": "easy",
                        "story_id": "tagged-1",
                        "story_path": "tagged-1.md",
                        "tags": ["cli", "low-risk"],
                        "expected": {"final_verdict": "approve"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    parsed = load_cases(manifest)
    assert len(parsed) == 1
    assert parsed[0]["tags"] == ["cli", "low-risk"]


def test_load_cases_rejects_non_list_tags(tmp_path: Path) -> None:
    """Non-list ``tags`` (e.g. comma string) → ValueError."""
    manifest = tmp_path / "cases.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "cases": [
                    {
                        "id": "TC-T2",
                        "level": "easy",
                        "story_id": "bad-tags",
                        "story_path": "bad-tags.md",
                        "tags": "cli,low-risk",  # string, not list
                        "expected": {"final_verdict": "approve"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="'tags' must be a list"):
        load_cases(manifest)


def test_real_cases_manifest_has_five_well_formed_cases() -> None:
    """The bundled real-mode manifest ships ≥5 cases (3 easy + 2 medium)."""
    manifest = REAL_CASES_DIR / "cases.yaml"
    assert manifest.is_file(), f"missing real-mode manifest: {manifest}"
    cases = load_cases(manifest)
    assert len(cases) >= 5
    levels = [c["level"] for c in cases]
    assert levels.count("easy") >= 3
    assert levels.count("medium") >= 2
    # every case carries tags + each story_path resolves to an existing fixture
    for c in cases:
        assert isinstance(c.get("tags"), list) and c["tags"], (
            f"case {c['id']} missing or empty tags"
        )
        story_file = REAL_CASES_DIR / c["story_path"]
        assert story_file.is_file(), f"missing story fixture: {story_file}"


# ── filter_cases_by_tags ───────────────────────────────────────────────────


def test_filter_cases_by_tags_keeps_matching_or_semantics() -> None:
    """OR semantics: case with ANY listed tag is kept."""
    cases = [
        {"id": "A", "tags": ["cli", "low-risk"]},
        {"id": "B", "tags": ["runtime"]},
        {"id": "C", "tags": ["docs", "low-risk"]},
        {"id": "D"},  # no tags field
    ]
    kept = filter_cases_by_tags(cases, ["low-risk"])
    assert [c["id"] for c in kept] == ["A", "C"]

    # empty/None filter → no-op
    assert filter_cases_by_tags(cases, None) == cases
    assert filter_cases_by_tags(cases, []) == cases


def test_filter_cases_by_tags_no_match_returns_empty() -> None:
    cases = [{"id": "A", "tags": ["cli"]}, {"id": "B", "tags": ["runtime"]}]
    assert filter_cases_by_tags(cases, ["nonexistent"]) == []


# ── run_eval_suite — cases_dir + project_root ──────────────────────────────


@pytest.mark.asyncio
async def test_run_eval_suite_uses_cases_dir_override(tmp_path: Path) -> None:
    """When ``cases_dir`` is set, manifest is read from there, not ``evals_root``."""
    evals_root = tmp_path / "evals"
    evals_root.mkdir()
    # Plant a DIFFERENT (failing) manifest at evals_root to prove it's bypassed.
    (evals_root / "cases.yaml").write_text(
        yaml.safe_dump({"not_cases": "should be ignored"}), encoding="utf-8"
    )

    custom_dir = tmp_path / "real"
    custom_dir.mkdir()
    story = custom_dir / "trivial.md"
    story.write_text("# trivial\n", encoding="utf-8")
    (custom_dir / "cases.yaml").write_text(
        yaml.safe_dump(
            {
                "cases": [
                    {
                        "id": "OVR-001",
                        "level": "easy",
                        "story_id": "trivial",
                        "story_path": "trivial.md",
                        "tags": ["cli"],
                        "expected": {
                            "final_verdict": "approve",
                            "max_iterations": 1,
                            "max_cost_usd": 1.0,
                            "must_emit": ["worker_completed"],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    worktree_root = tmp_path / "worktrees"
    worktree_root.mkdir()
    results, agg, _ = await run_eval_suite(
        evals_root=evals_root,
        worktree_root=worktree_root,
        cases_dir=custom_dir,
        mode="mock",
    )
    assert len(results) == 1
    assert results[0].case_id == "OVR-001"
    assert agg.pass_rate == 1.0


@pytest.mark.asyncio
async def test_run_eval_suite_real_mode_pins_project_root_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In real mode with ``project_root``, ORCHESTRATOR_TARGET_PROJECT points at it."""
    evals_root = tmp_path / "evals"
    evals_root.mkdir()
    story = evals_root / "cases" / "easy" / "1-1.md"
    story.parent.mkdir(parents=True, exist_ok=True)
    story.write_text("# story\n", encoding="utf-8")
    (evals_root / "cases.yaml").write_text(
        yaml.safe_dump(
            {
                "cases": [
                    {
                        "id": "TC-001",
                        "level": "easy",
                        "story_id": "1-1",
                        "story_path": "cases/easy/1-1.md",
                        "expected": {"final_verdict": "approve"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    target_project = tmp_path / "target-bmad"
    target_project.mkdir()

    captured: dict[str, str] = {}

    real_path = "bmad_orchestrator.eval.runner._run_one_case"
    from bmad_orchestrator.eval import runner as _runner

    async def _spy(case, **kwargs):  # type: ignore[no-untyped-def]
        captured["target"] = os.environ.get("ORCHESTRATOR_TARGET_PROJECT", "<unset>")
        # short-circuit before any subprocess work
        from bmad_orchestrator.eval.metrics import CaseResult

        return CaseResult(
            case_id=str(case["id"]),
            level=str(case["level"]),
            passed=True,
            final_verdict="approve",
            review_iteration=1,
            cost_usd=0.0,
            latency_ms=1,
            emitted_event_types=("worker_completed", "code_review_verdict"),
            failure_reasons=(),
        )

    monkeypatch.setattr(_runner, "_run_one_case", _spy)
    monkeypatch.delenv("ORCHESTRATOR_TARGET_PROJECT", raising=False)

    worktree_root = tmp_path / "worktrees"
    worktree_root.mkdir()
    _results, _agg, _ = await run_eval_suite(
        evals_root=evals_root,
        worktree_root=worktree_root,
        mode="real",
        project_root=target_project,
    )
    assert captured["target"] == str(target_project)
    assert real_path  # silence unused
