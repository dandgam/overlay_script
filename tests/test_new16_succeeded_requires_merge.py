"""NEW-16 — the ``succeeded`` metric requires a real integration merge.

pilot_findings_closure_v6 spec §4: on real Antares story 1.5 the worker wrote
code, never committed it, never reached the merge gate — yet the pilot summary
reported ``succeeded=1``. A worker exiting ``status=success`` only proves dev
work *may* have landed on the feature branch, not that it reached
``integration/<wave>``.

:func:`partition_pilot_outcomes` reclassifies the worker-completed story list
against the actual merge events:

* ``INTEGRATION_MERGE_COMPLETED`` → ``merged`` (the honest ``succeeded``).
* ``INTEGRATION_MERGE_SKIPPED`` reason ``no_commits`` → ``no_op``.
* ``INTEGRATION_MERGE_SKIPPED`` other reason / no merge event → ``failed``.

Coverage: 4 unit (one per branch + idempotent merge-wins) + 1 integration
(mixed pilot-summary scenario — 1 merged + 1 no-commit → ``succeeded=1``).
"""

from __future__ import annotations

from bmad_orchestrator.runtime.event_loop import Event, EventType
from bmad_orchestrator.runtime.pilot_outcomes import (
    PilotOutcomes,
    partition_pilot_outcomes,
)


def _merge_completed(story_id: str) -> Event:
    return Event(
        type=EventType.INTEGRATION_MERGE_COMPLETED,
        payload={"story_id": story_id, "sha": "abc123"},
    )


def _merge_skipped(story_id: str, reason: str) -> Event:
    return Event(
        type=EventType.INTEGRATION_MERGE_SKIPPED,
        payload={"story_id": story_id, "reason": reason},
    )


# ── unit ────────────────────────────────────────────────────────────────────


def test_merged_story_counts_as_succeeded() -> None:
    """A story with INTEGRATION_MERGE_COMPLETED → merged → succeeded."""
    outcomes = partition_pilot_outcomes(["1.3"], [_merge_completed("1.3")])
    assert outcomes.merged == ("1.3",)
    assert outcomes.succeeded_count == 1
    assert outcomes.failed == ()
    assert outcomes.no_op == ()


def test_committed_but_not_merged_is_failed() -> None:
    """Worker succeeded, commits exist, but the ff-merge hit a conflict —
    the work is stranded on the feature branch → failed, NOT succeeded."""
    outcomes = partition_pilot_outcomes(
        ["1.4"], [_merge_skipped("1.4", "ff_conflict")]
    )
    assert outcomes.failed == ("1.4",)
    assert outcomes.succeeded_count == 0
    assert outcomes.no_op == ()


def test_no_commits_skip_is_no_op() -> None:
    """Worker exited success but produced zero commits → no_op (not
    succeeded, not a failure of real work)."""
    outcomes = partition_pilot_outcomes(
        ["1.5"], [_merge_skipped("1.5", "no_commits")]
    )
    assert outcomes.no_op == ("1.5",)
    assert outcomes.succeeded_count == 0
    assert outcomes.failed == ()


def test_no_merge_event_at_all_is_failed() -> None:
    """A worker-completed story with NO merge event is stranded silently —
    treated as failed, never as succeeded. A later COMPLETED still wins
    (idempotent merge-wins) when both events are present."""
    outcomes = partition_pilot_outcomes(["1.6"], [])
    assert outcomes.failed == ("1.6",)
    assert outcomes.succeeded_count == 0

    # merge-wins — both a skip and a completed for the same story → merged.
    both = partition_pilot_outcomes(
        ["1.7"], [_merge_skipped("1.7", "ff_conflict"), _merge_completed("1.7")]
    )
    assert both.merged == ("1.7",)


# ── integration — mixed pilot-summary scenario ──────────────────────────────


def test_pilot_summary_one_merged_one_no_commit() -> None:
    """Mock pilot: 2 workers exited success, only 1 actually merged.

    Spec §4 acceptance: ``succeeded`` reflects only really-merged stories —
    the summary must report ``succeeded=1``, NOT ``succeeded=2``.
    """
    worker_succeeded = ["2.1", "2.2"]
    # The post-dev pipeline drained these events: 2.1 merged cleanly, 2.2
    # wrote nothing and was skipped.
    events = [
        Event(type=EventType.WORKER_COMPLETED, payload={"story_id": "2.1"}),
        Event(type=EventType.WORKER_COMPLETED, payload={"story_id": "2.2"}),
        _merge_completed("2.1"),
        _merge_skipped("2.2", "no_commits"),
    ]
    outcomes = partition_pilot_outcomes(worker_succeeded, events)

    assert isinstance(outcomes, PilotOutcomes)
    # honest succeeded — exactly one, not the two worker-success exits.
    assert outcomes.succeeded_count == 1
    assert outcomes.merged == ("2.1",)
    assert outcomes.no_op == ("2.2",)
    # 2.2 is NOT counted as a success.
    assert "2.2" not in outcomes.merged
    # total accounted == total worker-succeeded (nothing dropped).
    accounted = len(outcomes.merged) + len(outcomes.failed) + len(outcomes.no_op)
    assert accounted == len(worker_succeeded)
