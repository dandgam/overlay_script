"""L2 live tuning — adaptive code-review gate thresholds (spec §E6).

The orchestrator collects per-story coverage samples in ``BudgetGuard`` deques
(``_recent_p0_counts``, ``_recent_test_counts``, ``_recent_review_iterations``).
This module is the pure-function side: compute new thresholds from those
samples, detect movements that exceed the bounds guard, and atomically write
the result to ``skills/policy/code-review-gates.yaml``.

Subscriber wiring lives in :mod:`bmad_orchestrator.agent.run`; this module
contains no I/O outside :func:`atomic_write_gates_yaml`.

Tuning model — robust to outliers
---------------------------------

* Sample = per-story coverage ratio in ``[0, 1]``.
* New threshold = ``median(samples)`` clamped to ``[0, 1]``.
* ``iqr(samples) = q3 - q1`` (interpolated) is reported alongside as
  «typical drift band»; downstream consumers may use it for diagnostics.
* Bounds guard: a movement is OK iff
  ``abs(new - current) <= max_movement_fraction * max(new, current, EPS)``.
  Default ``max_movement_fraction = 0.5`` per spec acceptance
  («threshold movements > 50% require human approval»).

The number of samples required before a metric is tunable at all is
controlled by :data:`MIN_SAMPLES_FOR_TUNING` (default 5). With fewer samples
the threshold stays at its current value and no escalation is emitted.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from statistics import median, quantiles
from typing import Literal

import yaml

from bmad_orchestrator.skills_repo import CodeReviewGates

MIN_SAMPLES_FOR_TUNING: int = 5
MAX_MOVEMENT_FRACTION_DEFAULT: float = 0.5
_EPS: float = 1e-9

TunableMetric = Literal["p0_threshold", "test_coverage_threshold"]


@dataclass(slots=True, frozen=True)
class TuningProposal:
    """Outcome of evaluating one metric's samples against the bounds guard."""

    metric: TunableMetric
    current_value: float
    proposed_value: float
    samples: tuple[float, ...]
    median_value: float
    iqr: float
    within_bounds: bool
    max_movement_fraction: float


def _iqr(samples: tuple[float, ...]) -> float:
    """Return Q3 - Q1 (interpolated quantiles) or ``0.0`` for tiny windows.

    ``statistics.quantiles`` needs at least 2 data points; we return 0.0 for
    1-sample or empty windows because IQR is undefined there.
    """
    if len(samples) < 2:
        return 0.0
    qs = quantiles(samples, n=4, method="inclusive")
    return float(qs[2] - qs[0])


def _within_bounds(
    current: float, proposed: float, max_movement_fraction: float
) -> bool:
    """Bounds-guard predicate: ``|Δ| ≤ max_fraction * max(|current|, ε)``.

    P1-3 — scale is the **current** threshold (not the larger of current /
    proposed). Anchoring on ``proposed`` lets a single noisy median sample
    accept arbitrarily large jumps (0.5 → 0.8 = 60 %, but bounded against
    0.8 it passes). Anchoring on current makes the predicate honest:
    «movement must stay within ``max_fraction`` of where we are today».
    ``_EPS`` keeps a zero-current threshold from accepting infinite drift.
    """
    delta = abs(proposed - current)
    scale = max(abs(current), _EPS)
    return delta <= max_movement_fraction * scale


def evaluate_threshold(
    *,
    metric: TunableMetric,
    samples: tuple[float, ...],
    current_value: float,
    min_samples: int = MIN_SAMPLES_FOR_TUNING,
    max_movement_fraction: float = MAX_MOVEMENT_FRACTION_DEFAULT,
) -> TuningProposal | None:
    """Compute a tuning proposal from a window of coverage samples.

    Returns ``None`` when ``len(samples) < min_samples`` — caller treats that
    as «not enough signal yet, keep current value». Otherwise returns a
    :class:`TuningProposal` whose ``within_bounds`` flag tells the caller
    whether the new value is safe to write (``True``) or must escalate to a
    human (``False``).
    """
    if len(samples) < min_samples:
        return None
    med = float(median(samples))
    proposed = min(1.0, max(0.0, med))
    return TuningProposal(
        metric=metric,
        current_value=current_value,
        proposed_value=proposed,
        samples=samples,
        median_value=med,
        iqr=_iqr(samples),
        within_bounds=_within_bounds(current_value, proposed, max_movement_fraction),
        max_movement_fraction=max_movement_fraction,
    )


def apply_proposals(
    gates: CodeReviewGates, proposals: tuple[TuningProposal, ...]
) -> tuple[CodeReviewGates, tuple[TuningProposal, ...]]:
    """Apply only the in-bounds proposals; return the updated gates + escalations.

    ``escalations`` is the subset of ``proposals`` whose ``within_bounds`` is
    ``False`` — caller emits one ``HUMAN_QUERY`` per escalation. The returned
    :class:`CodeReviewGates` has only the safe metrics updated; out-of-bounds
    metrics keep their pre-call value.
    """
    updates: dict[str, float] = {}
    escalations: list[TuningProposal] = []
    for prop in proposals:
        if prop.within_bounds:
            updates[prop.metric] = prop.proposed_value
        else:
            escalations.append(prop)
    if not updates:
        return gates, tuple(escalations)
    new_gates = gates.model_copy(update=updates)
    return new_gates, tuple(escalations)


def atomic_write_gates_yaml(gates: CodeReviewGates, path: Path) -> None:
    """Write ``code-review-gates.yaml`` atomically (tempfile + os.replace).

    Preserves the schema documented in the on-disk default (top-level keys
    ``p0_threshold``, ``test_coverage_threshold``, ``compliance_tags``,
    ``sweep_every_stories``). The parent directory must already exist —
    callers (live_tuning_subscriber) point at ``skills/policy/`` which is
    created at bootstrap.

    Atomicity guarantees:

    * ``NamedTemporaryFile`` writes the payload to the SAME directory as the
      target (so ``os.replace`` is a same-filesystem rename = atomic on POSIX).
    * ``fsync`` flushes the tempfile before the rename, so a crash mid-write
      leaves either the old file or the new file — never a half-written file.
    * On exception during write, the tempfile is removed; the target stays
      at its pre-call content.
    """
    parent = path.parent
    if not parent.exists():
        raise FileNotFoundError(f"policy directory does not exist: {parent}")

    payload = {
        "p0_threshold": float(gates.p0_threshold),
        "test_coverage_threshold": float(gates.test_coverage_threshold),
        "compliance_tags": list(gates.compliance_tags),
        "sweep_every_stories": int(gates.sweep_every_stories),
    }
    serialised = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)

    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            fh.write(serialised)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


__all__ = [
    "MAX_MOVEMENT_FRACTION_DEFAULT",
    "MIN_SAMPLES_FOR_TUNING",
    "TunableMetric",
    "TuningProposal",
    "apply_proposals",
    "atomic_write_gates_yaml",
    "evaluate_threshold",
]
