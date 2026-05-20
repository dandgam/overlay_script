"""Eval suite runner — execute cases through the orchestrator + harvest metrics.

Modes:
  * **mock** (default, free) — uses ``spawn_worker(mock=True)``. Validates the
    orchestrator's event plumbing + harness wiring without burning tokens.
    Default verdict is synthesised: ``approve`` with iteration=1, cost=0.
    Useful for harness regression and CI.
  * **real** (TODO Step B) — spawns ``claude -p`` for each story. Burns
    tokens; not exercised in unit tests.

Outputs a list of :class:`bmad_orchestrator.eval.metrics.CaseResult` plus a
machine-readable JSON report saved under ``evals/results-<timestamp>.json``.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from bmad_orchestrator.agent.tools._common import worker_jsonl_path
from bmad_orchestrator.eval.metrics import (
    AggregateMetrics,
    CaseResult,
    aggregate_results,
    case_passed,
    pass_at_k,
    pass_consistency_at_k,
)
from bmad_orchestrator.runtime.worker_spawn import spawn_worker, tail_jsonl_events


def load_cases(manifest_path: Path) -> list[dict[str, Any]]:
    """Parse ``cases.yaml`` → list of case dicts. Validates required keys.

    Optional keys (passed through unchanged): ``tags`` (list[str]), ``may_emit``.
    """
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    cases = raw.get("cases", []) if isinstance(raw, dict) else []
    if not isinstance(cases, list):
        raise ValueError(f"{manifest_path}: 'cases' must be a list")
    for c in cases:
        for k in ("id", "level", "story_id", "story_path", "expected"):
            if k not in c:
                raise ValueError(f"case {c.get('id', '?')} missing required key {k!r}")
        tags = c.get("tags")
        if tags is not None and not isinstance(tags, list):
            raise ValueError(
                f"case {c.get('id', '?')} 'tags' must be a list of strings, got {type(tags).__name__}"
            )
    return cases


def filter_cases_by_tags(
    cases: list[dict[str, Any]], tags: list[str] | None
) -> list[dict[str, Any]]:
    """Keep cases that have at least one tag in ``tags`` (OR semantics).

    Empty/None ``tags`` → return all cases unchanged. Cases without a ``tags``
    field never match a non-empty filter.
    """
    if not tags:
        return cases
    wanted = set(tags)
    return [c for c in cases if wanted.intersection(set(c.get("tags") or []))]


async def _run_one_case(
    case: dict[str, Any],
    *,
    evals_root: Path,
    worktree_root: Path,
    mode: str = "mock",
) -> CaseResult:
    """Spawn one worker, follow JSONL events, build a CaseResult."""
    case_id = str(case["id"])
    level = str(case["level"])
    story_id = str(case["story_id"])
    expected = case.get("expected") or {}

    # Per-case isolated worktree.
    wt = worktree_root / case_id
    wt.mkdir(parents=True, exist_ok=True)

    # Q-26140-a1b2 — real-mode workers MUST share host netns or bwrap
    # --unshare-net blocks api.anthropic.com (Step B 2026-05-20 incident).
    # Mock-mode keeps the secure default "none" — no real LLM calls happen.
    is_mock = mode != "real"
    start = time.monotonic()
    handle = await spawn_worker(
        worktree=str(wt),
        story_id=story_id,
        branch=f"feature/{story_id}",
        mock=is_mock,
        sandbox_network="full" if not is_mock else "none",
    )

    emitted: list[str] = []
    final_verdict = "error"
    review_iteration = 1
    cost_usd = 0.0

    worker_success = False
    async for ev in tail_jsonl_events(handle.jsonl_path):
        et = str(ev.get("event_type", ""))
        emitted.append(et)
        if et == "code_review_verdict":
            final_verdict = str(ev.get("verdict") or final_verdict)
            try:
                review_iteration = int(ev.get("review_iteration", 1) or 1)
            except (TypeError, ValueError):
                pass
        elif et == "worker_completed":
            worker_success = ev.get("status") == "success"
            if final_verdict == "error" and worker_success:
                final_verdict = "approve"
            try:
                cost_usd = float(ev.get("cost_usd", cost_usd) or cost_usd)
            except (TypeError, ValueError):
                pass

    # Mock mode never actually spawns the code-review worker, so the
    # ``code_review_verdict`` event is missing. The harness synthesises one
    # so case must_emit contracts written for real-mode still validate the
    # plumbing. Real mode (Step B) emits naturally — no synthesis runs.
    if is_mock and worker_success and "code_review_verdict" not in emitted:
        emitted.append("code_review_verdict")

    latency_ms = int((time.monotonic() - start) * 1000)

    passed, reasons = case_passed(
        final_verdict=final_verdict,
        review_iteration=review_iteration,
        cost_usd=cost_usd,
        emitted_event_types=emitted,
        expected=expected,
    )

    return CaseResult(
        case_id=case_id,
        level=level,
        passed=passed,
        final_verdict=final_verdict,
        review_iteration=review_iteration,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        emitted_event_types=tuple(emitted),
        failure_reasons=tuple(reasons),
    )


async def run_eval_suite(
    *,
    evals_root: Path,
    worktree_root: Path,
    case_filter: str | None = None,
    mode: str = "mock",
    repeat: int = 1,
    cases_dir: Path | None = None,
    project_root: Path | None = None,
    tags: list[str] | None = None,
) -> tuple[list[CaseResult], AggregateMetrics, dict[str, Any]]:
    """Run every case in ``evals_root/cases.yaml``; aggregate; return results + metrics.

    Args:
        repeat: number of times to run each case. When > 1, pass@k and pass^k
            metrics are computed over all repetitions and included in the extra
            metrics dict returned as the third element of the tuple.
        cases_dir: when provided, read ``<cases_dir>/cases.yaml`` instead of
            ``<evals_root>/cases.yaml``. Used by Phase 3 Step B real-mode runs
            that keep their manifest under ``evals/cases/real/``.
        project_root: when provided AND ``mode == "real"``, ``ORCHESTRATOR_TARGET_PROJECT``
            is pinned to this path so workers spawn inside a real BMad target
            project rather than the per-eval worktree root. In mock mode the
            arg is ignored.
        tags: optional OR-filter — keep only cases whose ``tags`` list shares
            at least one entry with this filter.

    Returns:
        (results, aggregate, extra_metrics) where ``extra_metrics`` is empty when
        ``repeat == 1`` and contains ``pass_at_k`` / ``pass_caret_k`` when
        ``repeat > 1``.
    """
    import os

    if repeat < 1:
        raise ValueError(f"repeat must be >= 1, got {repeat}")

    manifest = (cases_dir or evals_root) / "cases.yaml"
    cases = load_cases(manifest)
    if case_filter:
        cases = [c for c in cases if c["id"] == case_filter]
        if not cases:
            raise ValueError(f"case_filter={case_filter!r} matched zero cases")
    if tags:
        cases = filter_cases_by_tags(cases, tags)
        if not cases:
            raise ValueError(f"tags={tags!r} matched zero cases")

    # Reset target_project env so worker_jsonl_path lands inside the eval root.
    # Each case's worktree is its own jsonl namespace. In real-mode the caller
    # may override with --project-root so the worker operates on a real BMad
    # checkout instead of the synthetic worktree.
    saved = os.environ.get("ORCHESTRATOR_TARGET_PROJECT")
    saved_wave = os.environ.get("BMAD_CURRENT_WAVE")
    if mode == "real" and project_root is not None:
        os.environ["ORCHESTRATOR_TARGET_PROJECT"] = str(project_root)
    else:
        os.environ["ORCHESTRATOR_TARGET_PROJECT"] = str(worktree_root)
    os.environ["BMAD_CURRENT_WAVE"] = "eval"
    try:
        results: list[CaseResult] = []
        # results_per_case tracks pass/fail for each repetition per case.
        results_per_case: dict[str, list[bool]] = {}

        for _repetition in range(repeat):
            for case in cases:
                case_id = str(case["id"])
                res = await _run_one_case(
                    case,
                    evals_root=evals_root,
                    worktree_root=worktree_root,
                    mode=mode,
                )
                results.append(res)
                if case_id not in results_per_case:
                    results_per_case[case_id] = []
                results_per_case[case_id].append(res.passed)
    finally:
        if saved is None:
            os.environ.pop("ORCHESTRATOR_TARGET_PROJECT", None)
        else:
            os.environ["ORCHESTRATOR_TARGET_PROJECT"] = saved
        if saved_wave is None:
            os.environ.pop("BMAD_CURRENT_WAVE", None)
        else:
            os.environ["BMAD_CURRENT_WAVE"] = saved_wave

    aggregate = aggregate_results(results)

    # Compute pass^k metrics when repeat > 1.
    extra_metrics: dict[str, Any] = {}
    if repeat > 1:
        extra_metrics["pass_at_k"] = pass_at_k(results_per_case, k=repeat)
        extra_metrics["pass_caret_k"] = pass_consistency_at_k(results_per_case, k=repeat)
        extra_metrics["k"] = repeat
        extra_metrics["results_per_case"] = {
            cid: list(bools) for cid, bools in results_per_case.items()
        }

    return results, aggregate, extra_metrics


def save_report(
    results: list[CaseResult],
    aggregate: AggregateMetrics,
    out_path: Path,
) -> None:
    """Persist machine-readable JSON report."""
    payload: dict[str, Any] = {
        "results": [asdict(r) for r in results],
        "aggregate": asdict(aggregate),
        "generated_at": int(time.time()),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


# Sync wrapper for callers (CLI) that aren't already in an event loop.
def run_eval_suite_sync(
    *,
    evals_root: Path,
    worktree_root: Path,
    case_filter: str | None = None,
    mode: str = "mock",
    repeat: int = 1,
    cases_dir: Path | None = None,
    project_root: Path | None = None,
    tags: list[str] | None = None,
) -> tuple[list[CaseResult], AggregateMetrics, dict[str, Any]]:
    return asyncio.run(
        run_eval_suite(
            evals_root=evals_root,
            worktree_root=worktree_root,
            case_filter=case_filter,
            mode=mode,
            repeat=repeat,
            cases_dir=cases_dir,
            project_root=project_root,
            tags=tags,
        )
    )


# Silence linter for unused import — worker_jsonl_path is part of the public
# contract via re-export for runner tests that need to plant fixtures.
_ = worker_jsonl_path
