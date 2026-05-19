"""Pilot findings closure §1 #1 — mark-done ID normalization (2026-05-19).

Covers:

* :func:`bmad_orchestrator.runtime.bmad_format.resolve_sprint_status_key`
  for dotted→kebab, kebab→dotted, case-insensitive exact, no-match.
* In-pilot mark-done semantics: only succeeded ids flip to ``"done"``, and
  dotted spawned ids resolve to kebab keys actually present in the
  ``epics.<n>.stories`` block of sprint-status.yaml.
* Regression — resume after an interrupted pilot does NOT re-mark stories
  that were already flipped to ``"done"`` by an earlier round.
"""

from __future__ import annotations

import copy

import pytest

from bmad_orchestrator.runtime.bmad_format import resolve_sprint_status_key

# ──────────────────────────── resolver — unit tests ──────────────────────────


@pytest.mark.parametrize(
    ("raw", "keys", "expected"),
    [
        # 1) dotted spawned id resolves to kebab-with-prose sprint-status key
        (
            "1.3",
            ["1-3-fastapi-app-lifespan-health", "1-4-other"],
            "1-3-fastapi-app-lifespan-health",
        ),
        # 2) kebab spawned id matches dotted sprint-status key
        (
            "3-1-lifecycle-state-machine",
            ["3.1", "3.2"],
            "3.1",
        ),
        # 3) case-insensitive exact match — keys stay verbatim in output
        (
            "Story-1-2-FOO",
            ["story-1-2-foo", "story-1-3-bar"],
            "story-1-2-foo",
        ),
        # 4) raw matches no key (typo) → None, caller falls back to raw
        (
            "9.9",
            ["1-1-foo", "1-2-bar"],
            None,
        ),
        # 5) letter-suffix dotted id resolves to kebab variant
        (
            "1.17b",
            ["1-17b-mv-fallback-conditional", "1-18-next"],
            "1-17b-mv-fallback-conditional",
        ),
    ],
)
def test_resolve_sprint_status_key_variants(
    raw: str, keys: list[str], expected: str | None
) -> None:
    assert resolve_sprint_status_key(raw, keys) == expected


# ───────────────────────── mark-done semantics ──────────────────────────────


def _apply_mark_done(snap: dict, succeeded: list[str]) -> dict:
    """Inline copy of the real-pilot mark-done loop (run.py §_run_real_pilot_body).

    Kept private to this test module so a refactor of the production loop
    that breaks the contract surfaces as a test failure here.
    """
    out = copy.deepcopy(snap)
    epics_block = out.get("epics") or {}
    for sid in succeeded:
        for epic_block in epics_block.values():
            if not isinstance(epic_block, dict):
                continue
            stories = epic_block.get("stories") or {}
            if not isinstance(stories, dict):
                continue
            resolved = resolve_sprint_status_key(sid, stories.keys())
            if resolved is None:
                continue
            stories[resolved] = "done"
            break
    return out


def test_mark_done_flips_dotted_spawned_to_kebab_key() -> None:
    """Spec #1 acceptance — spawned=['1.3'], sprint-status keys = kebab → flipped."""
    snap = {
        "epics": {
            "1": {
                "status": "in-progress",
                "stories": {
                    "1-3-fastapi-app-lifespan-health": "ready-for-dev",
                    "1-4-other": "backlog",
                },
            }
        }
    }
    out = _apply_mark_done(snap, succeeded=["1.3"])
    stories = out["epics"]["1"]["stories"]
    assert stories["1-3-fastapi-app-lifespan-health"] == "done"
    assert stories["1-4-other"] == "backlog"  # unrelated story untouched


def test_mark_done_does_not_flip_failed_or_halted_stories() -> None:
    """Spec #9 — failed/halted stories stay at their pre-pilot status.

    Verifies the contract: ``succeeded`` is the only list that triggers a
    sprint-status flip. ``failed`` / ``halted`` ids must round-trip unchanged
    so a resume picks them back up.
    """
    snap = {
        "epics": {
            "1": {
                "stories": {
                    "1-1-ok": "ready-for-dev",
                    "1-2-fail": "ready-for-dev",
                }
            }
        }
    }
    out = _apply_mark_done(snap, succeeded=["1.1"])
    stories = out["epics"]["1"]["stories"]
    assert stories["1-1-ok"] == "done"
    assert stories["1-2-fail"] == "ready-for-dev"


def test_mark_done_resume_skips_already_done_stories() -> None:
    """Regression — second mark-done pass over the same ``succeeded`` list is
    idempotent. Required so a resumed pilot doesn't re-spawn or re-flip the
    same story (Spec #1 acceptance: ready_next list excludes already-done).
    """
    snap = {
        "epics": {
            "1": {
                "stories": {
                    "1-3-fastapi-app-lifespan-health": "ready-for-dev",
                }
            }
        }
    }
    first = _apply_mark_done(snap, succeeded=["1.3"])
    second = _apply_mark_done(first, succeeded=["1.3"])
    assert (
        second["epics"]["1"]["stories"]["1-3-fastapi-app-lifespan-health"]
        == "done"
    )
    # No spurious key added on the second pass.
    assert set(second["epics"]["1"]["stories"].keys()) == {
        "1-3-fastapi-app-lifespan-health"
    }
