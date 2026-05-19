"""NEW-3 — `resolve_sprint_status_key` bridges dotted ids to kebab+slug composite keys.

Real-pilot log from Antares 1a:
  ``pilot_mark_done_unresolved reason='no matching sprint-status key' spawned_id=1.3``
while sprint-status carried the key ``1-3-fastapi-app-lifespan-health``.

A spawned id ``"1.3"`` must resolve to a composite key ``"1-3-<descriptive-slug>"``
deterministically, with a tie-break warning when several keys collide.
"""

from __future__ import annotations

import logging

from bmad_orchestrator.runtime.bmad_format import resolve_sprint_status_key

# ── unit ─────────────────────────────────────────────────────────────────────


def test_composite_single_match() -> None:
    """Dotted id resolves to the kebab+slug composite key."""
    keys = ["1-3-fastapi-app-lifespan-health", "1-4-startup-checks"]
    assert resolve_sprint_status_key("1.3", keys) == "1-3-fastapi-app-lifespan-health"


def test_composite_multiple_matches_tie_break_logged(
    caplog: object,
) -> None:
    """Several keys reducing to the same dotted id → warn + return first lexically."""
    keys = ["1-3-zebra-task", "1-3-alpha-task"]
    with caplog.at_level(logging.WARNING):  # type: ignore[attr-defined]
        resolved = resolve_sprint_status_key("1.3", keys)
    assert resolved == "1-3-alpha-task"  # lexicographically first → deterministic
    assert "sprint_status_key_ambiguous" in caplog.text  # type: ignore[attr-defined]


def test_no_match_returns_none() -> None:
    """A spawned id with no composite key in sprint-status falls back to None."""
    keys = ["2-1-other-epic", "3-4-unrelated"]
    assert resolve_sprint_status_key("1.3", keys) is None


def test_exact_match_short_circuits_before_normalization() -> None:
    """An already-matching key is returned verbatim (case-insensitive)."""
    keys = ["1-3-fastapi-app", "1.3"]
    assert resolve_sprint_status_key("1.3", keys) == "1.3"


# ── integration — nested sprint-status mark-done loop ────────────────────────


def test_mark_done_loop_with_composite_keys() -> None:
    """Mimic run.py mark-done: resolve a spawned id inside a nested epics block.

    Mirrors `_run_real_pilot` lines ~1349-1360 — iterate epic blocks, resolve
    the spawned id to a real sprint-status key, write ``done``.
    """
    snap = {
        "epics": {
            "epic-1": {
                "stories": {
                    "1-3-fastapi-app-lifespan-health": "ready-for-dev",
                    "1-4-startup-checks": "ready-for-dev",
                },
            },
        },
    }
    spawned_id = "1.3"

    resolved_key = None
    for epic_block in snap["epics"].values():
        stories = epic_block["stories"]
        resolved = resolve_sprint_status_key(spawned_id, stories.keys())
        if resolved is None:
            continue
        stories[resolved] = "done"
        resolved_key = resolved
        break

    assert resolved_key == "1-3-fastapi-app-lifespan-health"
    epic1_stories = snap["epics"]["epic-1"]["stories"]
    assert epic1_stories["1-3-fastapi-app-lifespan-health"] == "done"
    # The sibling story is untouched — only the resolved key flips.
    assert epic1_stories["1-4-startup-checks"] == "ready-for-dev"
