"""NEW-6 — ``parse_inner_exit_code`` accepts both runner exit-code formats.

spec_pilot_findings_closure_v3 §2 #4: the runner emits the inner exit code in
two shapes — ``Exit code: N`` (claude-p tail) and ``EXIT_CODE=N``
(bmad-auto-dev-runner.sh). The old ``_INNER_EXIT_RE`` matched only the first;
in the validation replay worker 1.4 printed ``EXIT_CODE=2`` and the miss let a
real failure surface as ``worker_completed status=success``.
"""

from __future__ import annotations

from bmad_orchestrator.runtime.worker_silent_failure import parse_inner_exit_code


def test_parse_exit_code_equals_format() -> None:
    """``EXIT_CODE=2`` (bmad-auto-dev-runner.sh form) → 2."""
    assert parse_inner_exit_code(["EXIT_CODE=2"]) == 2


def test_parse_both_formats_in_tail_returns_last() -> None:
    """Both formats present → last match wins (existing last-match semantics)."""
    tail = ["Exit code: 0", "noise", "EXIT_CODE=3"]
    assert parse_inner_exit_code(tail) == 3
