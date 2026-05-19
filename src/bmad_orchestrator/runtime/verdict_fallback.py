"""Fallback verdict parser — reads bmad-auto-dev runner's Stage 6 review log.

The bmad-auto-dev shell runner (``skills/upstream/bmad-auto-dev/scripts/
bmad-auto-dev-runner.sh``) writes the Stage 6 ``claude -p`` code-review
output to ``<worktree>/_bmad/auto-dev-state/reviews/<story_id>*.log``. The
runner derives ``review_verdict`` from the last ``PASS|NEEDS-FIX|BLOCKED``
token on the final ~5 lines of that log.

When the orchestrator's own merge-gate workers (spec / quality stages) fail
to surface a parseable ``verdict`` event in their JSONL stream, the runner's
own Stage 6 verdict is still a valid signal — code_review_subscriber falls
back to reading the runner log directly. Avoids stuck-in-error escalation
when the merge-gate worker's stdout doesn't carry a structured JSON verdict
line (observed in Antares pilots 1-7).

Mapping:
    PASS      → approve
    NEEDS-FIX → request_changes
    BLOCKED   → reject
    UNKNOWN / missing log / unparseable → None (caller stays with "error")

Selection rule when multiple log files match (e.g. story 1.2 produced
``1.2.log`` + ``1.2-retry-1.log`` + ``1.2-autofix.log``):
    1. Filter to files that actually contain a verdict token.
    2. Pick the one with the most recent mtime — that's the final answer
       for this story (post-autofix re-review wins over initial review).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

Verdict = Literal["approve", "request_changes", "reject"]

_VERDICT_TOKEN_RE = re.compile(r"\b(PASS|NEEDS-FIX|BLOCKED)\b")

_TOKEN_TO_VERDICT: dict[str, Verdict] = {
    "PASS": "approve",
    "NEEDS-FIX": "request_changes",
    "BLOCKED": "reject",
}


def _scan_log_for_verdict(path: Path) -> tuple[Verdict, str] | None:
    """Return ``(verdict, raw_token)`` from the final matching line, or None.

    The runner emits ``review_verdict="$(tail -n 5 ... | grep -Eo ... | tail -n 1)"``
    — i.e., the LAST token on the last few lines wins. We mirror that: scan
    the whole file, keep the last match.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    last_token: str | None = None
    for match in _VERDICT_TOKEN_RE.finditer(text):
        last_token = match.group(1)

    if last_token is None:
        return None

    return _TOKEN_TO_VERDICT[last_token], last_token


def parse_runner_review_log(
    worktree: str | Path, story_id: str
) -> tuple[Verdict, str] | None:
    """Read runner Stage 6 logs for ``story_id``, return ``(verdict, summary)``.

    Returns None when:
      * worktree path doesn't exist or has no ``_bmad/auto-dev-state/reviews/``
      * no log file matches the story's glob
      * none of the matching files contain a parseable verdict token

    Summary format: ``runner Stage 6 verdict=<TOKEN> (source=<filename>)``
    — terse, machine-readable, useful for HUMAN_QUERY escalation text.

    Multiple matching logs are resolved by mtime — the most recent file wins,
    so an autofix re-review supersedes the initial review.
    """
    reviews_dir = Path(worktree) / "_bmad" / "auto-dev-state" / "reviews"
    if not reviews_dir.is_dir():
        return None

    # Story IDs in the runner are kebab-cased; glob covers both bare-id files
    # (``<id>.log``, ``<id>-autofix.log``, ``<id>-retry-1.log``) and the
    # explicit stage6 naming used elsewhere in the runner.
    candidates: list[Path] = []
    for pattern in (f"{story_id}*.log", f"{story_id}-stage6*.log"):
        candidates.extend(reviews_dir.glob(pattern))

    # Deduplicate (glob patterns overlap) while keeping mtime sort stable.
    seen: set[Path] = set()
    unique: list[Path] = []
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        unique.append(c)

    if not unique:
        return None

    # Sort by mtime descending — most recent first.
    unique.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    for path in unique:
        scanned = _scan_log_for_verdict(path)
        if scanned is None:
            continue
        verdict, token = scanned
        summary = f"runner Stage 6 verdict={token} (source={path.name})"
        return verdict, summary

    return None


def read_runner_verdict(worktree: str | Path, story_id: str) -> Verdict | None:
    """Return just the runner Stage 6 verdict string for ``story_id``, or None.

    Thin reader over :func:`parse_runner_review_log` for callers that only need
    the verdict (``approve`` | ``request_changes`` | ``reject``) and not the
    human-readable summary — notably ``agent.run._tail_and_emit_completion``,
    which feeds the verdict into ``decide_worker_status`` (NEW-9: verdict is the
    source-of-truth for worker success, runner exit code is secondary).

    None when no parseable Stage 6 review log exists — the caller then falls
    back to exit-code logic.
    """
    result = parse_runner_review_log(worktree, story_id)
    if result is None:
        return None
    return result[0]


__all__ = ["Verdict", "parse_runner_review_log", "read_runner_verdict"]
