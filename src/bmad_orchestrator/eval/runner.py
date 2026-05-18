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
)
from bmad_orchestrator.runtime.worker_spawn import spawn_worker, tail_jsonl_events


def load_cases(manifest_path: Path) -> list[dict[str, Any]]:
    """Parse ``cases.yaml`` → list of case dicts. Validates required keys."""
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    cases = raw.get("cases", []) if isinstance(raw, dict) else []
    if not isinstance(cases, list):
        raise ValueError(f"{manifest_path}: 'cases' must be a list")
    for c in cases:
        for k in ("id", "level", "story_id", "story_path", "expected"):
            if k not in c:
                raise ValueError(f"case {c.get('id', '?')} missing required key {k!r}")
    return cases


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

    # mock-mode forces no real subprocess; real mode is not exercised yet.
    is_mock = mode != "real"
    start = time.monotonic()
    handle = await spawn_worker(
        worktree=str(wt),
        story_id=story_id,
        branch=f"feature/{story_id}",
        mock=is_mock,
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
) -> tuple[list[CaseResult], AggregateMetrics]:
    """Run every case in ``evals_root/cases.yaml``; aggregate; return both."""
    manifest = evals_root / "cases.yaml"
    cases = load_cases(manifest)
    if case_filter:
        cases = [c for c in cases if c["id"] == case_filter]
        if not cases:
            raise ValueError(f"case_filter={case_filter!r} matched zero cases")

    # Reset target_project env so worker_jsonl_path lands inside the eval root.
    # Each case's worktree is its own jsonl namespace.
    import os

    saved = os.environ.get("ORCHESTRATOR_TARGET_PROJECT")
    saved_wave = os.environ.get("BMAD_CURRENT_WAVE")
    os.environ["ORCHESTRATOR_TARGET_PROJECT"] = str(worktree_root)
    os.environ["BMAD_CURRENT_WAVE"] = "eval"
    try:
        results: list[CaseResult] = []
        for case in cases:
            res = await _run_one_case(
                case,
                evals_root=evals_root,
                worktree_root=worktree_root,
                mode=mode,
            )
            results.append(res)
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
    return results, aggregate


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
) -> tuple[list[CaseResult], AggregateMetrics]:
    return asyncio.run(
        run_eval_suite(
            evals_root=evals_root,
            worktree_root=worktree_root,
            case_filter=case_filter,
            mode=mode,
        )
    )


# Silence linter for unused import — worker_jsonl_path is part of the public
# contract via re-export for runner tests that need to plant fixtures.
_ = worker_jsonl_path
