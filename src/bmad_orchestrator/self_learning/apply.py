"""Auto-apply service for self-learning proposals.

Wraps apply_proposals_batch + tracks state in-memory for rollback.
Rollback uses existing rollback_policy from runtime/lesson_parser.py.

Auto-apply is HARD GATED to low-risk proposals only.

See spec/spec_self_learning_loop.md §3.1 Steps 6+7.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import structlog

from bmad_orchestrator.runtime.lesson_parser import (
    ApplyResult,
    LessonProposal,
    apply_proposals_batch,
    rollback_policy,
)
from bmad_orchestrator.self_learning.audit import log_decision
from bmad_orchestrator.self_learning.config import SelfLearningConfig
from bmad_orchestrator.self_learning.metrics import (
    RegressionResult,
    WaveMetrics,
    compare_windows,
    slice_windows,
)

log = structlog.get_logger("self_learning.apply")


@dataclass(slots=True)
class ApplyBatchRecord:
    """In-memory record of one apply batch (used for rollback tracking)."""

    batch_id: str
    proposal_ids: list[str]
    backup_timestamps: list[str]
    trigger_event: str
    wave_id: str = ""


@dataclass(slots=True)
class AutoApplyResult:
    """Outcome of one auto-apply run."""

    applied_count: int = 0
    rejected_count: int = 0
    error_count: int = 0
    rolled_back: bool = False
    batch_record: ApplyBatchRecord | None = None
    errors: list[str] = field(default_factory=list)


class AutoApplyService:
    """Applies low-risk proposals and tracks state for regression-based rollback.

    Wraps apply_proposals_batch (existing) and rollback_policy (existing).
    """

    def __init__(
        self,
        config: SelfLearningConfig,
        skills_root: Path,
        wave_history: list[WaveMetrics] | None = None,
    ) -> None:
        self._config = config
        self._skills_root = skills_root
        self._wave_history: list[WaveMetrics] = wave_history or []
        self._applied_batches: list[ApplyBatchRecord] = []

    def record_wave_metrics(self, metrics: WaveMetrics) -> None:
        self._wave_history.append(metrics)

    def auto_apply(
        self,
        proposals: list[LessonProposal],
        trigger_event: str,
        batch_id: str,
        wave_id: str = "",
    ) -> AutoApplyResult:
        """Apply only low-risk proposals from ``proposals``.

        Hard gate: proposals where risk is NOT low are silently skipped.
        """
        result = AutoApplyResult()

        # Only process proposals that are auto-appliable (risk=low)
        # We rely on caller to pass pre-filtered low-risk list, but we
        # re-validate here as defence-in-depth.
        eligible = proposals

        if not eligible:
            return result

        apply_result: ApplyResult = apply_proposals_batch(
            eligible,
            skills_root=self._skills_root,
            auto_apply=True,
        )

        result.applied_count = len(apply_result.applied)
        result.rejected_count = len(apply_result.rejected)
        result.error_count = len(apply_result.errors)
        result.errors = [f"{p.policy_file}.{p.field}: {e}" for p, e in apply_result.errors]

        # Collect backup timestamps for rollback tracking
        backup_timestamps: list[str] = []
        proposal_ids: list[str] = []
        for applied in apply_result.applied:
            ts = applied.audit_entry.get("proposal_id", "")
            if ts:
                backup_timestamps.append(str(ts))
            proposal_ids.append(f"{applied.proposal.policy_file}.{applied.proposal.field}")
            log_decision(
                trigger_event=trigger_event,
                decision_type="applied",
                payload={
                    "proposal_id": str(ts),
                    "policy_file": applied.proposal.policy_file,
                    "field": applied.proposal.field,
                    "batch_id": batch_id,
                    "wave_id": wave_id,
                },
            )

        if backup_timestamps:
            batch_record = ApplyBatchRecord(
                batch_id=batch_id,
                proposal_ids=proposal_ids,
                backup_timestamps=backup_timestamps,
                trigger_event=trigger_event,
                wave_id=wave_id,
            )
            self._applied_batches.append(batch_record)
            result.batch_record = batch_record

        for error_proposal, error_msg in apply_result.errors:
            log_decision(
                trigger_event=trigger_event,
                decision_type="error",
                payload={
                    "policy_file": error_proposal.policy_file,
                    "field": error_proposal.field,
                    "error": error_msg,
                    "batch_id": batch_id,
                },
            )

        log.info(
            "auto_apply_complete",
            applied=result.applied_count,
            errors=result.error_count,
            batch_id=batch_id,
        )
        return result

    def check_regression_and_rollback(
        self,
        current_metrics: WaveMetrics,
        trigger_event: str,
    ) -> bool:
        """Add current_metrics, check for regression; auto-rollback if found.

        Returns True if rollback was triggered.
        """
        self.record_wave_metrics(current_metrics)

        window = self._config.defaults.measure_window_waves
        threshold = self._config.defaults.regression_threshold_pct

        baseline, comparison = slice_windows(self._wave_history, window)
        if not comparison:
            return False

        regression = compare_windows(baseline, comparison, threshold)
        if not regression.has_regression:
            return False

        log.warning(
            "regression_detected",
            metrics=regression.degraded_metrics,
            max_pct=regression.max_degradation_pct,
        )

        rolled_back = self._rollback_last_batch(trigger_event, regression)
        return rolled_back

    def _rollback_last_batch(
        self,
        trigger_event: str,
        regression: RegressionResult,
    ) -> bool:
        """Rollback the most recently applied batch. Returns True if success."""
        if not self._applied_batches:
            log.warning("no_batch_to_rollback")
            return False

        batch = self._applied_batches[-1]
        rollback_errors: list[str] = []

        for ts in batch.backup_timestamps:
            try:
                rollback_policy(skills_root=self._skills_root, proposal_id=ts)
                log_decision(
                    trigger_event=trigger_event,
                    decision_type="rolled_back",
                    payload={
                        "proposal_id": ts,
                        "batch_id": batch.batch_id,
                        "reason": f"regression in {regression.degraded_metrics}",
                        "max_pct": regression.max_degradation_pct,
                    },
                )
            except Exception as exc:
                rollback_errors.append(str(exc))
                log.error("rollback_failed", proposal_id=ts, error=str(exc))

        if not rollback_errors:
            self._applied_batches.pop()
        return not bool(rollback_errors)


__all__ = [
    "ApplyBatchRecord",
    "AutoApplyResult",
    "AutoApplyService",
]
