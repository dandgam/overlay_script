"""Phase 3 eval suite — runner + metrics for the orchestrator agent.

See :mod:`bmad_orchestrator.eval.metrics` for pure aggregation functions and
:mod:`bmad_orchestrator.eval.runner` for the case-execution harness.

The CLI surface lives in :mod:`bmad_orchestrator.cli` under ``eval run``.
"""
from bmad_orchestrator.eval.metrics import (
    AggregateMetrics,
    CaseResult,
    aggregate_results,
    case_passed,
    percentile,
)

__all__ = [
    "AggregateMetrics",
    "CaseResult",
    "aggregate_results",
    "case_passed",
    "percentile",
]
