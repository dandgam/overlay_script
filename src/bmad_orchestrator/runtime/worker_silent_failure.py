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

# #4 NEW-4 — the outer ``claude -p`` wrapper prints the inner runner's exit
# code as a plain stdout line, optionally prefixed by the ``❯`` shell glyph:
#   Exit code: 1
#   ❯ Exit code: 1
# The line is matched after stripping surrounding whitespace.
_INNER_EXIT_RE = re.compile(r"^(?:❯\s*)?Exit code:\s*(\d+)$")


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


def parse_inner_exit_code(stdout_lines: Iterable[str]) -> int | None:
    """Scan tailed stdout lines for the runner's ``Exit code: N`` marker.

    #4 NEW-4 — the outer ``claude -p`` process can exit 0 while the inner
    ``bmad-auto-dev-runner.sh`` exited non-zero; the wrapper dutifully echoes
    the inner code to stdout. The orchestrator parses it here so a non-zero
    inner code can override a misleading outer-0 ``worker_completed`` success.

    Returns the **last** matching code (the runner logs ``Exit code:`` once near
    the end of its run; the last wins should a nested wrapper echo more than
    one) or ``None`` when no line matches — in which case the caller preserves
    the legacy outer-exit-only behaviour.
    """
    result: int | None = None
    for line in stdout_lines:
        if not isinstance(line, str):
            continue
        m = _INNER_EXIT_RE.match(line.strip())
        if m is not None:
            result = int(m.group(1))
    return result


__all__ = [
    "CleanupRecoveryDecision",
    "decide_cleanup_recovery",
    "detect_reused_worktree_cleanup_failure",
    "is_reused_worktree_cleanup_line",
    "parse_inner_exit_code",
]
