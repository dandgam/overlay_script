"""Detector helpers for runner Stage 7 cleanup failures (#2 NEW-2 Layer B).

Background — pilot_findings_closure_v2 spec §1 #2:
    On real Antares Epic 1, 3/3 stories halted identically with
    ``error: cannot delete branch 'feature/1.X' used by worktree at '<path>'``.
    The ``bmad-auto-dev-runner.sh`` Stage 7 cleanup runs ``git branch -d`` on a
    feature branch that a reused/stale worktree still holds, git refuses, the
    runner exits non-zero — but the outer ``claude -p`` exits 0, so the
    orchestrator misreads it as a *silent failure* and discards real work that
    already passed Stage 4-6 + autofix.

Layer A (the runner-side graceful Stage 7 in ``bmad-auto-dev-runner.sh``) is the
primary fix. This module is **Layer B — orchestrator-side defence-in-depth**:
when the worker's stdout still carries the cleanup-failure line (e.g. an older
runner without the Layer A patch), :func:`detect_reused_worktree_cleanup_failure`
recognises it from the tailed ``stdout_line`` events, and
:func:`decide_cleanup_recovery` decides whether the orchestrator should recover
the work (feature branch has commits past base) or preserve the halt.

Kept as pure functions (no I/O, no event bus) so they unit-test in isolation;
``agent.run._tail_and_emit_completion`` wires them into the live tail loop.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

# git phrases the refusal as:
#   error: cannot delete branch 'feature/1.3' used by worktree at '/path/wt-1.3'
# Both the quoted and the rare unquoted form are tolerated; the branch name is
# captured when quoted so downstream events can name the affected branch.
_REUSED_WORKTREE_RE = re.compile(
    r"cannot delete branch\s+'(?P<branch>[^']+)'\s+used by worktree"
)
# Fallback: match the phrase even when the branch is not single-quoted.
_REUSED_WORKTREE_LOOSE_RE = re.compile(r"cannot delete branch .* used by worktree")


def is_reused_worktree_cleanup_line(line: str) -> bool:
    """True when a single stdout line is git's reused-worktree refusal."""
    return _REUSED_WORKTREE_LOOSE_RE.search(line) is not None


def detect_reused_worktree_cleanup_failure(
    stdout_lines: Iterable[str],
) -> str | None:
    """Scan tailed stdout lines for the runner Stage 7 cleanup failure.

    Returns the affected feature branch name (e.g. ``feature/1.3``) when the
    branch is quoted in the message, the literal ``"<unknown>"`` when the phrase
    matched but no quoted branch could be extracted, or ``None`` when no line
    matches. The first match wins — a runner emits the refusal at most once.
    """
    for line in stdout_lines:
        if not isinstance(line, str):
            continue
        m = _REUSED_WORKTREE_RE.search(line)
        if m is not None:
            return m.group("branch")
        if _REUSED_WORKTREE_LOOSE_RE.search(line) is not None:
            return "<unknown>"
    return None


@dataclass(frozen=True, slots=True)
class CleanupRecoveryDecision:
    """Outcome of :func:`decide_cleanup_recovery`.

    * ``recover`` — emit a synthetic ``CODE_REVIEW_VERDICT(verdict=approve)`` so
      the merge subscriber picks up the work the runner already produced.
    * ``not recover`` — the feature branch carries no commits past base; the
      orchestrator preserves the existing halt behaviour.
    """

    recover: bool
    commits: int
    reason: str


def decide_cleanup_recovery(commit_count: int) -> CleanupRecoveryDecision:
    """Decide recovery vs. halt given the feature branch's commit count.

    ``commit_count`` is the number of commits on the worker's branch past its
    base SHA (caller computes it via ``git rev-list --count base..HEAD``).
    """
    n = max(0, int(commit_count))
    if n > 0:
        return CleanupRecoveryDecision(
            recover=True,
            commits=n,
            reason="runner_cleanup_recovery",
        )
    return CleanupRecoveryDecision(
        recover=False,
        commits=0,
        reason="no_commits_preserve_halt",
    )


__all__ = [
    "CleanupRecoveryDecision",
    "decide_cleanup_recovery",
    "detect_reused_worktree_cleanup_failure",
    "is_reused_worktree_cleanup_line",
]
