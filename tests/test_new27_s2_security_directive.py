"""NEW-27 (S2) — the security-review spawn must be a headless directive.

Like NEW-26 for code review: a ``/bmad-security-review`` slash command resolves
by walking up the worker's directory tree and can pick up the *target
project's* skill. The spawn now passes a self-contained directive prompt
(``SECURITY_REVIEW_DIRECTIVE``) straight to ``claude -p`` — no skill resolution
— and the directive mandates a ``VERDICT:`` line the gate parses.
"""

from __future__ import annotations

from bmad_orchestrator.runtime.security_review import (
    SECURITY_REVIEW_DIRECTIVE,
    VERDICT_APPROVE,
    VERDICT_BLOCK,
    VERDICT_MERGE_WITH_FIXES,
    parse_security_verdict,
)


def test_directive_is_not_a_slash_command() -> None:
    """The security spawn must pass a directive prompt, not a ``/skill``."""
    assert not SECURITY_REVIEW_DIRECTIVE.lstrip().startswith("/")
    assert "bmad-security-review" not in SECURITY_REVIEW_DIRECTIVE


def test_directive_forbids_interactive_halts() -> None:
    """The directive must explicitly forbid halting / asking the operator."""
    assert "NEVER halt" in SECURITY_REVIEW_DIRECTIVE
    assert "NEVER ask a question" in SECURITY_REVIEW_DIRECTIVE
    assert "no human present" in SECURITY_REVIEW_DIRECTIVE.lower()


def test_directive_verdict_lines_parse() -> None:
    """Every verdict the directive emits must be parseable by the gate."""
    assert parse_security_verdict("VERDICT: APPROVE") == VERDICT_APPROVE
    assert parse_security_verdict("VERDICT: MERGE WITH FIXES") == VERDICT_MERGE_WITH_FIXES
    assert parse_security_verdict("VERDICT: BLOCK") == VERDICT_BLOCK
    # Each verdict form is spelled out verbatim in the directive text.
    for form in ("VERDICT: APPROVE", "VERDICT: MERGE WITH FIXES", "VERDICT: BLOCK"):
        assert f"`{form}`" in SECURITY_REVIEW_DIRECTIVE


def test_directive_covers_the_four_hunter_angles() -> None:
    """The directive is self-contained — injection, auth, crypto, data-leak."""
    body = SECURITY_REVIEW_DIRECTIVE.lower()
    for angle in ("injection", "auth bypass", "crypto", "data leak"):
        assert angle in body
