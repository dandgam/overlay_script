"""NEW-26 — the injected review skill must be headless and verdict-emitting.

pilot run #7 / replay 1.5 (2026-05-20), after NEW-24 (network) + NEW-25 (skill
injection): the review spawn finally ran for real, but ``verdict=error`` stayed.

Root cause: the *upstream* ``bmad-code-review`` skill is interactive — its step
files HALT at numbered-choice checkpoints and it never emits a machine-readable
verdict. Headless ``claude -p`` has nothing to answer the prompts → exit 0 with
no ``verdict:`` line → ``verdict=error`` every run (only the runner-log fallback
rescued it).

Fix: ``_ensure_review_skill_in_worktree`` injects the headless variant
(``skills/headless/bmad-code-review``) — a self-contained SKILL.md with no
HALTs that ends with a ``VERDICT:`` line matching ``_VERDICT_LINE_RE``.
"""

from __future__ import annotations

from pathlib import Path

from bmad_orchestrator.agent.run import (
    _ensure_review_skill_in_worktree,
    _verdict_from_text,
)


def _injected_skill_md(tmp_path: Path) -> str:
    worktree = tmp_path / "wt-1.5"
    worktree.mkdir()
    _ensure_review_skill_in_worktree(str(worktree))
    skill_md = worktree / ".claude" / "skills" / "bmad-code-review" / "SKILL.md"
    assert skill_md.is_file(), "bmad-code-review/SKILL.md must be present"
    return skill_md.read_text(encoding="utf-8")


def test_headless_skill_source_exists() -> None:
    """The headless skill the fix copies from must ship in the repo."""
    package_root = Path(__file__).resolve().parents[1]
    src = package_root / "skills" / "headless" / "bmad-code-review"
    assert src.is_dir(), f"headless skill missing: {src}"
    assert (src / "SKILL.md").is_file()


def test_injected_skill_has_no_interactive_halts(tmp_path: Path) -> None:
    """The injected skill must not carry the upstream interactive checkpoints."""
    body = _injected_skill_md(tmp_path).lower()
    # The interactive upstream skill is built around "**HALT** — I am waiting
    # for your numbered choice". None of those checkpoint markers may survive
    # in the headless variant.
    assert "**halt**" not in body
    assert "i am waiting for your" not in body
    assert "reply with only the number" not in body
    assert "wait for user confirmation" not in body
    # And it must positively forbid halting.
    assert "never" in body and "halt" in body, (
        "headless skill must explicitly forbid halting"
    )


def test_injected_skill_mandates_a_verdict_line(tmp_path: Path) -> None:
    """The skill must instruct emitting a verdict line the orchestrator parses."""
    body = _injected_skill_md(tmp_path)
    assert "VERDICT:" in body, "headless skill must mandate a VERDICT: line"
    # Every verdict form the skill tells the reviewer to emit must be parseable
    # by the orchestrator's verdict regex.
    for verdict in ("approve", "request_changes", "reject"):
        line = f"VERDICT: {verdict}"
        assert _verdict_from_text(line) == verdict, (
            f"orchestrator must parse {line!r} emitted by the headless skill"
        )


def test_injection_is_idempotent(tmp_path: Path) -> None:
    """A second call must not raise or clobber the headless skill."""
    worktree = tmp_path / "wt-1.5"
    worktree.mkdir()
    _ensure_review_skill_in_worktree(str(worktree))
    _ensure_review_skill_in_worktree(str(worktree))
    skill_dir = worktree / ".claude" / "skills" / "bmad-code-review"
    assert skill_dir.is_dir()
