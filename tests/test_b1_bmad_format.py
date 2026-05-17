"""B1 acceptance — BMad-format sprint-status parser (spec_dag_planner_bmad_compat §4).

Covers:
- ``normalize_story_id`` for kebab, dotted, prefixed, letter-suffix, malformed.
- ``extract_status_token`` for embedded comments, multi-token, empty/None.
- ``parse_sprint_status_bmad`` for: legacy schema, upstream BMad wrapped in
  ``development_status``, bare flat BMad, mixed unknown statuses, real Odyssey
  snapshot subset, empty / malformed input.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from bmad_orchestrator.runtime.bmad_format import (
    KNOWN_STATUSES,
    extract_status_token,
    normalize_story_id,
    parse_sprint_status_bmad,
)

# ──────────────────────────── normalize_story_id ────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("3.1", "3.1"),
        ("1.17b", "1.17b"),
        ("3-1-lifecycle-state-machine", "3.1"),
        ("1-17b-mv-fallback-conditional", "1.17b"),
        ("story-0-0-legal-consultation-152-187-fz", "0.0"),
        ("1-10a-redis-stream-eventbus-trait", "1.10a"),
        ("  3-2  ", "3.2"),
        ("epic-2-retrospective", "epic-2-retrospective"),  # no dotted form
        ("", ""),
    ],
)
def test_normalize_story_id_variants(raw: str, expected: str) -> None:
    assert normalize_story_id(raw) == expected


# ─────────────────────────── extract_status_token ───────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("done  # 2026-05-17: manual override after review", "done"),
        ("in-progress NEEDS-FIX via bmad-code-review", "in-progress"),
        ("ready-for-dev", "ready-for-dev"),
        ("  done  ", "done"),
        ("#all comment", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_extract_status_token_variants(raw: Any, expected: str) -> None:
    assert extract_status_token(raw) == expected


# ───────────────────────── parse_sprint_status_bmad ─────────────────────────


def test_parse_legacy_nested_schema_roundtrips() -> None:
    """Legacy ``epics: {N: {status, stories}}`` → unified shape with normalized
    ids + canonical status tokens.
    """
    legacy = {
        "epics": {
            3: {
                "status": "in-progress",
                "stories": {
                    "3.1": "done  # manual override",
                    "3-2-dpa-click-accept": "done",
                    "3.3": "ready-for-dev",
                },
            },
            "4": {"status": "backlog", "stories": {"4.1": "backlog"}},
        }
    }
    result = parse_sprint_status_bmad(legacy)
    assert set(result["epics"].keys()) == {"3", "4"}
    epic3 = result["epics"]["3"]
    assert epic3["status"] == "in-progress"
    assert epic3["stories"]["3.1"] == "done"
    assert epic3["stories"]["3.2"] == "done"  # kebab story-id normalized
    assert epic3["stories"]["3.3"] == "ready-for-dev"
    assert result["epics"]["4"]["stories"]["4.1"] == "backlog"


def test_parse_bmad_upstream_development_status_wrapper() -> None:
    """Real Odyssey shape: top-level ``development_status:`` wrapping flat keys."""
    upstream = {
        "generated": "2026-05-13",
        "project": "Odyssey",
        "development_status": {
            "epic-1": "done",
            "1-1-rust-workspace-scaffold": "done",
            "1-17b-mv-fallback-conditional": "deferred",
            "epic-1-retrospective": "done",
            "epic-3": "in-progress",
            "3-1-lifecycle-state-machine": "done  # 2026-05-17: manual override",
            "3-2-dpa-click-accept-v1": "done",
            "3-3-hard-block-trigger-enforcement": "backlog",
        },
    }
    result = parse_sprint_status_bmad(upstream)
    epics = result["epics"]
    assert epics["1"]["status"] == "done"
    assert epics["1"]["stories"]["1.1"] == "done"
    assert epics["1"]["stories"]["1.17b"] == "deferred"
    assert epics["3"]["status"] == "in-progress"
    assert epics["3"]["stories"]["3.1"] == "done"
    assert epics["3"]["stories"]["3.3"] == "backlog"
    # Retrospective entries don't appear as stories or as separate epics.
    assert all("retrospective" not in sid for sid in epics["1"]["stories"])


def test_parse_bmad_bare_flat_root() -> None:
    """Top-level flat keys (no ``development_status:`` wrapper) still parse."""
    bare = {
        "epic-2": "backlog",
        "2-1-foo-bar-baz": "ready-for-dev",
        "2-2": "in-progress",
    }
    result = parse_sprint_status_bmad(bare)
    assert result["epics"]["2"]["status"] == "backlog"
    assert result["epics"]["2"]["stories"]["2.1"] == "ready-for-dev"
    assert result["epics"]["2"]["stories"]["2.2"] == "in-progress"


def test_parse_unknown_status_falls_back_to_backlog(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unknown enum value → ``backlog`` + structured WARNING."""
    data = {
        "development_status": {
            "epic-5": "done",
            "5-1-novel-status": "wibbling",
            "5-2-something": "DONE",  # case-sensitive — also unknown
        }
    }
    with caplog.at_level(logging.WARNING, logger="bmad_orchestrator.runtime.bmad_format"):
        result = parse_sprint_status_bmad(data)
    stories = result["epics"]["5"]["stories"]
    assert stories["5.1"] == "backlog"
    assert stories["5.2"] == "backlog"
    assert any("unknown_status" in rec.message for rec in caplog.records)


