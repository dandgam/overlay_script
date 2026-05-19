"""Honest pilot-outcome accounting (NEW-16).

Background — pilot_findings_closure_v6 spec §4:
    On real Antares story 1.5 the worker wrote code, never committed it, never
    reached the merge gate — yet the pilot summary still reported
    ``succeeded=1``. The ``succeeded`` metric lied: a worker exiting
    ``status=success`` only proves dev work *may* have landed on the feature
    branch, not that it reached ``integration/<wave>``.

This module reclassifies the worker-completed story list against the actual
merge events emitted by the post-dev pipeline:

* ``INTEGRATION_MERGE_COMPLETED`` — the feature branch was fast-forward-merged
  into integration. This — and only this — is a real ``succeeded``.
* ``INTEGRATION_MERGE_SKIPPED`` — the work never reached integration. The
  ``reason`` field splits it: ``no_commits`` → the worker produced nothing
  (``no_op``); ``ff_conflict`` / ``verdict_missing`` / anything else → the
  worker produced commits that failed to merge (``failed``).
* No merge event at all for a worker-completed story → ``failed`` (the work is
  stranded silently on the feature branch).

Kept as a pure function (no I/O, no event bus) so it unit-tests in isolation;
``agent.run._run_real_pilot_body`` feeds it the drained event list.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from bmad_orchestrator.runtime.event_loop import Event, EventType

# INTEGRATION_MERGE_SKIPPED reasons that mean "the worker produced no commits"
# — a no-op, distinct from a genuine merge failure of real work.
_NO_OP_SKIP_REASONS: frozenset[str] = frozenset({"no_commits"})


@dataclass(frozen=True, slots=True)
class PilotOutcomes:
    """Partition of worker-completed stories by real merge status.

    * ``merged`` — story landed in ``integration/<wave>`` → the honest
      ``succeeded`` metric.
    * ``no_op`` — worker exited success but produced zero commits — nothing to
      merge. Not a success, not really a failure of work either.
    * ``failed`` — worker produced commits (or claimed success) but the work
      never reached integration: merge conflict, missing verdict, or no merge
      event at all.
    """

    merged: tuple[str, ...]
    failed: tuple[str, ...]
    no_op: tuple[str, ...]

    @property
    def succeeded_count(self) -> int:
        """Honest ``succeeded`` — only stories actually merged to integration."""
        return len(self.merged)


def partition_pilot_outcomes(
    worker_succeeded: Iterable[str],
    events: Iterable[Event],
) -> PilotOutcomes:
    """Reclassify worker-completed stories by real integration-merge status.

    ``worker_succeeded`` — story ids that ``_tail_and_emit_completion`` tagged
    ``"completed"`` (worker exited ``status=success``). ``events`` — the events
    drained from the bus after the post-dev pipeline ran (WORKER_COMPLETED →
    code-review → merge gate → reconcile → merge).

    The classification is order-independent and idempotent: a story that has
    both a COMPLETED and a SKIPPED event (e.g. a retry) is treated as merged —
    a real fast-forward merge wins.
    """
    merged_ids: set[str] = set()
    skip_reason: dict[str, str] = {}
    for ev in events:
        payload = ev.payload or {}
        sid = str(payload.get("story_id") or "")
        if not sid:
            continue
        if ev.type == EventType.INTEGRATION_MERGE_COMPLETED:
            merged_ids.add(sid)
        elif ev.type == EventType.INTEGRATION_MERGE_SKIPPED:
            # Keep the first reason seen; a later COMPLETED still wins below.
            skip_reason.setdefault(sid, str(payload.get("reason") or ""))

    merged: list[str] = []
    failed: list[str] = []
    no_op: list[str] = []
    for sid in worker_succeeded:
        if sid in merged_ids:
            merged.append(sid)
        elif skip_reason.get(sid, "") in _NO_OP_SKIP_REASONS:
            no_op.append(sid)
        else:
            # Skipped with a real reason, or no merge event at all — the work
            # never reached integration. Stranded = failed (spec §4).
            failed.append(sid)

    return PilotOutcomes(
        merged=tuple(merged),
        failed=tuple(failed),
        no_op=tuple(no_op),
    )


__all__ = ["PilotOutcomes", "partition_pilot_outcomes"]
