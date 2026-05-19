"""NEW-26 — the review spawn must be headless and verdict-emitting.

pilot run #7 / replay 1.5 (2026-05-20), after NEW-24 (network) + NEW-25 (skill
injection): the review spawn finally ran for real, but ``verdict=error`` stayed.

Root cause chain — all from spawning a *skill* into a headless ``claude -p``
reviewer. The ``/bmad-code-review`` slash resolved (NEW-25) to the target
project's own ``.claude/skills/bmad-code-review``, which is written for an
interactive operator: its step files HALT at numbered-choice checkpoints and it
never prints a machine-readable verdict. Headless, nothing answers the prompts
→ exit 0 with no ``verdict:`` line → ``verdict=error`` every run.

Root fix: drop the slash command entirely. The review spawn passes a
self-contained headless directive prompt (``CODE_REVIEW_DIRECTIVE``) straight to
``claude -p`` — no skill resolution, no worktree injection, no HALTs — and the
directive mandates a final ``VERDICT:`` line that ``_VERDICT_LINE_RE`` parses.
"""

from __future__ import annotations

from bmad_orchestrator.agent.run import (
    CODE_REVIEW_DIRECTIVE,
    CODE_REVIEW_VERDICTS,
    _verdict_from_text,
)


def test_directive_is_not_a_slash_command() -> None:
    """The review spawn must pass a directive prompt, not a ``/skill``.

    A slash command resolves to whatever skill the worktree/target project
    happens to carry — the exact NEW-26 trap.
    """
    assert not CODE_REVIEW_DIRECTIVE.lstrip().startswith("/")
    assert "bmad-code-review" not in CODE_REVIEW_DIRECTIVE


def test_directive_forbids_interactive_halts() -> None:
    """The directive must explicitly forbid halting / asking the operator."""
    assert "NEVER halt" in CODE_REVIEW_DIRECTIVE
    assert "NEVER ask a question" in CODE_REVIEW_DIRECTIVE
    assert "no human present" in CODE_REVIEW_DIRECTIVE.lower()


def test_directive_mandates_a_parseable_verdict_line() -> None:
    """Every verdict the directive tells the reviewer to emit must parse."""
    assert "VERDICT:" in CODE_REVIEW_DIRECTIVE
    for verdict in CODE_REVIEW_VERDICTS:
        line = f"VERDICT: {verdict}"
        assert _verdict_from_text(line) == verdict, (
            f"orchestrator must parse {line!r} emitted by the directive"
        )
        # The directive's own text must spell out this verdict form.
        assert f"`VERDICT: {verdict}`" in CODE_REVIEW_DIRECTIVE


def test_directive_describes_the_full_review_flow() -> None:
    """The directive is self-contained — diff, review, triage, verdict."""
    for marker in ("STEP 1", "STEP 2", "STEP 3", "STEP 4", "git diff"):
        assert marker in CODE_REVIEW_DIRECTIVE