def test_parse_in_progress_with_needs_fix_takes_first_token() -> None:
    """``in-progress NEEDS-FIX via bmad-code-review`` → ``in-progress``."""
    data = {
        "development_status": {
            "epic-3": "in-progress",
            "3-2": "in-progress NEEDS-FIX via bmad-code-review",
        }
    }
    result = parse_sprint_status_bmad(data)
    assert result["epics"]["3"]["stories"]["3.2"] == "in-progress"


def test_parse_empty_and_malformed_inputs_return_empty_shape() -> None:
    assert parse_sprint_status_bmad({}) == {"epics": {}}
    assert parse_sprint_status_bmad(None) == {"epics": {}}  # type: ignore[arg-type]
    assert parse_sprint_status_bmad("not a dict") == {"epics": {}}  # type: ignore[arg-type]
    # Only meta keys, no schema match → empty
    assert parse_sprint_status_bmad({"generated": "2026-05-13"}) == {"epics": {}}


def test_parse_legacy_with_non_dict_epic_value_ignored() -> None:
    """Malformed epic entry (string instead of dict) is skipped, not raised."""
    data = {"epics": {3: "in-progress", 4: {"status": "backlog", "stories": {}}}}
    result = parse_sprint_status_bmad(data)
    assert "3" not in result["epics"]
    assert result["epics"]["4"]["status"] == "backlog"


def test_known_statuses_covers_real_world_enum() -> None:
    """Sanity check: every status we observed in Odyssey snapshot is recognized."""
    observed = {
        "done",
        "in-progress",
        "ready-for-dev",
        "backlog",
        "deferred",
        "optional",
        "review",
    }
    assert observed <= KNOWN_STATUSES


def test_parse_odyssey_snapshot_subset_yields_epic3_ready() -> None:
    """End-to-end: a realistic Odyssey subset must surface 3-3 as backlog (not 'ready'
    yet — backlog requires deps-done check downstream, but the parser must NOT
    drop it). Documents canonical shape used by DagPlanner."""
    subset = {
        "development_status": {
            "epic-3": "in-progress",
            "3-1-lifecycle-state-machine": "done",
            "3-2-dpa-click-accept-v1": "done",
            "3-3-hard-block-trigger-enforcement": "backlog",
            "epic-3-retrospective": "optional",
        }
    }
    result = parse_sprint_status_bmad(subset)
    epic3_stories = result["epics"]["3"]["stories"]
    assert set(epic3_stories.keys()) == {"3.1", "3.2", "3.3"}
    assert epic3_stories["3.3"] == "backlog"


def test_parse_preserves_letter_suffix_in_story_ids() -> None:
    """Letter-suffixed story ids (``1.10a``, ``1.17b``) survive normalization."""
    data = {
        "development_status": {
            "epic-1": "done",
            "1-10a-redis-stream-eventbus-trait": "done",
            "1-10b-outbox-relayer": "done",
            "1-15a-otel-tracing-prometheus": "done",
        }
    }
    result = parse_sprint_status_bmad(data)
    stories = result["epics"]["1"]["stories"]
    assert "1.10a" in stories
    assert "1.10b" in stories
    assert "1.15a" in stories


def test_parse_epic_only_with_no_stories_returns_empty_dict() -> None:
    """An epic-N entry without any matching stories yields an empty ``stories``
    dict so downstream consumers can iterate safely."""
    data = {"development_status": {"epic-99": "backlog"}}
    result = parse_sprint_status_bmad(data)
    assert result["epics"]["99"] == {"status": "backlog", "stories": {}}


def test_parse_story_without_prior_epic_creates_default_epic_entry() -> None:
    """If sprint-status omits ``epic-N:`` but lists ``N-M-...:``, the parser
    still groups stories under that epic with default ``backlog`` epic status.
    """
    data = {"development_status": {"7-1-foo": "ready-for-dev", "7-2-bar": "done"}}
    result = parse_sprint_status_bmad(data)
    assert result["epics"]["7"]["status"] == "backlog"
    assert result["epics"]["7"]["stories"]["7.1"] == "ready-for-dev"
    assert result["epics"]["7"]["stories"]["7.2"] == "done"
